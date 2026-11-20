"""PresetCraftAppLicator.

Legge lo stile scelto in fotocamera (per esempio Positive Film sulle Ricoh GR) dalle foto di una libreria LightCraft e
dà a ciascuna il profilo "Camera Matching" che lo riproduce, così in LightCraft i colori restano quelli dello scatto.
"""

import datetime
import io
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image, ImageOps, ImageTk

import ai
import autostart
import camera_styles as cs
import i18n
import matcher
import updater
import watcher
from i18n import registry_value, t
from lightcraft import (BACKUPS_KEPT, DATA_DIR, DEFAULT_CLI, LOCAL, backup_root, delete_backups, in_scope, is_cli,
                        is_library, library_open_reason, list_backups, local_time, log, utc_text)
from ai_panel import AiChoice
from translations import DATE_FORMATS, LANGUAGES, TEXTS
from ui_theme import (LEVELS, MUTED, TEXT, DateField, Dialog, TimeField, attach_scrollbars, center_on, dark_title_bar,
                      dpi_aware, set_icon, setup_theme)
from version import APP_NAME, VERSION

SETTINGS = os.path.join(DATA_DIR, "settings.json")
LOG = os.path.join(DATA_DIR, "presetcraft.log")


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    # copia nella cartella di servizio sul NAS (per ripartire dopo una formattazione), senza bloccare la finestra
    snapshot = json.loads(json.dumps(data))
    threading.Thread(target=_copy_settings, args=(snapshot,), daemon=True).start()


def _copy_settings(data):
    try:
        import watcher
        watcher.save_settings_copy(data)
    except Exception:  # noqa: BLE001 — NAS non raggiungibile: la copia si rifà al prossimo giro del watcher
        pass


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            if unit == "B":
                return f"{n:.0f} {unit}"
            text = f"{n:.1f} {unit}"
            return text if i18n.LANG == "en" else text.replace(".", ",")
        n /= 1024


# ---------------------------------------------------------------- anteprime (la JPEG salvata dalla fotocamera)

PREVIEW_MAX = 1600


def _embedded_jpeg(data):
    """La JPEG incorporata più grande che Pillow sa leggere: è quella con lo stile scelto in fotocamera."""
    best, best_px, pos = None, 0, 0
    for _ in range(64):
        pos = data.find(b"\xff\xd8\xff", pos)
        if pos < 0:
            break
        try:
            im = Image.open(io.BytesIO(data[pos:]))
            if im.format == "JPEG" and im.mode in ("RGB", "L", "CMYK") and im.width * im.height > best_px:
                best, best_px = pos, im.width * im.height
        except Exception:  # noqa: BLE001 — un candidato non leggibile si salta
            pass
        pos += 3
    if best is None:
        return None
    im = Image.open(io.BytesIO(data[best:]))
    im.load()
    return im


def load_preview(path):
    with open(path, "rb") as f:
        data = f.read()
    im = _embedded_jpeg(data)
    if im is None:
        return None
    try:  # l'orientamento sta nell'intestazione TIFF del raw, non nella JPEG incorporata
        orientation = Image.open(io.BytesIO(data)).getexif().get(0x0112, 1)
    except Exception:  # noqa: BLE001
        orientation = 1
    im = {3: lambda i: i.rotate(180, expand=True), 6: lambda i: i.rotate(270, expand=True),
          8: lambda i: i.rotate(90, expand=True)}.get(orientation, lambda i: i)(im)
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((PREVIEW_MAX, PREVIEW_MAX), Image.LANCZOS)
    return im


# ---------------------------------------------------------------- elenco

# (id, testo, larghezza, si allarga)
COLUMNS = (("name", "col_file", 200, False), ("folder", "col_folder", 150, False), ("camera", "col_camera", 105, False),
           ("captured", "col_captured", 125, False), ("imported", "col_imported", 125, False),
           ("style", "col_style", 150, False), ("current", "col_current", 120, False),
           ("target", "col_target", 165, False), ("status", "col_status", 200, True))
COLUMN_IDS = [c[0] for c in COLUMNS]
# la fotocamera si legge già nell'anteprima: si aggiunge dal menu Colonne se la libreria ne ha più d'una
DEFAULT_COLUMNS = ["name", "captured", "style", "current", "target", "status"]

# colori tenui delle righe (testo, sfondo)
ROW_COLORS = {
    "apply": ("#dce8fb", "#253650"),  # blu: riceverà il profilo
    "ok": ("#dff2e5", "#24432f"),     # verde: ha già il profilo giusto
    "skip": ("#f5ecd2", "#4a3f20"),   # giallo: resta com'è per scelta
}
LEGEND = (("apply", "legend_apply"), ("ok", "legend_ok"), ("skip", "legend_skip"), ("none", "legend_none"))


def row_kind(item):
    if item["status"] == "ok":
        return "ok"
    if item["status"] == "phone":
        return "skip"
    if item["status"] in ("apply", "manual"):
        return "apply" if item["action"] == "apply" else "skip"
    return "none"


def status_text(item):
    s, a = item["status"], item["action"]
    if s == "apply":
        return t("st_apply") if a == "apply" else t("st_skipped")
    if s == "manual":
        return t("st_manual_forced") if a == "apply" else t("st_manual")
    return t("st_" + s)


def local_text(ts, fmt=None):
    """Un'ora di LightCraft (UTC) come data e ora del PC, nel formato della lingua."""
    local = local_time(ts)
    return local.strftime(fmt or DATE_FORMATS.get(i18n.LANG, "%d/%m/%Y") + " %H:%M") if local else ts


def target_text(item):
    if not item["target"]:
        return "—"
    return t("target_own", name=item["target"]) if item.get("own") else item["target"]


def style_text(item):
    if item["style_name"]:
        return item["style_name"]
    return t("style_unknown", code=item["style_code"]) if item["style_code"] is not None else "—"


def when_text(iso, with_time=True):
    try:
        when = datetime.datetime.fromisoformat((iso or "")[:19])
    except ValueError:
        return ""
    return when.strftime(DATE_FORMATS.get(i18n.LANG, "%d/%m/%Y") + ("  %H:%M" if with_time else ""))


# ---------------------------------------------------------------- impostazioni

class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.title(t("settings"))
        self.resizable(False, False)
        self.transient(app)
        set_icon(self)
        self.saved = False
        frame = ttk.Frame(self, padding=(24, 22, 24, 18))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=t("settings"), style="Title.TLabel").pack(anchor="w")

        ttk.Label(frame, text=t("lang_label").upper(), style="Muted.TLabel").pack(anchor="w", pady=(18, 4))
        self.lang_codes = ["auto", *LANGUAGES]
        self.lang = ttk.Combobox(frame, values=[t("lang_auto"), *LANGUAGES.values()], state="readonly", width=40)
        current = app.settings.get("language") or "auto"
        self.lang.current(self.lang_codes.index(current) if current in self.lang_codes else 0)
        self.lang.pack(anchor="w")

        ttk.Label(frame, text=t("cli_label").upper(), style="Muted.TLabel").pack(anchor="w", pady=(18, 4))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.cli = tk.StringVar(value=app.settings.get("cli") or DEFAULT_CLI)
        ttk.Entry(row, textvariable=self.cli, width=58).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text=t("btn_browse"), command=self.browse).pack(side="left", padx=(8, 0))
        ttk.Label(frame, text=t("cli_hint"), style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        ttk.Label(frame, text=t("profiles_label"), style="Muted.TLabel").pack(anchor="w", pady=(18, 4))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.profiles_dir = tk.StringVar(value=app.settings.get("profiles_dir") or "")
        ttk.Entry(row, textvariable=self.profiles_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text=t("btn_browse"), command=self.browse_profiles).pack(side="left", padx=(8, 0))
        ttk.Button(row, text=t("btn_clear"), command=lambda: self.profiles_dir.set("")).pack(side="left", padx=(8, 0))
        ttk.Label(frame, text=t("profiles_hint"), style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        ttk.Label(frame, text=t("backup_label").upper(), style="Muted.TLabel").pack(anchor="w", pady=(18, 4))
        self.backup_mode = tk.StringVar(value="custom" if app.settings.get("backup_dir") else "library")
        self.backup_dir = tk.StringVar(value=app.settings.get("backup_dir") or "")
        ttk.Radiobutton(frame, text=t("backup_next_to"), value="library", variable=self.backup_mode,
                        command=self.backup_mode_changed).pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(4, 0))
        ttk.Radiobutton(row, text=t("backup_custom"), value="custom", variable=self.backup_mode,
                        command=self.backup_mode_changed).pack(side="left")
        self.backup_entry = ttk.Entry(row, textvariable=self.backup_dir)
        self.backup_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.backup_browse = ttk.Button(row, text=t("btn_browse"), command=self.browse_backup_dir)
        self.backup_browse.pack(side="left", padx=(8, 0))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text=t("backup_kept"), style="Body.TLabel").pack(side="left")
        self.kept = tk.IntVar(value=app.settings.get("backups_kept", BACKUPS_KEPT))
        ttk.Spinbox(row, from_=1, to=50, textvariable=self.kept, width=5, state="readonly").pack(side="left",
                                                                                               padx=(10, 0))
        ttk.Label(frame, text=t("backup_hint"), style="Muted.TLabel", wraplength=560, justify="left").pack(
            anchor="w", pady=(4, 0))
        self.backup_mode_changed()

        ttk.Label(frame, text=t("updates_label").upper(), style="Muted.TLabel").pack(anchor="w", pady=(18, 4))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.auto_update = tk.BooleanVar(value=app.settings.get("check_updates", True))
        ttk.Checkbutton(row, text=t("upd_auto"), variable=self.auto_update).pack(side="left")
        ttk.Button(row, text=t("upd_check_now"), command=lambda: app.check_updates(manual=True, parent=self)).pack(
            side="right")
        ttk.Label(frame, text=t("upd_hint"), style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        ttk.Label(frame, text=f"{APP_NAME} · " + t("about", version=VERSION), style="Muted.TLabel").pack(
            anchor="w", pady=(18, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(buttons, text=t("wz_open"), command=self.open_wizard).pack(side="left")
        ttk.Button(buttons, text=t("btn_save"), style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(buttons, text=t("btn_cancel"), command=self.destroy).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self.destroy())

        center_on(self, app)
        dark_title_bar(self)
        self.deiconify()
        self.grab_set()

    def open_wizard(self):
        self.destroy()
        self.app.after(50, self.app.open_wizard)

    def browse(self):
        start = os.path.dirname(self.cli.get()) if os.path.isdir(os.path.dirname(self.cli.get())) else LOCAL
        path = filedialog.askopenfilename(parent=self, title=t("cli_pick"), initialdir=start,
                                          filetypes=[("lightcraft-cli.exe", "lightcraft-cli.exe")])
        if path:
            self.cli.set(os.path.normpath(path))

    def browse_profiles(self):
        start = self.profiles_dir.get() if os.path.isdir(self.profiles_dir.get()) else os.path.expanduser("~")
        path = filedialog.askdirectory(parent=self, title=t("profiles_pick"), initialdir=start)
        if path:
            self.profiles_dir.set(os.path.normpath(path))

    def backup_mode_changed(self):
        state = ["!disabled"] if self.backup_mode.get() == "custom" else ["disabled"]
        self.backup_entry.state(state)
        self.backup_browse.state(state)

    def browse_backup_dir(self):
        start = self.backup_dir.get() if os.path.isdir(self.backup_dir.get()) else os.path.expanduser("~")
        path = filedialog.askdirectory(parent=self, title=t("backups_pick"), initialdir=start)
        if path:
            self.backup_dir.set(os.path.normpath(path))

    def save(self):
        s = self.app.settings
        cli = self.cli.get().strip()
        # il percorso predefinito si accetta anche se LightCraft non c'è ancora: lo si chiede al momento di applicare
        if cli and os.path.normcase(cli) != os.path.normcase(DEFAULT_CLI) and not is_cli(cli):
            Dialog.ask(self, t("settings"), t("cli_bad"), cli, level="warn")
            return
        profiles_dir = self.profiles_dir.get().strip()
        if profiles_dir and not os.path.isdir(profiles_dir):
            Dialog.ask(self, t("settings"), t("profiles_dir_bad"), profiles_dir, level="warn")
            return
        backup_dir = self.backup_dir.get().strip() if self.backup_mode.get() == "custom" else ""
        if self.backup_mode.get() == "custom" and not os.path.isdir(backup_dir):
            Dialog.ask(self, t("settings"), t("backup_dir_bad"), backup_dir or None, level="warn")
            return
        for key, value in (("backup_dir", backup_dir), ("profiles_dir", profiles_dir)):
            if value:
                s[key] = os.path.normpath(value)
            else:
                s.pop(key, None)
        s["backups_kept"] = max(1, min(50, int(self.kept.get())))
        s["language"] = self.lang_codes[self.lang.current()]
        s["check_updates"] = bool(self.auto_update.get())
        if cli and os.path.normcase(cli) != os.path.normcase(DEFAULT_CLI):
            s["cli"] = cli
        else:
            s.pop("cli", None)
        save_settings(s)
        self.saved = True
        self.destroy()


class BackupsDialog(tk.Toplevel):
    """Elenco dei backup della libreria corrente, con apertura della cartella ed eliminazione."""

    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.lib = app.settings.get("library")
        self.title(t("backups_title"))
        self.geometry("760x510")
        self.minsize(620, 400)
        self.transient(app)
        set_icon(self)
        frame = ttk.Frame(self, padding=(24, 22, 24, 18))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=t("backups_title"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text=t("backups_intro"), style="Body.TLabel", wraplength=700, justify="left").pack(
            anchor="w", pady=(6, 0))
        self.where = ttk.Label(frame, style="Muted.TLabel", wraplength=700, justify="left")
        self.where.pack(anchor="w", pady=(6, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(14, 0))
        ttk.Button(buttons, text=t("w_close"), style="Accent.TButton", command=self.destroy).pack(side="right")
        self.btn_all = ttk.Button(buttons, text=t("btn_delete_all"), command=lambda: self.delete(all_=True))
        self.btn_all.pack(side="right", padx=(0, 8))
        self.btn_sel = ttk.Button(buttons, text=t("btn_delete_selected"), command=self.delete)
        self.btn_sel.pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text=t("btn_open_folder"), command=self.open_folder).pack(side="left")
        self.total = ttk.Label(buttons, style="Muted.TLabel")
        self.total.pack(side="left", padx=(12, 0))

        # pulizia per età: si tengono i backup degli ultimi N giorni di calendario, oggi compreso
        older = ttk.Frame(frame)
        older.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Label(older, text=t("backups_older_label"), style="Body.TLabel").pack(side="left")
        self.days = ttk.Spinbox(older, from_=1, to=3650, width=5, justify="right")
        self.days.set(str(app.settings.get("backups_days", 7)))
        self.days.pack(side="left", padx=(8, 8))
        ttk.Label(older, text=t("backups_older_days"), style="Muted.TLabel").pack(side="left")
        self.btn_older = ttk.Button(older, text=t("btn_delete_older"), command=self.delete_older)
        self.btn_older.pack(side="right")

        listf = ttk.Frame(frame)
        listf.pack(fill="both", expand=True, pady=(14, 0))
        self.tree = ttk.Treeview(listf, columns=("when", "size", "folder"), show="headings", selectmode="extended")
        for c, title, w, anchor, stretch in (("when", t("col_date"), 170, "w", False),
                                             ("size", t("col_size") + "   ", 100, "e", False),
                                             ("folder", "   " + t("col_folder"), 380, "w", True)):
            self.tree.heading(c, text=title, anchor=anchor)
            self.tree.column(c, width=w, minwidth=60, anchor=anchor, stretch=stretch)
        attach_scrollbars(self.tree)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_buttons())
        self.bind("<Escape>", lambda e: self.destroy())

        self.load()
        center_on(self, app)
        dark_title_bar(self)
        self.deiconify()
        self.grab_set()

    def load(self):
        self.tree.delete(*self.tree.get_children())
        self.backups = {}
        if not is_library(self.lib):
            self.where.configure(text=t("no_library"))
            self.update_buttons()
            return
        self.where.configure(text=t("col_folder") + ": " + backup_root(self.lib, self.app.settings))
        fmt = DATE_FORMATS.get(i18n.LANG, "%d/%m/%Y") + "  %H:%M:%S"
        for b in list_backups(self.lib, self.app.settings):
            row = self.tree.insert("", "end", values=(b["when"].strftime(fmt), human(b["bytes"]),
                                                      os.path.dirname(b["path"])))
            self.backups[row] = b
        total = sum(b["bytes"] for b in self.backups.values())
        self.total.configure(text=t("backups_total", count=len(self.backups), size=human(total)) if self.backups
                             else t("backups_none"))
        self.update_buttons()

    def update_buttons(self):
        self.btn_sel.state(["!disabled"] if self.tree.selection() else ["disabled"])
        self.btn_all.state(["!disabled"] if self.backups else ["disabled"])
        self.btn_older.state(["!disabled"] if self.backups else ["disabled"])

    def open_folder(self):
        sel = self.tree.selection()
        path = self.backups[sel[0]]["path"] if sel else backup_root(self.lib, self.app.settings) if self.lib else None
        if path and os.path.isdir(path):
            os.startfile(path)
        elif path and os.path.isdir(os.path.dirname(path)):
            os.startfile(os.path.dirname(path))

    def older_than(self, days, today=None):
        """I backup fatti prima degli ultimi `days` giorni di calendario (1 = si tengono solo quelli di oggi)."""
        first_kept = (today or datetime.date.today()) - datetime.timedelta(days=days - 1)
        return [b for b in self.backups.values() if b["when"].date() < first_kept]

    def delete_older(self):
        try:
            days = int(str(self.days.get()).strip())
            if days < 1:
                raise ValueError
        except ValueError:
            Dialog.ask(self, t("backups_title"), t("backups_older_bad"), None, level="warn")
            return
        self.app.settings["backups_days"] = days
        save_settings(self.app.settings)
        chosen = self.older_than(days)
        if not chosen:
            Dialog.ask(self, t("backups_title"), t("backups_older_none", days=days), None, level="info")
            return
        self.delete(chosen=chosen)

    def delete(self, all_=False, chosen=None):
        if chosen is None:
            chosen = list(self.backups.values()) if all_ else [self.backups[r] for r in self.tree.selection()]
        if not chosen:
            return
        size = human(sum(b["bytes"] for b in chosen))
        answer = Dialog.ask(self, t("backups_title"), t("backups_delete_head", count=len(chosen), size=size),
                            t("backups_delete_body"), level="warn",
                            buttons=[("cancel", t("btn_cancel"), False), ("delete", t("btn_delete"), True)],
                            default="cancel")
        if answer != "delete":
            return
        self.config(cursor="watch")
        self.update()
        failed = delete_backups([b["path"] for b in chosen])
        self.config(cursor="")
        if failed:
            Dialog.ask(self, t("backups_title"), t("backups_delete_failed"), None, level="error",
                       detail="\n".join(failed[:20]))
        self.load()


class MappingsDialog(tk.Toplevel):
    """Per ogni coppia fotocamera + stile trovata nelle foto: il profilo da usare."""

    def __init__(self, app, focus_key=None):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.saved = False
        self.title(t("maps_title"))
        self.resizable(False, False)
        self.transient(app)
        set_icon(self)
        frame = ttk.Frame(self, padding=(24, 22, 24, 18))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=t("maps_title"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text=t("maps_intro"), style="Body.TLabel", wraplength=720, justify="left").pack(
            anchor="w", pady=(6, 0))

        # stili presenti nelle foto esaminate: (modello, chiave) -> [nome da mostrare, quante foto, stile]
        found = {}
        for item in app.items:
            if item["style_key"] and item["model"]:
                entry = found.setdefault((item["model"], item["style_key"]), [style_text(item), 0, item])
                entry[1] += 1
        grid = ttk.Frame(frame)
        grid.pack(fill="x", pady=(16, 0))
        for col, key in enumerate(("maps_col_camera", "maps_col_style", "maps_col_photos", "maps_col_profile")):
            ttk.Label(grid, text=t(key), style="Muted.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 18),
                                                                   pady=(0, 6))
        self.rows = []
        first = None
        for r, ((model, key), (label, count, item)) in enumerate(
                sorted(found.items(), key=lambda kv: (kv[0][0], kv[1][0].lower())), start=1):
            profiles = list(app.finder.profiles_for(model))
            auto = cs.auto_profile(item["style_name"], profiles, app.finder.preferred(model))
            values = [t("maps_auto", name=auto) if auto else t("maps_auto_none"), t("maps_skip"), *profiles]
            ttk.Label(grid, text=model, style="Body.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 18), pady=3)
            ttk.Label(grid, text=label, style="Strong.TLabel" if not item["style_name"] else "Body.TLabel").grid(
                row=r, column=1, sticky="w", padx=(0, 18), pady=3)
            ttk.Label(grid, text=str(count), style="Body.TLabel").grid(row=r, column=2, sticky="w", padx=(0, 18), pady=3)
            if profiles:
                combo = ttk.Combobox(grid, values=values, state="readonly", width=34)
                chosen = matcher.mapping_for(app.settings, model, key)
                combo.current(0 if chosen is None else 1 if chosen == "" else
                              values.index(chosen) if chosen in values else 0)
                combo.grid(row=r, column=3, sticky="w", pady=3)
                self.rows.append((model, key, combo))
                if first is None or key == focus_key:  # si parte dallo stile scelto col tasto destro
                    first = combo
            else:
                ttk.Label(grid, text=t("maps_no_profiles"), style="Muted.TLabel").grid(row=r, column=3, sticky="w")
        if not found:
            ttk.Label(grid, text=t("maps_empty"), style="Muted.TLabel").grid(row=1, column=0, columnspan=4, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(20, 0))
        ttk.Button(buttons, text=t("btn_save"), style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(buttons, text=t("btn_cancel"), command=self.destroy).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self.destroy())
        center_on(self, app)
        dark_title_bar(self)
        self.deiconify()
        self.grab_set()
        if first is not None:
            first.focus_set()

    def save(self):
        changed = []
        for model, key, combo in self.rows:
            i = combo.current()
            new = None if i <= 0 else "" if i == 1 else combo.get()
            if new != matcher.mapping_for(self.app.settings, model, key):
                changed.append((model, key))
        if changed:
            count = sum(1 for i in self.app.items if (i["model"], i["style_key"]) in changed)
            if Dialog.ask(self, t("maps_title"), t("maps_confirm_title"), t("maps_confirm_body", count=count),
                          level="warn", buttons=[("cancel", t("btn_cancel"), False), ("ok", t("btn_save"), True)]) != "ok":
                return
        mappings = self.app.settings.setdefault("mappings", {})
        for model, key, combo in self.rows:
            camera = mappings.setdefault(cs.norm(model), {})
            i = combo.current()
            if i <= 0:
                camera.pop(key, None)  # automatico
            elif i == 1:
                camera[key] = ""  # non cambiare
            else:
                camera[key] = combo.get()
            if not camera:
                mappings.pop(cs.norm(model), None)
        save_settings(self.app.settings)
        self.saved = True
        self.destroy()


class AdvancedFilterDialog(tk.Toplevel):
    """Lo storico di tutte le importazioni, da restringere con un intervallo di date e (facoltativo) di ore; poi si
    esaminano le foto dell'intervallo o solo le importazioni selezionate. result: la scelta per "Foto da esaminare"."""

    def __init__(self, app, batches, scope):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.batches = batches
        self.shown = list(batches)
        self.start = self.end = ""
        self.result = None
        self.fmt = DATE_FORMATS.get(i18n.LANG, "%d/%m/%Y")
        self.title(t("adv_title"))
        self.resizable(False, False)
        self.transient(app)
        set_icon(self)
        frame = ttk.Frame(self, padding=(24, 22, 24, 18))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=t("adv_title"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text=t("adv_intro"), style="Body.TLabel", wraplength=640, justify="left").pack(anchor="w",
                                                                                                    pady=(6, 0))
        # giorni con importazioni: evidenziati nel calendario
        marked = {local_time(b["start"]).date() for b in batches if local_time(b["start"])}
        grid = ttk.Frame(frame)
        grid.pack(fill="x", pady=(16, 0))
        self.pickers = {}
        for r, (prefix, label, default) in enumerate((("from", "adv_from", (0, 0)), ("to", "adv_to", (23, 59)))):
            ttk.Label(grid, text=t(label).capitalize(), style="Body.TLabel").grid(row=r, column=0, sticky="w",
                                                                                 padx=(0, 10), pady=3)
            day = DateField(grid, self.fmt, marked, on_change=self.apply)
            day.grid(row=r, column=1, sticky="w", pady=3)
            clock = TimeField(grid, t("adv_time"), default=default, on_change=self.apply)
            clock.grid(row=r, column=2, sticky="w", padx=(18, 0), pady=3)
            self.pickers[prefix] = (day, clock)
        ttk.Button(grid, text=t("adv_reset"), command=self.reset).grid(row=0, column=3, rowspan=2, padx=(24, 0))
        ttk.Label(frame, text=t("adv_hint"), style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        self.error = ttk.Label(frame, text="", style="Muted.TLabel", foreground="#f87171")
        self.error.pack(anchor="w")

        box = ttk.Frame(frame)
        box.pack(fill="both", expand=True, pady=(10, 0))
        self.tree = ttk.Treeview(box, columns=("when", "photos"), show="headings", height=12, selectmode="extended")
        self.tree.heading("when", text=t("adv_col_when"), anchor="w")
        self.tree.heading("photos", text=t("adv_col_photos"), anchor="e")
        self.tree.column("when", width=420, anchor="w")
        self.tree.column("photos", width=110, anchor="e", stretch=False)
        attach_scrollbars(self.tree)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_summary())
        self.summary = ttk.Label(frame, text="", style="Muted.TLabel")
        self.summary.pack(anchor="w", pady=(6, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(16, 0))
        ttk.Button(buttons, text=t("adv_show"), style="Accent.TButton", command=self.choose).pack(side="right")
        ttk.Button(buttons, text=t("btn_cancel"), command=self.destroy).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self.destroy())

        # si riparte dall'intervallo scelto l'ultima volta
        if scope.startswith("range:"):
            start, _sep, end = scope[6:].partition("|")
            for prefix, ts, empty_time in (("from", start, (0, 0)), ("to", end, (23, 59))):
                local = local_time(ts) if ts else None
                if local:
                    day, clock = self.pickers[prefix]
                    day.set(local.date())
                    if (local.hour, local.minute) != empty_time:
                        clock.set((local.hour, local.minute))
            self.apply()
        else:
            self.fill()
        center_on(self, app)
        dark_title_bar(self)
        self.deiconify()
        self.grab_set()
        self.pickers["from"][0].entry.focus_set()

    def moment(self, prefix, end):
        """L'estremo dell'intervallo come ora del PC (senza data: nessun limite). Senza ora: inizio o fine giornata."""
        day, clock = self.pickers[prefix]
        if not day.value:
            return None
        h, m = clock.value or ((23, 59) if end else (0, 0))
        return datetime.datetime.combine(day.value, datetime.time(h, m, 59 if end else 0))

    def apply(self):
        start, end = self.moment("from", False), self.moment("to", True)
        if start and end and start > end:
            self.error.configure(text=t("adv_order"))
            return
        self.error.configure(text="")
        self.start, self.end = (utc_text(start) if start else ""), (utc_text(end) if end else "")
        scope = f"range:{self.start}|{self.end}"
        self.shown = [b for b in self.batches
                      if not (self.start or self.end) or in_scope(b["start"], scope, self.batches)]
        self.fill()

    def reset(self):
        for day, clock in self.pickers.values():
            day.set(None)
            clock.set(None)
        self.apply()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for i, b in enumerate(self.shown):
            self.tree.insert("", "end", iid=str(i), values=(local_text(b["start"]), b["count"]))
        self.update_summary()

    def picked(self):
        return [self.shown[int(i)] for i in self.tree.selection()]

    def update_summary(self):
        chosen = self.picked()
        if chosen:
            text = t("adv_selected", batches=len(chosen), count=sum(b["count"] for b in chosen))
        else:
            text = t("adv_summary", batches=len(self.shown), count=sum(b["count"] for b in self.shown))
        self.summary.configure(text=text)

    def choose(self):
        chosen = self.picked()
        if chosen:
            self.result = "batches:" + ";".join(f"{b['start']}/{b['end']}" for b in chosen)
        elif self.start or self.end:
            self.result = f"range:{self.start}|{self.end}"
        else:
            self.result = "all"
        self.destroy()


class PhotoProfileDialog(tk.Toplevel):
    """Il profilo per alcune foto soltanto: non cambia la regola dello stile né le importazioni automatiche."""

    def __init__(self, app, items):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.items = items
        self.saved = False
        self.title(t("photo_title"))
        self.resizable(False, False)
        self.transient(app)
        set_icon(self)
        frame = ttk.Frame(self, padding=(24, 22, 24, 18))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=t("photo_title"), style="Title.TLabel").pack(anchor="w")
        model = items[0]["model"]
        ttk.Label(frame, text=t("photo_intro", count=len(items), camera=model), style="Body.TLabel", wraplength=560,
                  justify="left").pack(anchor="w", pady=(6, 0))
        self.profiles = list(app.finder.profiles_for(model))
        first = items[0]
        rule, _path = matcher.target_profile(app.settings, app.finder, model,
                                             {"key": first["style_key"], "name": first["style_name"]})
        values = [t("photo_auto", name=rule) if rule else t("photo_auto_none"), *self.profiles]
        self.combo = ttk.Combobox(frame, values=values, state="readonly", width=46)
        own = {i["target"] for i in items if i.get("own")}
        current = own.pop() if len(own) == 1 and len(items) == sum(1 for i in items if i.get("own")) else None
        self.combo.current(1 + self.profiles.index(current) if current in self.profiles else 0)
        self.combo.pack(anchor="w", pady=(16, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(20, 0))
        ttk.Button(buttons, text=t("btn_save"), style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(buttons, text=t("btn_cancel"), command=self.destroy).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self.destroy())
        center_on(self, app)
        dark_title_bar(self)
        self.deiconify()
        self.grab_set()
        self.combo.focus_set()

    def save(self):
        lib = self.app.settings.get("library", "")
        choices = self.app.settings.setdefault("choices", {}).setdefault(lib, {})
        i = self.combo.current()
        for item in self.items:
            if i <= 0:
                if str(choices.get(str(item["id"]), "")).startswith("profile:"):
                    choices.pop(str(item["id"]), None)  # torna a seguire la regola dello stile
            else:
                choices[str(item["id"])] = "profile:" + self.profiles[i - 1]
        if not choices:
            self.app.settings["choices"].pop(lib, None)
        save_settings(self.app.settings)
        self.saved = True
        self.destroy()


class AutoImportDialog(tk.Toplevel):
    """Cartelle osservate in background: le foto nuove vanno nella libreria scelta con il profilo del loro stile."""

    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.saved = False
        self.title(t("auto_title"))
        self.resizable(False, False)
        self.transient(app)
        set_icon(self)
        w = app.settings.get("watch") or {}
        self.rules = [dict(r) for r in w.get("rules") or []]
        self.added = []  # (regola, importa anche le foto già presenti): lo stato si crea al salvataggio
        frame = ttk.Frame(self, padding=(24, 22, 24, 18))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=t("auto_title"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text=t("auto_intro"), style="Body.TLabel", wraplength=720, justify="left").pack(
            anchor="w", pady=(6, 0))

        ttk.Label(frame, text=t("auto_folders").upper(), style="Muted.TLabel").pack(anchor="w", pady=(18, 4))
        box = ttk.Frame(frame)
        box.pack(fill="x")
        self.tree = ttk.Treeview(box, columns=("folder", "subfolders", "library"), show="headings", height=4,
                                 selectmode="browse")
        self.tree.heading("folder", text=t("auto_col_folder"), anchor="w")
        self.tree.heading("subfolders", text=t("auto_col_subfolders"), anchor="w")
        self.tree.heading("library", text=t("auto_col_library"), anchor="w")
        self.tree.column("folder", width=380, anchor="w")
        self.tree.column("subfolders", width=110, minwidth=90, anchor="w", stretch=False)
        self.tree.column("library", width=250, anchor="w")
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_buttons())
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text=t("auto_into"), style="Body.TLabel").pack(side="left")
        self.libs = list(app.settings.get("libraries", []))
        self.lib_combo = ttk.Combobox(row, state="readonly", width=40, values=app.library_names())
        if app.settings.get("library") in self.libs:
            self.lib_combo.current(self.libs.index(app.settings["library"]))
        elif self.libs:
            self.lib_combo.current(0)
        self.lib_combo.pack(side="left", padx=(10, 0))
        self.btn_remove = ttk.Button(row, text=t("auto_remove"), command=self.remove)
        self.btn_remove.pack(side="right")
        ttk.Button(row, text="＋  " + t("auto_add"), command=self.add).pack(side="right", padx=(0, 8))
        # per le cartelle organizzate per anno/mese/giorno; vale per la cartella che si aggiunge (per cambiarla:
        # togli la cartella e aggiungila di nuovo, così le foto già presenti nelle sottocartelle non sembrano nuove)
        self.subfolders = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text=t("auto_subfolders"), variable=self.subfolders).pack(anchor="w", pady=(8, 0))

        # librerie da consultare sul telefono (sezione Libreria di ViewerCraftStar): indice scritto in una cartella del NAS
        ph = app.settings.get("phone") or {}
        ttk.Label(frame, text=t("auto_phone").upper(), style="Muted.TLabel").pack(anchor="w", pady=(18, 4))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text=t("auto_phone_folder"), style="Body.TLabel").pack(side="left")
        self.phone_folder = tk.StringVar(value=ph.get("folder") or "")
        ttk.Entry(row, textvariable=self.phone_folder, state="readonly", width=58).pack(side="left", padx=(10, 8))
        ttk.Button(row, text=t("auto_phone_pick"), command=self.pick_phone_folder).pack(side="left")
        ttk.Label(frame, text=t("auto_phone_hint"), style="Muted.TLabel", wraplength=720, justify="left").pack(
            anchor="w", pady=(4, 0))
        chosen = {watcher.key(x) for x in ph.get("libraries") or []}
        self.phone_vars = []
        ttk.Label(frame, text=t("auto_phone_libs"), style="Body.TLabel").pack(anchor="w", pady=(8, 2))
        libs_box = ttk.Frame(frame)
        libs_box.pack(fill="x")
        for r, lib in enumerate(self.libs):
            var = tk.BooleanVar(value=watcher.key(lib) in chosen)
            ttk.Checkbutton(libs_box, text=self.lib_name(lib), variable=var).grid(row=r, column=0, sticky="w")
            ttk.Label(libs_box, text=lib, style="Muted.TLabel").grid(row=r, column=1, sticky="w", padx=(12, 0))
            self.phone_vars.append((lib, var))
        # ricerca per soggetti sul telefono: il motore IA (facoltativo) lavora sulle librerie spuntate qui sopra
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(10, 0))
        self.ai_label = ttk.Label(row, style="Body.TLabel", wraplength=560, justify="left")
        self.ai_label.pack(side="left")
        ttk.Button(row, text=t("ai_button"), command=self.open_ai).pack(side="right")
        self.show_ai()

        # raw proprietari in DNG (Adobe DNG Converter)
        ttk.Label(frame, text=t("dng_title").upper(), style="Muted.TLabel").pack(anchor="w", pady=(18, 4))
        dcfg = app.settings.get("dng") or {}
        self.dng_convert = tk.BooleanVar(value=bool(dcfg.get("convert")))
        self.dng_existing = tk.BooleanVar(value=bool(dcfg.get("existing")))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Checkbutton(row, text=t("dng_convert"), variable=self.dng_convert,
                        command=self.dng_changed).pack(side="left")
        self.dng_state = ttk.Label(row, style="Muted.TLabel")
        self.dng_state.pack(side="left", padx=(12, 0))
        self.dng_install_btn = ttk.Button(row, text=t("dng_install"), command=self.dng_install)
        ttk.Checkbutton(frame, text=t("dng_existing", count=self.raw_count()), variable=self.dng_existing).pack(
            anchor="w", pady=(4, 0))
        ttk.Label(frame, text=t("dng_hint"), style="Muted.TLabel", wraplength=720, justify="left").pack(
            anchor="w", pady=(4, 0))
        self.dng_show()

        ttk.Label(frame, text=t("auto_options").upper(), style="Muted.TLabel").pack(anchor="w", pady=(18, 4))
        self.enabled = tk.BooleanVar(value=bool(w.get("enabled")))
        ttk.Checkbutton(frame, text=t("auto_enable"), variable=self.enabled).pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text=t("auto_interval"), style="Body.TLabel").pack(side="left")
        self.interval = tk.IntVar(value=int(w.get("interval") or watcher.DEFAULT_INTERVAL))
        ttk.Spinbox(row, from_=1, to=60, textvariable=self.interval, width=5, state="readonly").pack(
            side="left", padx=(10, 0))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text=t("auto_quiet"), style="Body.TLabel").pack(side="left")
        self.quiet = tk.IntVar(value=int(w.get("quiet") or watcher.DEFAULT_QUIET))
        ttk.Spinbox(row, from_=10, to=900, increment=10, textvariable=self.quiet, width=5, state="readonly").pack(
            side="left", padx=(10, 0))
        ttk.Label(frame, text=t("auto_quiet_hint"), style="Muted.TLabel", wraplength=720, justify="left").pack(
            anchor="w", pady=(4, 0))
        ttk.Label(frame, text=t("auto_hint"), style="Muted.TLabel", wraplength=720, justify="left").pack(
            anchor="w", pady=(8, 0))
        self.status = ttk.Label(frame, style="Strong.TLabel", wraplength=720, justify="left")
        self.status.pack(anchor="w", pady=(14, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(buttons, text=t("btn_save"), style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(buttons, text=t("btn_cancel"), command=self.destroy).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self.destroy())
        self.fill()
        self.show_status()
        center_on(self, app)
        dark_title_bar(self)
        self.deiconify()
        self.grab_set()

    def lib_name(self, lib):
        return os.path.basename(lib) or lib

    def raw_count(self):
        """Quante foto raw proprietarie ci sono nelle librerie (per la casella dei raw già importati)."""
        import dng
        n = 0
        for lib in self.libs:
            try:
                n += len(dng.library_raws(lib))
            except Exception:  # noqa: BLE001 — libreria non raggiungibile adesso
                pass
        return n

    def dng_show(self):
        import dng
        ok = dng.exe() is not None
        self.dng_state.configure(text=("✓  " + t("dng_installed")) if ok else t("dng_missing"))
        if ok:
            self.dng_install_btn.pack_forget()
        else:
            self.dng_install_btn.pack(side="left", padx=(12, 0))

    def dng_changed(self):
        import dng
        if self.dng_convert.get() and dng.exe() is None:
            self.dng_install()

    def dng_install(self):
        import dng
        answer = Dialog.ask(self, t("dng_title"), t("dng_install_head"), t("dng_install_body"), level="info",
                            buttons=[("cancel", t("btn_cancel"), False), ("install", t("dng_install"), True)],
                            default="install")
        if answer != "install":
            if dng.exe() is None:
                self.dng_convert.set(False)
            return
        self.dng_state.configure(text=t("dng_installing"))
        self.configure(cursor="watch")

        def work():
            ok, detail = dng.install()
            self.after(0, lambda: self.dng_installed(ok, detail))
        threading.Thread(target=work, daemon=True).start()

    def dng_installed(self, ok, detail):
        self.configure(cursor="")
        if not ok:
            self.dng_convert.set(False)
            Dialog.ask(self, t("dng_title"), t("dng_install_failed"), detail, level="error")
        self.dng_show()

    def show_ai(self):
        s = self.app.settings
        folder = ai.engine_dir(s)
        if ai.installing(folder):
            text = t("ai_short_installing")
        elif not ai.installed_info(folder):
            text = t("ai_short_none")
        elif not (s.get("ai") or {}).get("enabled"):
            text = t("ai_short_off")
        else:
            text = t("ai_short_on")
        self.ai_label.configure(text=text)

    def open_ai(self):
        d = AiDialog(self, self.app)
        self.wait_window(d)
        self.grab_set()
        self.show_ai()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rules):
            self.tree.insert("", "end", iid=str(i), values=(r["folder"], t("w_yes") if r.get("subfolders") else t("w_no"),
                                                            self.lib_name(r["library"])))
        self.update_buttons()

    def update_buttons(self):
        self.btn_remove.state(["!disabled"] if self.tree.selection() else ["disabled"])

    def show_status(self):
        lines = []
        st = watcher.status()
        checked = st.get("checked") if autostart.is_enabled() else None
        if not (self.app.settings.get("watch") or {}).get("enabled"):
            lines.append(t("auto_status_off"))
        elif checked:
            lines.append(t("auto_status_on", time=when_text(checked)))
        else:  # il processo scrive status.json a ogni giro e lo toglie quando si chiude
            lines.append(t("auto_status_stopped"))
        last = [watcher.load_rule_state(r).get("last") for r in self.rules]
        last = max((x for x in last if x and (x.get("imported") or x.get("profiles"))), default=None,
                   key=lambda x: x["when"])
        if last:
            lines.append(t("auto_status_last", count=last["imported"], profiles=last["profiles"],
                           when=when_text(last["when"])))
        self.status.configure(text="\n".join(lines))

    def add(self):
        if not self.libs:
            Dialog.ask(self, t("auto_title"), t("auto_no_library"), None, level="warn")
            return
        i = self.lib_combo.current()
        lib = self.libs[i if i >= 0 else 0]
        start = os.path.dirname(lib)
        folder = filedialog.askdirectory(parent=self, title=t("auto_pick"), initialdir=start)
        if not folder:
            return
        # scelta passando da Rete › NAS: si torna alla lettera dell'unità, come nella libreria
        folder = watcher.prefer_drive(os.path.normpath(folder))
        if any(watcher.key(r["folder"]) == watcher.key(folder) and watcher.key(r["library"]) == watcher.key(lib)
               for r in self.rules):
            Dialog.ask(self, t("auto_title"), t("auto_dup"), folder, level="warn")
            return
        subfolders = bool(self.subfolders.get())
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            existing = watcher.new_files(folder, lib, subfolders)
        except OSError as e:
            Dialog.ask(self, t("auto_title"), t("auto_folder_bad"), str(e), level="error")
            return
        finally:
            self.configure(cursor="")
        import_existing = False
        if existing:
            answer = Dialog.ask(self, t("auto_title"), t("auto_existing_head", count=len(existing)),
                                t("auto_existing_body"), level="info",
                                buttons=[("import", t("btn_import_too"), False), ("ignore", t("btn_ignore"), True)],
                                default="ignore")
            if answer is None:
                return
            import_existing = answer == "import"
        rule = {"folder": folder, "library": lib, "subfolders": subfolders}
        self.rules.append(rule)
        self.added.append((rule, import_existing))
        self.fill()

    def pick_phone_folder(self):
        # si parte dalla radice dell'unità delle foto, dove creare la cartella nuova (non dentro la cartella delle foto)
        start = self.phone_folder.get() or (os.path.splitdrive(self.rules[0]["folder"])[0] + "\\" if self.rules else "")
        folder = filedialog.askdirectory(parent=self, title=t("auto_phone_pick_title"), initialdir=start or None)
        if not folder:
            return
        folder = watcher.prefer_drive(os.path.normpath(folder))
        if self.has_photos(folder):
            Dialog.ask(self, t("auto_title"), t("auto_phone_has_photos"), folder, level="warn")
            return
        self.phone_folder.set(folder)

    def has_photos(self, folder):
        """La cartella è una cartella osservata, o contiene direttamente foto: non va bene per il telefono."""
        if any(watcher.key(r["folder"]) == watcher.key(folder) for r in self.rules):
            return True
        try:
            with os.scandir(folder) as it:
                return any(e.is_file() and os.path.splitext(e.name)[1].lower() in watcher.PHOTO_EXTS for e in it)
        except OSError:
            return False

    def remove(self):
        sel = self.tree.selection()
        if not sel:
            return
        rule = self.rules.pop(int(sel[0]))
        self.added = [(r, x) for r, x in self.added if r is not rule]
        self.fill()

    def save(self):
        s = self.app.settings
        before = s.get("watch") or {}
        phone_libs = [lib for lib, var in self.phone_vars if var.get()]
        phone_folder = self.phone_folder.get().strip()
        if phone_libs and not phone_folder:
            Dialog.ask(self, t("auto_title"), t("auto_phone_need_folder"), None, level="warn")
            return
        s["phone"] = {**(s.get("phone") or {}), "folder": phone_folder, "libraries": phone_libs}
        import dng
        convert = bool(self.dng_convert.get()) and dng.exe() is not None
        s["dng"] = {"convert": convert, "existing": convert and bool(self.dng_existing.get())}
        enabled = bool(self.enabled.get()) and bool(self.rules or phone_libs)
        try:
            # prima lo stato delle regole nuove, poi le impostazioni: il processo in background non vede mai una
            # regola senza il suo elenco di file da ignorare
            for rule, import_existing in self.added:
                watcher.create_rule_state(rule, import_existing)
            kept = {(watcher.key(r["folder"]), watcher.key(r["library"])) for r in self.rules}
            for r in before.get("rules") or []:
                if (watcher.key(r["folder"]), watcher.key(r["library"])) not in kept:
                    watcher.forget_rule(r)
            s["watch"] = {"enabled": enabled, "interval": max(1, min(60, int(self.interval.get()))),
                          "quiet": max(10, min(900, int(self.quiet.get()))), "rules": self.rules}
            save_settings(s)
            # il processo in corso (se c'è) si chiude tra un giro e l'altro: riparte con le impostazioni nuove,
            # oppure resta spento insieme alla sua icona
            self.configure(cursor="watch")
            self.update_idletasks()
            autostart.stop_running(timeout=30)
            if enabled:
                autostart.enable()
            elif autostart.is_enabled():
                autostart.disable()
        except Exception as e:  # noqa: BLE001
            log(f"ERROR automatic import: {e}")
            Dialog.ask(self, t("auto_title"), t("auto_task_error"), str(e), level="error")
            return
        log("automatic import " + ("on: " + "; ".join(f"{r['folder']} -> {r['library']}" for r in self.rules)
                                   if enabled else "off"))
        self.saved = True
        self.destroy()


class SetupWizard(tk.Toplevel):
    """Configurazione guidata: al primo avvio (e da Impostazioni). Spiega a cosa serve ogni cartella e, se la cartella
    di servizio sul NAS ha già una copia delle impostazioni (PC nuovo, formattato), le ripristina."""

    PAGES = ("welcome", "lightcraft", "service", "photos", "phone", "ai", "summary")

    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.done = False
        self.open_ai_after = tk.BooleanVar(value=False)
        self.title(t("wz_title"))
        self.resizable(False, False)
        self.transient(app)
        set_icon(self)
        self.load(json.loads(json.dumps(app.settings)))
        self.restore = tk.StringVar(value="new")
        self.restore_folder = tk.StringVar(value="")
        self.found = None  # copia delle impostazioni trovata nella cartella di servizio
        self.added = []  # (regola, importa anche le foto già presenti)
        self.page = 0
        outer = ttk.Frame(self, padding=(26, 22, 26, 18))
        outer.pack(fill="both", expand=True)
        self.step_label = ttk.Label(outer, style="Muted.TLabel")
        self.step_label.pack(anchor="w")
        self.body = ttk.Frame(outer, width=780, height=500)
        self.body.pack(fill="both", expand=True, pady=(6, 0))
        self.body.pack_propagate(False)
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(14, 0))
        self.btn_next = ttk.Button(buttons, style="Accent.TButton", command=self.next)
        self.btn_next.pack(side="right")
        self.btn_back = ttk.Button(buttons, text=t("wz_back"), command=self.back)
        self.btn_back.pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text=t("btn_cancel"), command=self.destroy).pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())
        self.render()
        center_on(self, app)
        dark_title_bar(self)
        self.deiconify()
        self.grab_set()

    # ---- stato

    def load(self, s):
        """Prende i valori da un dizionario di impostazioni (quelle attuali o quelle ripristinate)."""
        self.s = s
        self.libs = list(s.get("libraries") or [])
        self.cli = tk.StringVar(value=s.get("cli") or DEFAULT_CLI)
        ph = s.get("phone") or {}
        self.service = tk.StringVar(value=ph.get("folder") or "")
        chosen = {watcher.key(x) for x in ph.get("libraries") or []}
        self.phone_chosen = {watcher.key(lib): (watcher.key(lib) in chosen if ph else True) for lib in self.libs}
        self.previews = tk.BooleanVar(value=bool(ph.get("previews", True)))
        backup_dir = s.get("backup_dir") or ""
        self.backup_custom = backup_dir
        in_service = bool(backup_dir and ph.get("folder") and watcher.key(backup_dir) == watcher.key(
            os.path.join(ph["folder"], "backups")))
        if in_service or (not backup_dir and not s.get("libraries")):
            mode = "service"  # consigliato: tutto nella cartella di servizio
        else:
            mode = "custom" if backup_dir else "library"
        self.backup_mode = tk.StringVar(value=mode)
        self.rules = [dict(r) for r in (s.get("watch") or {}).get("rules") or []]
        self.subfolders = tk.BooleanVar(value=True)

    def lib_name(self, lib):
        return os.path.basename(os.path.normpath(lib)) or lib

    # ---- pagine

    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def heading(self, title, text):
        ttk.Label(self.body, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text=text, style="Body.TLabel", wraplength=760, justify="left").pack(anchor="w",
                                                                                                pady=(6, 0))

    def section(self, text):
        ttk.Label(self.body, text=text.upper(), style="Muted.TLabel").pack(anchor="w", pady=(16, 4))

    def hint(self, text):
        ttk.Label(self.body, text=text, style="Muted.TLabel", wraplength=760, justify="left").pack(
            anchor="w", pady=(4, 0))

    def render(self):
        self.clear()
        name = self.PAGES[self.page]
        self.step_label.configure(text=t("wz_step", n=self.page + 1, total=len(self.PAGES)))
        getattr(self, "page_" + name)()
        self.btn_back.state(["disabled"] if self.page == 0 else ["!disabled"])
        self.btn_next.configure(text=t("wz_finish") if name == "summary" else t("wz_next"))

    def page_welcome(self):
        self.heading(t("wz_welcome_title"), t("wz_welcome_text"))
        box = ttk.Frame(self.body)
        box.pack(fill="x", pady=(12, 0))
        for icon, key in (("▣", "wz_place_photos"), ("◧", "wz_place_library"), ("◈", "wz_place_service")):
            row = ttk.Frame(box)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=icon, style="Strong.TLabel", width=3).pack(side="left", anchor="n")
            ttk.Label(row, text=t(key), style="Body.TLabel", wraplength=720, justify="left").pack(side="left")
        self.section(t("wz_start"))
        ttk.Radiobutton(self.body, text=t("wz_start_new"), value="new", variable=self.restore).pack(anchor="w")
        ttk.Radiobutton(self.body, text=t("wz_start_restore"), value="restore", variable=self.restore).pack(
            anchor="w", pady=(4, 0))
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=(6, 0), padx=(24, 0))
        ttk.Entry(row, textvariable=self.restore_folder, state="readonly", width=60).pack(side="left")
        ttk.Button(row, text=t("btn_browse"), command=self.pick_restore).pack(side="left", padx=(8, 0))
        self.restore_info = ttk.Label(self.body, style="Muted.TLabel", wraplength=720, justify="left")
        self.restore_info.pack(anchor="w", padx=(24, 0), pady=(4, 0))
        self.show_found()

    def pick_restore(self):
        folder = filedialog.askdirectory(parent=self, title=t("wz_restore_pick"),
                                         initialdir=self.restore_folder.get() or None)
        if not folder:
            return
        folder = watcher.prefer_drive(os.path.normpath(folder))
        self.restore_folder.set(folder)
        self.restore.set("restore")
        self.found = watcher.read_settings_copy(folder)
        self.show_found()

    def show_found(self):
        if not self.restore_folder.get():
            text = t("wz_restore_hint")
        elif self.found:
            text = "✓  " + t("wz_restore_found", computer=self.found.get("computer") or "?",
                             when=when_text(self.found.get("saved") or ""))
        else:
            text = "⚠  " + t("wz_restore_none")
        self.restore_info.configure(text=text)

    def page_lightcraft(self):
        self.heading(t("wz_lc_title"), t("wz_lc_text"))
        self.section(t("cli_label"))
        row = ttk.Frame(self.body)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.cli, width=70).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text=t("btn_browse"), command=self.pick_cli).pack(side="left", padx=(8, 0))
        self.cli_state = ttk.Label(self.body, style="Muted.TLabel")
        self.cli_state.pack(anchor="w", pady=(4, 0))
        self.cli_trace = self.cli.trace_add("write", lambda *_: self.show_cli())
        self.show_cli()
        self.section(t("wz_lc_libraries"))
        self.lib_tree = ttk.Treeview(self.body, columns=("name", "path"), show="headings", height=5,
                                     selectmode="browse")
        self.lib_tree.heading("name", text=t("wz_col_name"), anchor="w")
        self.lib_tree.heading("path", text=t("wz_col_folder"), anchor="w")
        self.lib_tree.column("name", width=230, anchor="w")
        self.lib_tree.column("path", width=520, anchor="w")
        self.lib_tree.pack(fill="x")
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="＋  " + t("wz_add_library"), command=self.add_library).pack(side="left")
        ttk.Button(row, text=t("auto_remove"), command=self.remove_library).pack(side="left", padx=(8, 0))
        self.hint(t("wz_lc_hint"))
        self.fill_libs()

    def show_cli(self):
        if not self.cli_state.winfo_exists():
            return
        ok = is_cli(self.cli.get().strip())
        self.cli_state.configure(text="✓  " + t("wz_cli_ok") if ok else "⚠  " + t("wz_cli_missing"))

    def pick_cli(self):
        start = os.path.dirname(self.cli.get()) if os.path.isdir(os.path.dirname(self.cli.get())) else LOCAL
        path = filedialog.askopenfilename(parent=self, title=t("cli_pick"), initialdir=start,
                                          filetypes=[("lightcraft-cli.exe", "lightcraft-cli.exe")])
        if path:
            self.cli.set(os.path.normpath(path))

    def fill_libs(self):
        self.lib_tree.delete(*self.lib_tree.get_children())
        for i, lib in enumerate(self.libs):
            mark = "" if is_library(lib) else "⚠  "
            self.lib_tree.insert("", "end", iid=str(i), values=(mark + self.lib_name(lib), lib))

    def add_library(self):
        start = os.path.dirname(self.libs[-1]) if self.libs else os.path.expanduser("~")
        lib = filedialog.askdirectory(parent=self, title=t("pick_library"), initialdir=start)
        if not lib:
            return
        lib = os.path.normpath(lib)
        if not is_library(lib):
            Dialog.ask(self, t("wz_title"), t("pick_library"), t("not_library"), level="error")
            return
        if not any(watcher.key(x) == watcher.key(lib) for x in self.libs):
            self.libs.append(lib)
            self.phone_chosen[watcher.key(lib)] = True
        self.fill_libs()

    def remove_library(self):
        sel = self.lib_tree.selection()
        if sel:
            lib = self.libs.pop(int(sel[0]))
            self.rules = [r for r in self.rules if watcher.key(r["library"]) != watcher.key(lib)]
            self.fill_libs()

    def page_service(self):
        self.heading(t("wz_service_title"), t("wz_service_text"))
        self.section(t("wz_service_folder"))
        row = ttk.Frame(self.body)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.service, state="readonly", width=70).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text=t("auto_phone_pick"), command=self.pick_service).pack(side="left", padx=(8, 0))
        self.hint(t("wz_service_hint"))
        self.section(t("wz_service_inside"))
        tree = ttk.Frame(self.body)
        tree.pack(fill="x")
        for name, key in (("libraries\\<…>\\", "wz_dir_libraries"), ("settings\\", "wz_dir_settings"),
                          ("backups\\", "wz_dir_backups")):
            r = ttk.Frame(tree)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=name, style="Strong.TLabel", width=18).pack(side="left", anchor="n")
            ttk.Label(r, text=t(key), style="Body.TLabel", wraplength=600, justify="left").pack(side="left")
        self.section(t("backup_label"))
        ttk.Radiobutton(self.body, text=t("wz_backup_service"), value="service", variable=self.backup_mode).pack(
            anchor="w")
        ttk.Radiobutton(self.body, text=t("backup_next_to"), value="library", variable=self.backup_mode).pack(
            anchor="w", pady=(2, 0))
        if self.backup_custom and self.backup_mode.get() == "custom":
            ttk.Radiobutton(self.body, text=t("wz_backup_keep", folder=self.backup_custom), value="custom",
                            variable=self.backup_mode).pack(anchor="w", pady=(2, 0))

    def pick_service(self):
        start = self.service.get() or (os.path.splitdrive(self.rules[0]["folder"])[0] + "\\" if self.rules else "")
        folder = filedialog.askdirectory(parent=self, title=t("auto_phone_pick_title"), initialdir=start or None)
        if not folder:
            return
        folder = watcher.prefer_drive(os.path.normpath(folder))
        if self.has_photos(folder):
            Dialog.ask(self, t("wz_title"), t("auto_phone_has_photos"), folder, level="warn")
            return
        self.service.set(folder)

    def has_photos(self, folder):
        if any(watcher.key(r["folder"]) == watcher.key(folder) for r in self.rules):
            return True
        try:
            with os.scandir(folder) as it:
                return any(e.is_file() and os.path.splitext(e.name)[1].lower() in watcher.PHOTO_EXTS for e in it)
        except OSError:
            return False

    def page_photos(self):
        self.heading(t("wz_photos_title"), t("wz_photos_text"))
        self.section(t("auto_folders"))
        self.rule_tree = ttk.Treeview(self.body, columns=("folder", "subfolders", "library"), show="headings",
                                      height=5, selectmode="browse")
        self.rule_tree.heading("folder", text=t("auto_col_folder"), anchor="w")
        self.rule_tree.heading("subfolders", text=t("auto_col_subfolders"), anchor="w")
        self.rule_tree.heading("library", text=t("auto_col_library"), anchor="w")
        self.rule_tree.column("folder", width=400, anchor="w")
        self.rule_tree.column("subfolders", width=110, anchor="w", stretch=False)
        self.rule_tree.column("library", width=240, anchor="w")
        self.rule_tree.pack(fill="x")
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text=t("auto_into"), style="Body.TLabel").pack(side="left")
        self.rule_lib = ttk.Combobox(row, state="readonly", width=36, values=[self.lib_name(x) for x in self.libs])
        if self.libs:
            self.rule_lib.current(0)
        self.rule_lib.pack(side="left", padx=(10, 0))
        ttk.Button(row, text=t("auto_remove"), command=self.remove_rule).pack(side="right")
        ttk.Button(row, text="＋  " + t("auto_add"), command=self.add_rule).pack(side="right", padx=(0, 8))
        ttk.Checkbutton(self.body, text=t("auto_subfolders"), variable=self.subfolders).pack(anchor="w", pady=(8, 0))
        self.hint(t("wz_photos_hint"))
        self.fill_rules()

    def fill_rules(self):
        self.rule_tree.delete(*self.rule_tree.get_children())
        for i, r in enumerate(self.rules):
            self.rule_tree.insert("", "end", iid=str(i), values=(
                r["folder"], t("w_yes") if r.get("subfolders") else t("w_no"), self.lib_name(r["library"])))

    def add_rule(self):
        if not self.libs:
            Dialog.ask(self, t("wz_title"), t("auto_no_library"), None, level="warn")
            return
        lib = self.libs[max(0, self.rule_lib.current())]
        folder = filedialog.askdirectory(parent=self, title=t("auto_pick"), initialdir=os.path.dirname(lib))
        if not folder:
            return
        folder = watcher.prefer_drive(os.path.normpath(folder))
        if self.service.get() and watcher.key(folder) == watcher.key(self.service.get()):
            Dialog.ask(self, t("wz_title"), t("wz_photos_is_service"), folder, level="warn")
            return
        if any(watcher.key(r["folder"]) == watcher.key(folder) and watcher.key(r["library"]) == watcher.key(lib)
               for r in self.rules):
            Dialog.ask(self, t("wz_title"), t("auto_dup"), folder, level="warn")
            return
        subfolders = bool(self.subfolders.get())
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            existing = watcher.new_files(folder, lib, subfolders)
        except OSError as e:
            Dialog.ask(self, t("wz_title"), t("auto_folder_bad"), str(e), level="error")
            return
        finally:
            self.configure(cursor="")
        import_existing = False
        if existing:
            answer = Dialog.ask(self, t("wz_title"), t("auto_existing_head", count=len(existing)),
                                t("auto_existing_body"), level="info",
                                buttons=[("import", t("btn_import_too"), False), ("ignore", t("btn_ignore"), True)],
                                default="ignore")
            if answer is None:
                return
            import_existing = answer == "import"
        rule = {"folder": folder, "library": lib, "subfolders": subfolders}
        self.rules.append(rule)
        self.added.append((rule, import_existing))
        self.fill_rules()

    def remove_rule(self):
        sel = self.rule_tree.selection()
        if sel:
            rule = self.rules.pop(int(sel[0]))
            self.added = [(r, x) for r, x in self.added if r is not rule]
            self.fill_rules()

    def page_phone(self):
        self.heading(t("wz_phone_title"), t("wz_phone_text"))
        self.section(t("wz_phone_libs"))
        for lib in self.libs:
            var = tk.BooleanVar(value=self.phone_chosen.get(watcher.key(lib), True))
            ttk.Checkbutton(self.body, text=self.lib_name(lib), variable=var,
                            command=lambda k=watcher.key(lib), v=var: self.phone_chosen.__setitem__(k, v.get())).pack(
                anchor="w")
        if not self.libs:
            self.hint(t("auto_no_library"))
        if not self.service.get():
            self.hint("⚠  " + t("wz_phone_no_service"))
        self.section(t("wz_previews"))
        ttk.Checkbutton(self.body, text=t("wz_previews_check"), variable=self.previews).pack(anchor="w")
        self.hint(t("wz_previews_hint"))
        self.section(t("wz_phone_app"))
        self.hint(t("wz_phone_hint"))

    def page_ai(self):
        self.heading(t("wz_ai_title"), t("wz_ai_text"))
        folder = ai.engine_dir(self.s)
        info = ai.installed_info(folder)
        self.section(t("ai_button"))
        ttk.Label(self.body, text=("✓  " + t("wz_ai_installed", folder=folder)) if info else t("wz_ai_not_installed"),
                  style="Body.TLabel", wraplength=760, justify="left").pack(anchor="w")
        ttk.Checkbutton(self.body, text=t("wz_ai_open"), variable=self.open_ai_after).pack(anchor="w", pady=(12, 0))
        self.hint(t("wz_ai_hint"))

    def page_summary(self):
        self.heading(t("wz_summary_title"), t("wz_summary_text"))
        svc = self.service.get()
        backups = {"service": os.path.join(svc, "backups") if svc else t("backup_next_to"),
                   "library": t("backup_next_to"), "custom": self.backup_custom}[self.backup_mode.get()]
        phone = [self.lib_name(x) for x in self.libs if self.phone_chosen.get(watcher.key(x), True)] if svc else []
        lines = [(t("wz_lc_libraries"), "\n".join(self.libs) or "—"),
                 (t("wz_service_folder"), svc or t("wz_none")),
                 (t("backup_label"), backups),
                 (t("auto_folders"), "\n".join(f"{r['folder']}  →  {self.lib_name(r['library'])}"
                                               for r in self.rules) or t("wz_none")),
                 (t("wz_phone_libs"), ", ".join(phone) or t("wz_none")),
                 (t("wz_previews"), t("w_yes") if phone and self.previews.get() else t("w_no"))]
        grid = ttk.Frame(self.body)
        grid.pack(fill="x", pady=(14, 0))
        for r, (k, v) in enumerate(lines):
            ttk.Label(grid, text=k, style="Muted.TLabel", wraplength=230, justify="left").grid(
                row=r, column=0, sticky="nw", pady=3, padx=(0, 14))
            ttk.Label(grid, text=v, style="Body.TLabel", wraplength=510, justify="left").grid(
                row=r, column=1, sticky="nw", pady=3)
        self.hint(t("wz_summary_hint"))

    # ---- navigazione

    def check(self, name):
        """Un testo d'avviso se nella pagina manca qualcosa per andare avanti, altrimenti None."""
        if name == "welcome" and self.restore.get() == "restore" and not self.found:
            return t("wz_restore_none")
        if name == "lightcraft" and not self.libs:
            return t("wz_need_library")
        if name == "service" and not self.service.get() and self.backup_mode.get() == "service":
            return t("wz_need_service_backup")
        return None

    def next(self):
        name = self.PAGES[self.page]
        problem = self.check(name)
        if problem:
            Dialog.ask(self, t("wz_title"), problem, None, level="warn")
            return
        if name == "welcome" and self.restore.get() == "restore" and self.found:
            restored = dict(self.found["settings"])
            for k in watcher.LOCAL_ONLY:
                if k in self.app.settings:
                    restored[k] = self.app.settings[k]
            ph = dict(restored.get("phone") or {})
            ph["folder"] = self.restore_folder.get()  # la cartella scelta adesso (la lettera può essere cambiata)
            restored["phone"] = ph
            self.load(restored)
            self.found = None  # ripristinate: le pagine seguenti mostrano i valori da controllare
            self.restore.set("new")
        if name == "summary":
            self.finish()
            return
        self.page += 1
        self.render()

    def back(self):
        if self.page:
            self.page -= 1
            self.render()

    def finish(self):
        s = self.s
        s["libraries"] = self.libs
        if s.get("library") not in self.libs:
            if self.libs:
                s["library"] = self.libs[0]
            else:
                s.pop("library", None)
        cli = self.cli.get().strip()
        if cli and os.path.normcase(cli) != os.path.normcase(DEFAULT_CLI):
            s["cli"] = cli
        else:
            s.pop("cli", None)
        svc = self.service.get().strip()
        phone_libs = [x for x in self.libs if self.phone_chosen.get(watcher.key(x), True)] if svc else []
        s["phone"] = {**(s.get("phone") or {}), "folder": svc, "libraries": phone_libs,
                      "previews": bool(self.previews.get())}
        mode = self.backup_mode.get()
        if mode == "service" and svc:
            s["backup_dir"] = os.path.join(svc, "backups")
        elif mode == "custom" and self.backup_custom:
            s["backup_dir"] = self.backup_custom
        else:
            s.pop("backup_dir", None)
        before = self.app.settings.get("watch") or {}
        w = dict(s.get("watch") or {})
        enabled = bool(self.rules or phone_libs)
        s["watch"] = {"enabled": enabled, "interval": int(w.get("interval") or watcher.DEFAULT_INTERVAL),
                      "quiet": int(w.get("quiet") or watcher.DEFAULT_QUIET), "rules": self.rules}
        s["setup_done"] = True
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            if svc:
                subs = [watcher.LIBRARIES_DIR, watcher.SETTINGS_DIR] + (["backups"] if mode == "service" else [])
                for sub in subs:
                    os.makedirs(os.path.join(svc, sub), exist_ok=True)
            # regole: prima lo stato delle nuove (quali file ignorare), poi le impostazioni
            for rule, import_existing in self.added:
                watcher.create_rule_state(rule, import_existing)
            kept = {(watcher.key(r["folder"]), watcher.key(r["library"])) for r in self.rules}
            for r in before.get("rules") or []:
                if (watcher.key(r["folder"]), watcher.key(r["library"])) not in kept:
                    watcher.forget_rule(r)
            # regole ripristinate da un altro PC (senza stato qui): i file già presenti contano come già importati
            for r in self.rules:
                if not os.path.exists(watcher.rule_state_path(r)):
                    watcher.create_rule_state(r, False)
            self.app.settings.clear()
            self.app.settings.update(s)
            save_settings(self.app.settings)
            autostart.stop_running(timeout=30)
            if enabled:
                autostart.enable()
            elif autostart.is_enabled():
                autostart.disable()
        except Exception as e:  # noqa: BLE001
            log(f"ERROR setup wizard: {e!r}")
            Dialog.ask(self, t("wz_title"), t("wz_error"), str(e), level="error")
            return
        finally:
            if self.winfo_exists():
                self.configure(cursor="")
        log(f"setup wizard: {len(self.libs)} libraries, service folder {svc or '-'}, {len(self.rules)} watched folders")
        self.done = True
        self.destroy()


class AiDialog(tk.Toplevel):
    """Motore IA per la ricerca per soggetti sul telefono: installazione (in un processo a sé), stato del lavoro,
    attivazione, modello e cartella, rimozione."""

    def __init__(self, master, app):
        super().__init__(master)
        self.withdraw()
        self.app = app
        self.title(t("ai_title"))
        self.resizable(False, False)
        self.transient(master)
        set_icon(self)
        self.frame = ttk.Frame(self, padding=(24, 22, 24, 18))
        self.frame.pack(fill="both", expand=True)
        self.mode = None  # quello che la finestra mostra adesso: "install", "installing", "ready", "change"
        self.busy = False
        self.bind("<Escape>", lambda e: self.destroy())
        self.render()
        center_on(self, master)
        dark_title_bar(self)
        self.deiconify()
        self.grab_set()
        self.after(1000, self.poll)

    # ---- dati

    def cfg(self):
        return self.app.settings.get("ai") or {}

    def folder(self):
        return ai.engine_dir(self.app.settings)

    def save_cfg(self, **values):
        self.app.settings["ai"] = {**self.cfg(), **values}
        save_settings(self.app.settings)

    def wanted_mode(self):
        folder = self.folder()
        if ai.installing(folder):
            return "installing"
        info = ai.installed_info(folder)
        if info and self.mode == "change":
            return "change"
        return "ready" if info else "install"

    # ---- disegno

    def render(self):
        for w in self.frame.winfo_children():
            w.destroy()
        self.mode = self.wanted_mode() if self.mode != "change" else "change"
        f = self.frame
        ttk.Label(f, text=t("ai_title"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text=t("ai_intro"), style="Body.TLabel", wraplength=640, justify="left").pack(anchor="w",
                                                                                                 pady=(6, 0))
        self.status = ttk.Label(f, style="Strong.TLabel", wraplength=640, justify="left")
        self.status.pack(anchor="w", pady=(16, 0))
        self.bar = ttk.Progressbar(f, mode="determinate", maximum=1000)
        self.detail = ttk.Label(f, style="Muted.TLabel", wraplength=640, justify="left")
        buttons = ttk.Frame(f)
        if self.mode in ("install", "change"):
            info = ai.installed_info(self.folder()) or {}
            self.choice = AiChoice(f, folder=self.folder(), model=self.cfg().get("model") or info.get("model"),
                                   allow_none=False, wrap=640)
            self.choice.pack(fill="x", pady=(14, 0))
            self.detail.pack(anchor="w", pady=(8, 0))
            buttons.pack(fill="x", pady=(18, 0))
            ttk.Button(buttons, text=t("ai_install") if self.mode == "install" else t("ai_apply"),
                       style="Accent.TButton", command=self.apply).pack(side="right")
            if self.mode == "change":
                ttk.Button(buttons, text=t("btn_cancel"), command=self.cancel_change).pack(side="right", padx=(0, 8))
        elif self.mode == "installing":
            self.bar.pack(fill="x", pady=(14, 0))
            self.detail.pack(anchor="w", pady=(6, 0))
            buttons.pack(fill="x", pady=(18, 0))
            ttk.Button(buttons, text=t("ai_stop_install"), command=self.stop_install).pack(side="right")
        else:
            self.enabled = tk.BooleanVar(value=bool(self.cfg().get("enabled")))
            ttk.Checkbutton(f, text=t("ai_enable"), variable=self.enabled,
                            command=lambda: self.save_cfg(enabled=bool(self.enabled.get()))).pack(anchor="w",
                                                                                                  pady=(14, 0))
            row = ttk.Frame(f)
            row.pack(fill="x", pady=(8, 0))
            ttk.Label(row, text=t("ai_bits"), style="Body.TLabel").pack(side="left")
            self.bits = ttk.Combobox(row, state="readonly", width=40, values=[t("ai_bits_8"), t("ai_bits_4")])
            self.bits.current(0 if int(self.cfg().get("phone_bits") or 8) == 8 else 1)
            self.bits.bind("<<ComboboxSelected>>",
                           lambda e: self.save_cfg(phone_bits=8 if self.bits.current() == 0 else 4))
            self.bits.pack(side="left", padx=(10, 0))
            # descrizioni delle foto (facoltative): scaricano il loro modello la prima volta
            describer = self.cfg().get("describer") or ai.recommended_describer(ai.detect_gpu())
            if describer:
                self.desc_on = tk.BooleanVar(value=bool(self.cfg().get("descriptions")))
                ttk.Checkbutton(f, text=t("ai_desc_enable"), variable=self.desc_on,
                                command=lambda d=describer: self.toggle_descriptions(d)).pack(anchor="w", pady=(10, 0))
                ttk.Label(f, text=t("ai_desc_hint"), style="Muted.TLabel", wraplength=640, justify="left").pack(
                    anchor="w", padx=(24, 0))
            # quando lavorare: GPU libera, fascia oraria per le analisi grandi
            self.gpu_check = tk.BooleanVar(value=bool(self.cfg().get("gpu_check", True)))
            ttk.Checkbutton(f, text=t("ai_gpu_check"), variable=self.gpu_check,
                            command=lambda: self.save_cfg(gpu_check=bool(self.gpu_check.get()))).pack(anchor="w",
                                                                                                    pady=(10, 0))
            win = self.cfg().get("window") or {}
            row = ttk.Frame(f)
            row.pack(anchor="w", pady=(6, 0))
            self.win_on = tk.BooleanVar(value=bool(win.get("enabled")))
            ttk.Checkbutton(row, text=t("ai_window"), variable=self.win_on, command=self.save_window).pack(side="left")
            hours = [f"{h:02d}:00" for h in range(24)]
            self.win_from = ttk.Combobox(row, values=hours, width=6, state="readonly")
            self.win_from.set(win.get("from") or "22:00")
            self.win_from.pack(side="left", padx=(8, 0))
            ttk.Label(row, text=t("ai_window_to"), style="Body.TLabel").pack(side="left", padx=(6, 6))
            self.win_to = ttk.Combobox(row, values=hours, width=6, state="readonly")
            self.win_to.set(win.get("to") or "07:00")
            self.win_to.pack(side="left")
            for c in (self.win_from, self.win_to):
                c.bind("<<ComboboxSelected>>", lambda e: self.save_window())
            ttk.Label(f, text=t("ai_window_hint"), style="Muted.TLabel", wraplength=640, justify="left").pack(
                anchor="w", pady=(2, 0))
            # librerie da analizzare, nell'ordine della coda
            ttk.Label(f, text=t("ai_queue").upper(), style="Muted.TLabel").pack(anchor="w", pady=(16, 4))
            self.queue_box = ttk.Frame(f)
            self.queue_box.pack(fill="x")
            self.fill_queue()
            self.bar.pack(fill="x", pady=(14, 0))
            self.detail.pack(anchor="w", pady=(6, 0))
            buttons.pack(fill="x", pady=(18, 0))
            paused = bool(self.cfg().get("paused"))
            ttk.Button(buttons, text=t("ai_resume") if paused else t("ai_pause"),
                       command=lambda: self.set_pause(not paused)).pack(side="left", padx=(0, 8))
            ttk.Button(buttons, text=t("ai_change"), command=self.start_change).pack(side="left")
            ttk.Button(buttons, text=t("ai_remove"), command=self.remove).pack(side="left", padx=(8, 0))
            ttk.Button(buttons, text=t("ai_log"), command=self.open_log).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text=t("w_close"), command=self.destroy).pack(side="right", padx=(0, 8))
        self.update_status()

    def toggle_descriptions(self, describer):
        on = bool(self.desc_on.get())
        info = ai.installed_info(self.folder()) or {}
        if on and describer not in (info.get("describers") or []):
            gb = ai.DESCRIBERS[describer]["download"]
            if Dialog.ask(self, t("ai_title"), t("ai_desc_q", gb=f"{gb:.0f}"), None, level="info",
                          buttons=[("no", t("btn_cancel"), False), ("yes", t("ai_install"), True)]) != "yes":
                self.desc_on.set(False)
                return
            self.install(self.folder(), self.cfg().get("model") or info.get("model"), describer)
            return
        self.save_cfg(descriptions=on, describer=describer)
        if on:
            ai.maybe_start(self.app.settings, log)

    def save_window(self):
        self.save_cfg(window={"enabled": bool(self.win_on.get()), "from": self.win_from.get(), "to": self.win_to.get()})

    def set_pause(self, paused):
        ai.set_paused(self.app.settings, paused)
        save_settings(self.app.settings)
        if not paused:
            ai.maybe_start(self.app.settings, log)
        self.render()

    def fill_queue(self):
        for w in self.queue_box.winfo_children():
            w.destroy()
        self.queue_labels = {}
        libs = ai.queue(self.app.settings)
        if not libs:
            ttk.Label(self.queue_box, text=t("ai_queue_empty"), style="Muted.TLabel", wraplength=640,
                      justify="left").pack(anchor="w")
            return
        for i, lib in enumerate(libs):
            row = ttk.Frame(self.queue_box)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{i + 1}.  {os.path.basename(os.path.normpath(lib))}", style="Body.TLabel").pack(
                side="left")
            lbl = ttk.Label(row, style="Muted.TLabel")
            lbl.pack(side="left", padx=(10, 0))
            self.queue_labels[lib] = lbl
            ttk.Button(row, text="✕", width=3, command=lambda x=lib: self.unqueue(x)).pack(side="right")
            ttk.Button(row, text="↓", width=3, command=lambda x=lib: self.move(x, 1)).pack(side="right", padx=(0, 4))
            ttk.Button(row, text="↑", width=3, command=lambda x=lib: self.move(x, -1)).pack(side="right", padx=(0, 4))
        self.update_queue()

    def update_queue(self):
        for lib, lbl in (getattr(self, "queue_labels", None) or {}).items():
            st = ai.library_state(self.app.settings, lib)
            state = st["state"]
            if state == "running":
                text = t("ai_q_running", pct=st["done"] * 100 // max(1, st["total"]))
            elif state == "done":
                text = t("ai_row_done", count=st["count"])
                described = ((ai.worker_status(self.folder()).get("libraries") or {})
                             .get(os.path.basename(os.path.normpath(lib))) or {}).get("described", 0)
                if described:
                    text += "  ·  " + t("ai_q_described", count=described)
            elif state in ("partial", "paused"):
                text = t("ai_row_partial", count=st["count"], photos=st["photos"] or "?")
            elif state == "waiting":
                text = t("ai_row_gpu_busy") if st["phase"] == "gpu_busy" else t("ai_row_window")
            else:
                text = t("ai_row_queued")
            try:
                lbl.configure(text=text)
            except tk.TclError:
                pass

    def move(self, lib, delta):
        ai.move_in_queue(self.app.settings, lib, delta)
        save_settings(self.app.settings)
        self.fill_queue()

    def unqueue(self, lib):
        name = os.path.basename(os.path.normpath(lib))
        if Dialog.ask(self, t("ai_title"), t("ai_unqueue_q", library=name), t("ai_unqueue_body"), level="warn",
                      buttons=[("no", t("btn_cancel"), True), ("yes", t("ai_unqueue"), False)], default="no") != "yes":
            return
        ai.remove_from_queue(self.app.settings, lib)
        save_settings(self.app.settings)
        self.fill_queue()

    def update_status(self):
        folder = self.folder()
        if self.mode in ("install", "change"):
            p = ai.install_progress(folder)
            text = t("ai_status_not_installed") if self.mode == "install" else t("ai_status_change")
            if p.get("error") and p.get("error") != "cancelled":
                text = t("ai_status_install_error", error=p["error"])
            self.status.configure(text=text)
            return
        if self.mode == "installing":
            p = ai.install_progress(folder)
            self.status.configure(text=t("ai_status_installing", model=self.model_name(p.get("model"))))
            self.bar["value"] = float(p.get("total") or 0) * 1000
            gb = f"  ·  {p['got_gb']:.1f} / {p['total_gb']:.1f} GB" if p.get("got_gb") is not None else ""
            self.detail.configure(text=t("ai_step_" + str(p.get("step") or "uv")) + gb +
                                  f"  ·  {float(p.get('total') or 0) * 100:.0f}%")
            return
        info = ai.installed_info(folder) or {}
        model = self.cfg().get("model") or info.get("model")
        lines = [t("ai_status_ready", model=self.model_name(model), device=info.get("gpu") or "CPU")]
        st = ai.worker_status(folder)
        phase = st.get("phase")
        if self.cfg().get("paused"):
            lines.append(t("tray_ai_paused"))
        if not self.cfg().get("enabled"):
            lines = [t("ai_status_off")]
        elif st.get("running") and phase in ("previews", "embeddings", "descriptions", "phone_model", "loading",
                                             "starting"):
            step = f"{t('ai_step_of', n=st['step'], tot=st['steps'])}  ·  "                 if st.get("step") and (st.get("steps") or 0) > 1 else ""
            lines.append(step + t("ai_phase_" + phase, library=st.get("library") or "", done=st.get("done") or 0,
                                  total=st.get("total") or 0))
        elif phase == "error":
            lines.append(t("ai_status_error", error=st.get("error") or ""))
        self.status.configure(text=lines[0])
        total = st.get("total") or 0
        working = bool(st.get("running") and total)
        self.bar["value"] = (st.get("done") or 0) / total * 1000 if working else 0
        if working and not self.bar.winfo_ismapped():
            self.bar.pack(fill="x", pady=(14, 0), before=self.detail)
        elif not working and self.bar.winfo_ismapped():
            self.bar.pack_forget()
        if not hasattr(self, "size_gb") or self.busy:
            self.size_gb = ai.folder_size_gb(folder) if not self.busy else 0  # una volta: con molte miniature è lento
        self.update_queue()
        # le righe per libreria stanno nell'elenco della coda
        self.detail.configure(text="\n".join(lines[1:] + [t("ai_where", folder=folder, gb=f"{self.size_gb:.0f}")]))

    def model_name(self, m):
        name = {"metaclip2-giant": t("ai_m_giant"), "metaclip2-huge": t("ai_m_huge"),
                "metaclip2-b16": t("ai_m_small")}.get(m, m or "")
        return name.split(" – ")[0]  # solo il nome, senza la descrizione

    def poll(self):
        if not self.winfo_exists():
            return
        wanted = self.wanted_mode()
        if not self.busy and wanted != self.mode and not (self.mode == "change" and wanted == "ready"):
            self.mode = wanted
            self.render()
        elif not self.busy:
            self.update_status()
        self.after(1500, self.poll)

    # ---- azioni

    def app_exe(self):
        return sys.executable

    def apply(self):
        c = self.choice
        if not c.valid_folder():
            Dialog.ask(self, t("ai_title"), t("w_folder_bad"), None, level="warn")
            return
        new_folder, model = os.path.normpath(c.folder.get().strip()), c.model()
        old_folder = os.path.normpath(self.folder())
        moving = self.mode == "change" and os.path.normcase(new_folder) != os.path.normcase(old_folder)
        if not moving and not c.enough_space():
            if Dialog.ask(self, t("ai_title"), t("ai_space_low"), new_folder, level="warn",
                          buttons=[("back", t("w_back"), True), ("go", t("ai_install"), False)]) != "go":
                return
        if moving:
            if os.path.exists(new_folder) and os.listdir(new_folder):
                Dialog.ask(self, t("ai_title"), t("ai_move_not_empty"), new_folder, level="warn")
                return
            if Dialog.ask(self, t("ai_title"), t("ai_move_q"), f"{old_folder}  →  {new_folder}", level="info",
                          buttons=[("no", t("w_no"), False), ("yes", t("w_yes"), True)]) != "yes":
                return
            self.busy = True
            self.status.configure(text=t("ai_moving"))
            self.configure(cursor="watch")

            def work():
                err = None
                try:
                    ai.stop_worker(old_folder)
                    import shutil
                    os.makedirs(os.path.dirname(new_folder), exist_ok=True)
                    shutil.move(old_folder, new_folder)
                except Exception as e:  # noqa: BLE001
                    err = str(e)
                self.after(0, lambda: self.after_move(err, new_folder, model))
            threading.Thread(target=work, daemon=True).start()
            return
        self.install(new_folder, model, c.describer())

    def after_move(self, err, new_folder, model):
        self.busy = False
        self.configure(cursor="")
        if err:
            Dialog.ask(self, t("ai_title"), t("ai_move_error"), err, level="error")
            return
        self.save_cfg(dir=new_folder)
        info = ai.installed_info(new_folder) or {}
        if model in (info.get("models") or [info.get("model")]):
            self.save_cfg(model=model)
            self.mode = "ready"
            self.render()
        else:
            self.install(new_folder, model, self.cfg().get("describer") if self.cfg().get("descriptions") else None)

    def install(self, folder, model, describer=None):
        self.save_cfg(dir=folder, model=model, enabled=True, phone_bits=int(self.cfg().get("phone_bits") or 8),
                      descriptions=bool(describer), describer=describer)
        try:
            ai.start_install_process(self.app_exe(), folder, model, describer)
        except OSError as e:
            Dialog.ask(self, t("ai_title"), t("ai_status_install_error", error=str(e)), None, level="error")
            return
        log(f"ai: install started ({model}) in {folder}")
        self.mode = "installing"
        self.after(800, self.render)

    def stop_install(self):
        p = ai.install_progress(self.folder())
        if ai.pid_alive(p.get("pid")):
            subprocess.run(["taskkill", "/PID", str(p["pid"]), "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ai.save_json(os.path.join(self.folder(), "installazione.json"), {**p, "error": "cancelled"})
        self.mode = None
        self.render()

    def start_change(self):
        self.mode = "change"
        self.render()

    def cancel_change(self):
        self.mode = None
        self.render()

    def remove(self):
        folder = self.folder()
        size = ai.folder_size_gb(folder)
        if Dialog.ask(self, t("ai_title"), t("ai_remove_q", gb=f"{size:.0f}"), folder, level="warn",
                      buttons=[("no", t("w_no"), True), ("yes", t("ai_remove"), False)], default="no") != "yes":
            return
        try:
            nas = ai.nas_outputs(self.app.settings)
        except Exception:  # noqa: BLE001
            nas = []
        remove_nas = bool(nas) and Dialog.ask(
            self, t("ai_title"), t("ai_remove_nas_q", count=len(nas)), "\n".join(nas), level="info",
            buttons=[("no", t("w_no"), True), ("yes", t("w_yes"), False)], default="no") == "yes"
        self.busy = True
        self.configure(cursor="watch")
        self.status.configure(text=t("ai_removing"))

        def work():
            if remove_nas:
                ai.remove_nas_outputs(self.app.settings)
            ok = ai.remove_engine(folder)
            self.after(0, lambda: self.after_remove(ok, folder))
        threading.Thread(target=work, daemon=True).start()

    def after_remove(self, ok, folder):
        self.busy = False
        self.configure(cursor="")
        self.app.settings["ai"] = {"enabled": False}
        save_settings(self.app.settings)
        log(f"ai: engine removed from {folder}")
        if not ok:
            Dialog.ask(self, t("ai_title"), t("ai_remove_partial"), folder, level="warn")
        self.mode = None
        self.render()

    def open_log(self):
        path = os.path.join(self.folder(), "ia.log")
        if os.path.isfile(path):
            os.startfile(path)
        else:
            Dialog.ask(self, t("ai_title"), t("log_empty"), None, level="info")


# ---------------------------------------------------------------- finestra principale

GREEN = "#22c55e"
TRACK = "#3c4043"
TRACK_BG = "#2b2b2b"  # come le schede


class GreenBar(tk.Canvas):
    """Barra di avanzamento verde, sottile, disegnata a mano (quella di ttk col tema scuro si vede poco).
    `set(None)` = lavoro in corso senza una percentuale: un tratto verde che scorre."""

    def __init__(self, master, height=6):
        super().__init__(master, height=height, highlightthickness=0, bd=0, bg=TRACK)
        self.value, self.offset = 0.0, 0
        self.fill = self.create_rectangle(0, 0, 0, height, fill=GREEN, width=0)
        self.bind("<Configure>", lambda e: self.draw())
        self.after(60, self.animate)

    def set(self, value):
        self.value = value
        self.draw()

    def draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if self.value is None:  # senza percentuale: un quarto di barra che scorre
            seg = max(40, w // 4)
            x = self.offset % (w + seg) - seg
            self.coords(self.fill, x, 0, x + seg, h)
        else:
            self.coords(self.fill, 0, 0, w * max(0.0, min(1.0, self.value)), h)

    def animate(self):
        if not self.winfo_exists():
            return
        if self.value is None and self.winfo_ismapped():
            self.offset += 8
            self.draw()
        self.after(60, self.animate)


def ai_progress(settings):
    """Cosa sta facendo il motore IA, per la barra in fondo alla finestra: (testo, frazione 0..1, None = senza
    percentuale, False = fermo) oppure None se non sta facendo niente. Vale per tutte le librerie, non solo per
    quella scelta."""
    folder = ai.engine_dir(settings)
    if not folder:
        return None
    if ai.installing(folder):
        p = ai.install_progress(folder)
        gb = f"  ·  {p['got_gb']:.1f} / {p['total_gb']:.1f} GB" if p.get("got_gb") is not None else ""
        frac = float(p.get("total") or 0)
        return (t("footer_ai_installing", step=t("ai_step_" + str(p.get("step") or "uv"))) + gb +
                f"  ·  {frac * 100:.0f}%", frac)
    if not ai.installed_info(folder):
        return None
    st = ai.worker_status(folder)
    phase = st.get("phase")
    if ai.config(settings).get("paused") and phase not in (None, "idle"):
        return f"{t('footer_ai')}  ·  {t('tray_ai_paused')}", False
    if phase == "gpu_busy":
        return t("tray_ai_gpu_busy"), False
    if phase == "waiting_window":
        return t("tray_ai_window"), False
    if not st.get("running") or phase in (None, "idle"):
        return None
    done, total = st.get("done") or 0, st.get("total") or 0
    if phase in ("previews", "embeddings", "descriptions", "phone_model", "loading", "starting"):
        text = t("ai_phase_" + phase, library=st.get("library") or "", done=f"{done:,}".replace(",", "."),
                 total=f"{total:,}".replace(",", "."))
    else:
        text = t("tray_ai_working")
    step, steps, plan = st.get("step") or 0, st.get("steps") or 0, st.get("plan") or []
    head = t("footer_ai") + (f"  ·  {t('ai_step_of', n=step, tot=steps)}" if step and steps > 1 else "")
    text = f"{head}  ·  {text}"
    if total and phase in ("previews", "embeddings", "descriptions"):
        text += f"  ·  {done * 100 // total}%"
    if step and step < len(plan):  # cosa viene dopo (con la libreria se è un'altra)
        kind, lib = plan[step]
        nxt = t("ai_kind_" + kind) + (f" ({lib})" if lib != st.get("library") else "")
        text += f"  ·  {t('ai_next', what=nxt)}"
    if total and phase in ("previews", "embeddings", "descriptions"):
        return text, done / total
    return text, None


def eta_seconds(st):
    """Secondi che mancano alla fase in corso del motore IA, dal ritmo tenuto da quando è cominciata."""
    done, total = st.get("done") or 0, st.get("total") or 0
    try:
        started = datetime.datetime.fromisoformat(st["phase_started"])
    except (KeyError, TypeError, ValueError):
        return None
    elapsed = (datetime.datetime.now() - started).total_seconds()
    if done < 20 or elapsed < 30 or done >= total:
        return None
    return elapsed / done * (total - done)


def short_duration(seconds):
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{max(1, m)} min"


RED = "#f87171"
BLUE = "#60a5fa"
RECENT = 3600  # per un'ora l'ultima importazione / l'ultimo aggiornamento della ricerca IA sono una "notizia"


def recent(iso):
    try:
        return (datetime.datetime.now() - datetime.datetime.fromisoformat(str(iso)[:19])).total_seconds() < RECENT
    except ValueError:
        return False


def import_lines(settings):
    """Righe dell'importazione automatica per la barra di stato: (tipo, testo, frazione, clic)."""
    if not (settings.get("watch") or {}).get("enabled"):
        return []
    st = watcher.status() or {}
    if not ai.pid_alive(st.get("pid")):
        return [("wait", t("status_import_stopped"), False, "auto")]
    try:
        import tray  # stessi testi dell'icona nell'area di notifica
        main, sub, last = tray.describe(st)
    except Exception as e:  # noqa: BLE001
        log(f"status: {e!r}")
        return []
    phase = st.get("phase", "idle")
    lines = []
    if phase in ("importing", "profiles"):
        done, total = st.get("done") or 0, st.get("total") or 0
        text = " · ".join(x for x in (main, st.get("library"), sub if sub != st.get("library") else "") if x)
        lines.append(("work", f"{text}  ·  {done * 100 // max(1, total)}%", done / total if total else None, "auto"))
    elif phase in ("incoming", "waiting", "metadata", "converting"):
        lines.append(("work", " · ".join(x for x in (main, sub) if x), None, "auto"))
    elif phase == "error":
        lines.append(("error", " · ".join(x for x in (main, sub) if x), False, "auto"))
    else:
        lines.append(("info", " · ".join(x for x in (main, sub) if x), False, "auto"))
    try:  # anteprime sviluppate per il telefono (thread del processo in background)
        import developed
        pv = watcher.load_json(developed.STATUS)
        if pv.get("running") and pv.get("pid") == st.get("pid") and pv.get("phase") == "render" and pv.get("total"):
            done, total = pv.get("done") or 0, pv["total"]
            lines.append(("work", t("previews_line", library=pv.get("library") or "", done=done, total=total),
                          done / total, "auto"))
    except Exception as e:  # noqa: BLE001
        log(f"status previews: {e!r}")
    try:  # raw già nelle librerie convertiti in DNG (thread del processo in background)
        import dng
        dv = watcher.load_json(dng.STATUS)
        if dv.get("running") and dv.get("pid") == st.get("pid") and dv.get("total"):
            done, total = dv.get("done") or 0, dv["total"]
            lines.append(("work", t("dng_line", library=dv.get("library") or "", done=done, total=total),
                          done / total, "auto"))
    except Exception as e:  # noqa: BLE001
        log(f"status dng: {e!r}")
    try:  # ricerca dei volti (thread del processo in background)
        import faces
        fv = watcher.load_json(faces.STATUS)
        if fv.get("running") and fv.get("pid") == st.get("pid") and fv.get("total"):
            done, total = fv.get("done") or 0, fv["total"]
            lines.append(("work", t("faces_line", library=fv.get("library") or "", done=done, total=total),
                          done / total, "auto"))
    except Exception as e:  # noqa: BLE001
        log(f"status faces: {e!r}")
    if last and recent((st.get("last") or {}).get("when")):
        lines.append(("news", last, False, "auto"))
    return lines


def ai_lines(settings):
    """Righe del motore IA per la barra di stato."""
    p = ai_progress(settings)
    if p is not None:
        text, frac = p
        return [("work" if frac is not False else "wait", text, frac, "ai")]
    folder = ai.engine_dir(settings)
    if not folder or not ai.installed_info(folder) or not ai.config(settings).get("enabled"):
        return []
    st = ai.worker_status(folder)
    libs = st.get("libraries") or {}
    count = sum(int(v.get("count") or 0) for v in libs.values())
    lines = []
    if st.get("phase") == "error":
        lines.append(("error", f"{t('footer_ai')}  ·  {t('ai_status_error', error=st.get('error') or '')}", False,
                      "ai"))
    elif count:
        lines.append(("info", t("status_ai_ready", count=f"{count:,}".replace(",", ".")), False, "ai"))
    last = st.get("last_run") or {}
    if last and recent(last.get("id")):
        news = [t("tray_ai_done_line", count=n, library=k) for k, n in (last.get("libraries") or {}).items()]
        news += [t("tray_ai_desc_line", count=n, library=k) for k, n in (last.get("described") or {}).items()]
        if news:
            lines.append(("news", f"{t('tray_ai_done_title')}: " + "; ".join(news) + f" ({last['id'][11:16]})",
                          False, "ai"))
    return lines


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self.title(APP_NAME)
        self.geometry("1440x820")
        self.minsize(1000, 620)
        set_icon(self)
        setup_theme(self)
        self.settings = load_settings()
        self.settings.setdefault("libraries", [])
        i18n.set_language(self.settings.get("language") or registry_value("Language"))
        self.items, self.reason, self.release = [], None, None
        self.info = {}
        self.previews = {}
        self.finder = cs.ProfileFinder(self.settings.get("profiles_dir"))
        self.loading = 0  # numero dell'ultima lettura avviata: quelle superate si ignorano
        self._build()
        dark_title_bar(self)
        self.deiconify()
        self.after(150, self.refresh)
        if not self.settings.get("setup_done"):
            if self.settings.get("libraries"):  # già configurata da una versione precedente: niente procedura guidata
                self.settings["setup_done"] = True
                save_settings(self.settings)
            else:
                self.after(400, self.open_wizard)
        if self.settings.get("check_updates", True):
            self.after(2500, self.check_updates)

    def _build(self):
        self.outer = outer = ttk.Frame(self, padding=(24, 20, 24, 18))
        outer.pack(fill="both", expand=True)

        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Button(head, text="⚙  " + t("settings"), command=self.open_settings).pack(side="right", anchor="n")
        # il motore IA (installazione, modello, descrizioni, coda delle librerie, orari): accanto alle impostazioni
        ttk.Button(head, text="◈  " + t("ai_button"), command=self.open_ai).pack(side="right", anchor="n", padx=(0, 8))
        ttk.Label(head, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(head, text=t("subtitle"), style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(head, text="ⓘ  " + t("supported_note"), style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        # libreria e foto da esaminare
        card = ttk.Frame(outer, style="Card.TFrame", padding=(16, 12))
        card.pack(fill="x", pady=(16, 0))
        top = ttk.Frame(card)
        top.pack(fill="x")
        self.btn_refresh = ttk.Button(top, text="↻  " + t("refresh"), command=self.refresh)
        self.btn_refresh.pack(side="right", anchor="n")
        self.btn_remove = ttk.Button(top, text=t("remove_library"), command=self.remove_library)
        self.btn_remove.pack(side="right", anchor="n", padx=(0, 8))
        ttk.Button(top, text="＋  " + t("add_library"), command=self.add_library).pack(side="right", anchor="n",
                                                                                     padx=(0, 8))
        info = ttk.Frame(top)
        info.pack(side="left", fill="x", expand=True)
        ttk.Label(info, text=t("library"), style="Muted.TLabel").pack(anchor="w")
        self.lib_combo = ttk.Combobox(info, state="readonly", width=48, font=self.f_strong)
        self.lib_combo.pack(anchor="w", pady=(4, 2))
        self.lib_combo.bind("<<ComboboxSelected>>", self.on_library_selected)
        self.lib_path = ttk.Label(info, style="Muted.TLabel")
        self.lib_path.pack(anchor="w")
        # ricerca per soggetti sul telefono (motore IA facoltativo): stato della libreria scelta e pulsante
        ai_row = ttk.Frame(info)
        ai_row.pack(anchor="w", pady=(6, 0))
        self.ai_state = ttk.Label(ai_row, style="Muted.TLabel")
        self.ai_state.pack(side="left")
        self.btn_ai = ttk.Button(ai_row, command=self.ai_action)
        self.btn_ai.pack(side="left", padx=(10, 0))
        self.ai_action_kind = None
        # quali foto esaminare: una riga a parte, così i pulsanti della libreria hanno sempre spazio
        scope = ttk.Frame(card)
        scope.pack(fill="x", pady=(10, 0))
        ttk.Label(scope, text=t("scope_label"), style="Muted.TLabel").pack(side="left", padx=(0, 14))
        # tutta la libreria, l'ultima importazione, o da un'importazione in poi (il menu si riempie a ogni lettura)
        self.scope = self.settings.get("scope", "last")
        if self.scope == "recent":  # versioni precedenti: "aggiunte di recente" (30 giorni, come LightCraft)
            self.scope = "last"
        self.scope_values = []
        self.scope_combo = ttk.Combobox(scope, state="readonly", width=58)
        self.scope_combo.pack(side="left")
        self.scope_combo.bind("<<ComboboxSelected>>", self.on_scope_changed)
        ttk.Button(scope, text=t("adv_button"), command=self.open_advanced).pack(side="left", padx=(10, 0))
        self.fill_scope(None)
        self.update_library_label()
        self.after(4000, lambda: self.update_ai_row(again=True))

        self.banner = tk.Frame(outer, highlightthickness=0, bd=0)
        self.banner.pack(fill="x", pady=(12, 0))
        self.banner_bar = tk.Frame(self.banner, width=4)
        self.banner_bar.pack(side="left", fill="y")
        self.banner_text = tk.Label(self.banner, anchor="w", justify="left", font=self.f_body, padx=12, pady=9,
                                    fg=TEXT)
        self.banner_text.pack(side="left", fill="x", expand=True)
        self.banner.bind("<Configure>", lambda e: self.banner_text.configure(wraplength=max(200, e.width - 40)))

        stats = ttk.Frame(outer)
        stats.pack(fill="x", pady=(12, 0))
        self.stat_vars = {}
        for i, key in enumerate(("checked", "apply", "ok", "other")):
            box = ttk.Frame(stats, style="Card.TFrame", padding=(16, 10))
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            stats.columnconfigure(i, weight=1)
            ttk.Label(box, text=t("stat_" + key).upper(), style="Muted.TLabel").pack(anchor="w")
            var = tk.StringVar(value="–")
            ttk.Label(box, textvariable=var, style="Stat.TLabel").pack(anchor="w")
            self.stat_vars[key] = var

        # barra di stato, sempre in fondo: importazione automatica e motore IA (anche quando lavorano con l'app
        # chiusa), notizie recenti e avanzamenti con la barra verde. Un clic su una riga apre la sua finestra.
        self.status_zone = tk.Frame(outer, bg=TRACK_BG)
        self.status_zone.pack(side="bottom", fill="x", pady=(12, 0))
        top_zone = tk.Frame(self.status_zone, bg=TRACK_BG)
        top_zone.pack(fill="x", padx=12, pady=(6, 5))
        # a destra, solo quando servono: pausa del motore IA e i dettagli del lavoro in corso
        ctrl = tk.Frame(top_zone, bg=TRACK_BG)
        ctrl.pack(side="right", anchor="n")
        self.btn_details = ttk.Button(ctrl, command=self.toggle_details)
        self.btn_pause = ttk.Button(ctrl, command=self.toggle_ai_pause)
        self.details_open = False
        rows = tk.Frame(top_zone, bg=TRACK_BG)
        rows.pack(side="left", fill="x", expand=True)
        self.status_rows = []
        for _ in range(4):
            line = tk.Frame(rows, bg=TRACK_BG)
            dot = tk.Label(line, text="●", bg=TRACK_BG, font=self.f_body)
            dot.pack(side="left")
            text = tk.Label(line, anchor="w", bg=TRACK_BG, font=self.f_body)
            text.pack(side="left", padx=(6, 0), fill="x", expand=True)
            self.status_rows.append((line, dot, text))
        # dettagli: i passi del giro del motore IA (fatti, in corso con il tempo che manca, da fare)
        self.details = tk.Frame(self.status_zone, bg=TRACK_BG)
        self.detail_rows = []
        self.status_bar = GreenBar(self.status_zone, height=4)
        self.status_bar.pack(fill="x", side="bottom")
        foot = ttk.Frame(outer)
        foot.pack(side="bottom", fill="x", pady=(14, 0))
        self.after(800, self.update_status_zone)
        ttk.Button(foot, text=t("open_log"), command=self.open_log).pack(side="left")
        ttk.Button(foot, text=t("backups_btn"), command=self.open_backups).pack(side="left", padx=(8, 0))
        auto_on = (self.settings.get("watch") or {}).get("enabled")
        self.btn_auto = ttk.Button(foot, text=("● " if auto_on else "") + t("auto_btn"), command=self.open_auto_import)
        self.btn_auto.pack(side="left", padx=(8, 0))
        ttk.Label(foot, text=f"v{VERSION}", style="Muted.TLabel").pack(side="left", padx=(12, 0))
        self.update_link = ttk.Label(foot, style="Link.TLabel", cursor="hand2")
        self.update_link.bind("<Button-1>", lambda e: self.offer_update())
        if self.release:
            self.show_update_link()
        self.go = ttk.Button(foot, text=t("go"), style="Accent.TButton", command=self.confirm)
        self.go.pack(side="right")
        self.btn_maps = ttk.Button(foot, text=t("mappings_btn"), command=self.open_mappings)
        self.btn_maps.pack(side="right", padx=(0, 8))
        self.progress = ttk.Progressbar(foot, mode="indeterminate", length=160)
        self.go.state(["disabled"])

        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(14, 0))
        listf = ttk.Frame(panes)
        legend = ttk.Frame(listf)
        legend.pack(side="top", fill="x", pady=(0, 6))
        for kind, key in LEGEND:
            bg = ROW_COLORS[kind][1] if kind in ROW_COLORS else ttk.Style(self).lookup("Treeview", "background") or "#1c1c1c"
            tk.Label(legend, text="    ", bg=bg, bd=0, highlightthickness=1, highlightbackground="#5f6368").pack(
                side="left", padx=(0, 6))
            ttk.Label(legend, text=t(key), style="Muted.TLabel").pack(side="left", padx=(0, 14))
        self.btn_columns = ttk.Button(legend, text="☰  " + t("columns_btn"), command=self.columns_menu_at_button)
        self.btn_columns.pack(side="right")
        ttk.Label(legend, text=t("legend_hint"), style="Muted.TLabel").pack(side="right", padx=(0, 12))
        box = ttk.Frame(listf)
        box.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(box, columns=COLUMN_IDS, show="headings", selectmode="extended")
        for c, _key, w, stretch in COLUMNS:
            self.tree.heading(c, anchor="w", command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=w, minwidth=60, anchor="w", stretch=stretch)
        self.tree.configure(displaycolumns=self.visible_columns())
        self.sort = tuple(self.settings.get("sort", ("captured", False)))
        self.update_headings()
        for kind, (fg, bg) in ROW_COLORS.items():
            self.tree.tag_configure(kind, foreground=fg, background=bg)
        self.tree.tag_configure("none", foreground=MUTED)
        attach_scrollbars(self.tree)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Button-3>", self.right_click)
        self.tree.bind("<ButtonPress-1>", self.heading_press, add="+")
        self.tree.bind("<B1-Motion>", self.heading_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self.heading_release, add="+")
        self.tree.bind("<App>", self.row_menu)
        self.tree.bind("<Double-1>", lambda e: self.open_large())
        self.tree.bind("<Return>", lambda e: self.open_large())
        panes.add(listf, weight=3)

        prev = ttk.Frame(panes, style="Card.TFrame", padding=12)
        card_bg = ttk.Style(self).lookup("Card.TFrame", "background") or "#2b2b2b"
        self.preview_img = tk.Label(prev, bg=card_bg, fg=MUTED, font=self.f_small, cursor="hand2",
                                    text=t("preview_hint"))
        self.preview_img.pack(fill="both", expand=True)
        self.preview_img.bind("<Configure>", lambda e: self.render_preview())
        self.preview_img.bind("<Button-1>", lambda e: self.open_large())
        self.preview_name = ttk.Label(prev, style="Strong.TLabel")
        self.preview_name.pack(anchor="w", pady=(10, 0))
        self.preview_info = ttk.Label(prev, style="Muted.TLabel", justify="left")
        self.preview_info.pack(anchor="w", pady=(2, 0))
        panes.add(prev, weight=2)
        self.after(60, lambda: panes.sashpos(0, int(panes.winfo_width() * 0.71)))

        self.rows = {}  # riga -> foto
        self.current = None
        self._tk_img = None

    # ---- stato

    def set_status(self, level, text):
        bg, accent = LEVELS[level]
        self.banner.configure(bg=bg)
        self.banner_bar.configure(bg=accent)
        self.banner_text.configure(bg=bg, text=text)

    def set_busy(self, busy, text=None):
        if busy:
            self.go.state(["disabled"])
            self.btn_maps.state(["disabled"])
            self.btn_refresh.state(["disabled"])
            self.lib_combo.state(["disabled"])
            self.progress.pack(side="right", padx=(0, 12))
            self.progress.start(12)
            self.config(cursor="watch")
            if text:
                self.set_status("info", text)
        else:
            self.progress.stop()
            self.progress.pack_forget()
            self.btn_refresh.state(["!disabled"])
            self.btn_maps.state(["!disabled"])
            self.lib_combo.state(["!disabled", "readonly"])
            self.config(cursor="")

    # ---- librerie

    def library_names(self):
        libs = self.settings.get("libraries", [])
        base = [os.path.basename(p) or p for p in libs]
        names = []
        for path, name in zip(libs, base):
            if base.count(name) > 1:
                name = f"{name}  ({os.path.basename(os.path.dirname(path))})"
            names.append(name if is_library(path) else "⚠  " + name)
        return names

    def update_library_label(self):
        libs = self.settings.get("libraries", [])
        lib = self.settings.get("library") or ""
        self.lib_combo.configure(values=self.library_names())
        if lib in libs:
            self.lib_combo.current(libs.index(lib))
        else:
            self.lib_combo.set(t("no_library"))
        self.lib_path.configure(text=lib or t("no_library_hint"))
        self.btn_remove.state(["!disabled"] if lib in libs else ["disabled"])
        self.update_ai_row()

    # ---- ricerca IA della libreria scelta

    def update_ai_row(self, again=False):
        """Stato della ricerca IA per la libreria scelta e il pulsante giusto (si aggiorna ogni pochi secondi)."""
        try:
            lib = self.settings.get("library")
            folder = ai.engine_dir(self.settings)
            # qui solo il riepilogo della libreria scelta (l'avanzamento è nella barra di stato, le impostazioni nel
            # pulsante Motore IA in alto); l'unico pulsante è Analizza, per una libreria mai analizzata
            if not lib:
                text, kind = "", None
            elif ai.installing(folder):
                text, kind = t("ai_short_installing"), None
            elif not ai.installed_info(folder):
                text, kind = t("ai_row_none"), None
            else:
                st = ai.library_state(self.settings, lib)
                state = st["state"]
                if state == "none":
                    text, kind = t("ai_row_not_analyzed"), "analyze"
                elif not st["count"]:
                    text, kind = t("ai_row_queued"), None
                else:
                    n = f"{st['count']:,}".replace(",", ".")
                    text = t("ai_row_done", count=n) if st["count"] >= (st["photos"] or 0) else \
                        t("ai_row_partial", count=n, photos=f"{st['photos']:,}".replace(",", "."))
                    if ai.describer_ready(self.settings) or st.get("described"):
                        text += "  ·  " + t("ai_q_described", count=f"{st.get('described') or 0:,}".replace(",", "."))
                    kind = None
                text = t("ai_row_label") + " " + text if not text.startswith(t("ai_row_label")) else text
                if not ai.config(self.settings).get("enabled") and state != "none":
                    text, kind = t("ai_short_off"), None
            self.ai_state.configure(text=text)
            if kind is None:
                self.btn_ai.pack_forget()
            else:
                self.btn_ai.configure(text=t("ai_analyze"))
                if not self.btn_ai.winfo_ismapped():
                    self.btn_ai.pack(side="left", padx=(10, 0))
            self.ai_action_kind = kind
        except Exception as e:  # noqa: BLE001 — la riga della ricerca IA non deve mai bloccare la finestra
            log(f"ai row: {e!r}")
        if again:
            self.after(4000, lambda: self.update_ai_row(again=True))

    def ai_action(self):
        if self.ai_action_kind == "analyze":
            self.analyze_ai()

    def update_status_zone(self):
        """Barra di stato: prima chi sta lavorando, poi errori e attese, le notizie dell'ultima ora e lo stato a
        riposo. La barra verde segue il primo lavoro con una percentuale (o scorre se non ce l'ha)."""
        try:
            lines = import_lines(self.settings) + ai_lines(self.settings)
            order = {"work": 0, "error": 1, "wait": 2, "news": 3, "info": 4}
            lines.sort(key=lambda x: order[x[0]])
            if not lines:
                lines = [("info", t("status_ready"), False, None)]
            colors = {"work": (GREEN, TEXT), "error": (RED, TEXT), "wait": (MUTED, MUTED), "news": (BLUE, TEXT),
                      "info": (MUTED, MUTED)}
            for i, (line, dot, text) in enumerate(self.status_rows):
                if i < len(lines):
                    kind, msg = lines[i][0], lines[i][1]
                    dot.configure(fg=colors[kind][0])
                    text.configure(text=msg, fg=colors[kind][1])
                    if not line.winfo_ismapped():
                        line.pack(fill="x", pady=1)
                elif line.winfo_ismapped():
                    line.pack_forget()
            work = [x for x in lines if x[0] == "work"]
            if work:
                frac = next((x[2] for x in work if isinstance(x[2], float)), None)
                self.status_bar.set(frac)
            else:
                self.status_bar.set(0.0)
            self.update_ai_controls()
        except Exception as e:  # noqa: BLE001 — la barra di stato non deve mai bloccare la finestra
            log(f"status zone: {e!r}")
        self.after(2000, self.update_status_zone)

    def update_ai_controls(self):
        """Pausa/Riprendi e Dettagli nella barra di stato: solo mentre il motore IA ha un giro in corso o in pausa."""
        folder = ai.engine_dir(self.settings)
        cfg = ai.config(self.settings)
        st = ai.worker_status(folder) if folder and ai.installed_info(folder) else {}
        paused = bool(cfg.get("paused"))
        busy = bool(st) and cfg.get("enabled") and (st.get("running") or paused) and \
            st.get("phase") not in (None, "idle")
        if busy:
            self.btn_pause.configure(text=("▶  " + t("ai_resume")) if paused else ("⏸  " + t("ai_pause")))
            if not self.btn_pause.winfo_ismapped():
                self.btn_pause.pack(side="right")
        elif self.btn_pause.winfo_ismapped():
            self.btn_pause.pack_forget()
        plan = (st.get("plan") or []) if busy else []
        if plan:
            self.btn_details.configure(text=("▴  " if self.details_open else "▾  ") + t("status_details"))
            if not self.btn_details.winfo_ismapped():
                self.btn_details.pack(side="right", padx=(0, 8))
        elif self.btn_details.winfo_ismapped():
            self.btn_details.pack_forget()
        show = bool(plan) and self.details_open
        if show:
            self.fill_details(st, plan)
            if not self.details.winfo_ismapped():
                self.details.pack(fill="x", padx=(32, 12), pady=(0, 6), before=self.status_bar)
        elif self.details.winfo_ismapped():
            self.details.pack_forget()

    def fill_details(self, st, plan):
        """Una riga per passo: ✓ fatto, ● in corso (quante, percentuale, tempo che manca), ○ da fare."""
        step = st.get("step") or 0
        while len(self.detail_rows) < len(plan):
            row = tk.Label(self.details, anchor="w", bg=TRACK_BG, font=self.f_body, justify="left")
            self.detail_rows.append(row)
        for i, row in enumerate(self.detail_rows):
            if i >= len(plan):
                row.pack_forget()
                continue
            kind, lib = plan[i]
            label = f"{i + 1}   {lib}  ·  {t('ai_kind_' + kind)}"
            if i + 1 < step:
                mark, color = "✓", MUTED
            elif i + 1 == step:
                mark, color = "●", TEXT
                done, total = st.get("done") or 0, st.get("total") or 0
                if total and st.get("phase") == kind:
                    label += f"   {done:,} / {total:,}".replace(",", ".") + f"  ·  {done * 100 // total}%"
                    left = eta_seconds(st)
                    if left:
                        label += "  ·  " + t("status_left", time=short_duration(left))
                elif st.get("phase") == "loading":
                    label += "   " + t("ai_phase_loading")
            else:
                mark, color = "○", MUTED
            row.configure(text=f"{mark}  {label}", fg=GREEN if mark == "●" else color)
            if not row.winfo_ismapped():
                row.pack(fill="x")

    def toggle_details(self):
        self.details_open = not self.details_open
        self.update_ai_controls()

    def toggle_ai_pause(self):
        paused = not ai.config(self.settings).get("paused")
        ai.set_paused(self.settings, paused)
        save_settings(self.settings)
        if not paused:
            ai.maybe_start(self.settings, log)
        self.update_ai_controls()
        self.update_ai_row()

    def open_ai(self):
        d = AiDialog(self, self)
        self.wait_window(d)
        self.update_ai_row()

    def analyze_ai(self):
        """Prima analisi della libreria scelta: la decide l'utente (è lunga); dopo, le foto nuove vanno da sole."""
        s = self.settings
        lib = s.get("library")
        if not lib:
            return
        if not ai.installed_info(ai.engine_dir(s)):
            self.open_ai()
            return
        ph = s.get("phone") or {}
        if not (ph.get("folder") or "").strip():
            Dialog.ask(self, t("ai_title"), t("ai_need_phone"), None, level="info")
            self.open_auto_import()
            return
        name = os.path.basename(os.path.normpath(lib))
        if not any(ai.same(lib, x) for x in ph.get("libraries") or []):
            if Dialog.ask(self, t("ai_title"), t("ai_add_phone_q", library=name), None, level="info",
                          buttons=[("no", t("btn_cancel"), False), ("yes", t("ai_analyze"), True)]) != "yes":
                return
        self.config(cursor="watch")
        self.update_idletasks()
        photos = ai.library_photo_count(lib)
        self.config(cursor="")
        if photos is None:
            Dialog.ask(self, t("ai_title"), t("st_read_error"), lib, level="error")
            return
        minutes = ai.estimate_minutes(s, photos, ai.detect_gpu())
        time_text = t("ai_eta_h", h=f"{minutes / 60:.1f}".replace(".", ",")) if minutes >= 90 else \
            t("ai_eta_m", m=max(1, round(minutes)))
        window = ai.config(s).get("window") or {}
        later = window.get("enabled") and not ai.in_window({"from": window.get("from") or "22:00",
                                                             "to": window.get("to") or "07:00"})
        body = t("ai_analyze_body", photos=f"{photos:,}".replace(",", "."), time=time_text)
        if later:
            body += "\n\n" + t("ai_analyze_later", start=window.get("from") or "22:00")
        if Dialog.ask(self, t("ai_title"), t("ai_analyze_q", library=name), body, level="info",
                      buttons=[("no", t("btn_cancel"), False), ("yes", t("ai_analyze"), True)]) != "yes":
            return
        libs = ph.get("libraries") or []
        if not any(ai.same(lib, x) for x in libs):
            s["phone"] = {**ph, "libraries": [*libs, lib]}
        ai.add_to_queue(s, lib)
        s["ai"] = {**ai.config(s), "enabled": True}
        if ai.config(s).get("paused"):
            ai.set_paused(s, False)
        w = s.get("watch") or {}
        if not w.get("enabled"):  # le foto nuove si analizzano dal processo in background: va acceso
            s["watch"] = {**w, "enabled": True}
            save_settings(s)
            try:
                autostart.enable()
            except Exception as e:  # noqa: BLE001
                log(f"ERROR automatic import: {e}")
        save_settings(s)
        self.btn_auto.configure(text=("● " if (s.get("watch") or {}).get("enabled") else "") + t("auto_btn"))
        log(f"ai: analysis of {lib} queued ({photos} photos)")
        ai.maybe_start(s, log)
        self.after(1500, self.update_ai_row)

    def add_library(self):
        current = self.settings.get("library")
        start = os.path.dirname(current) if current else os.path.expanduser("~")
        lib = filedialog.askdirectory(title=t("pick_library"), initialdir=start)
        if not lib:
            return
        lib = os.path.normpath(lib)
        if not is_library(lib):
            Dialog.ask(self, APP_NAME, t("pick_library"), t("not_library"), level="error")
            return
        libs = self.settings.setdefault("libraries", [])
        known = next((p for p in libs if os.path.normcase(p) == os.path.normcase(lib)), None)
        if known is None:
            libs.append(lib)
        self.switch_library(known or lib)

    def remove_library(self):
        lib = self.settings.get("library")
        libs = self.settings.get("libraries", [])
        if lib not in libs:
            return
        name = self.library_names()[libs.index(lib)].replace("⚠  ", "")
        answer = Dialog.ask(self, APP_NAME, t("remove_library_head", name=name), t("remove_library_body"),
                            level="warn", buttons=[("cancel", t("btn_cancel"), False), ("remove", t("btn_remove"), True)],
                            default="cancel")
        if answer != "remove":
            return
        index = libs.index(lib)
        libs.remove(lib)
        self.switch_library(libs[min(index, len(libs) - 1)] if libs else None)

    def on_library_selected(self, _event=None):
        libs = self.settings.get("libraries", [])
        i = self.lib_combo.current()
        if 0 <= i < len(libs) and libs[i] != self.settings.get("library"):
            self.switch_library(libs[i])

    def switch_library(self, lib):
        if lib:
            self.settings["library"] = lib
        else:
            self.settings.pop("library", None)
        save_settings(self.settings)
        self.refresh()

    MAX_BATCHES = 5  # importazioni elencate nel menu; le altre si trovano con "Filtri avanzati…"

    def fill_scope(self, batches):
        """Riempie il menu "Foto da esaminare" con le importazioni della libreria (dalla più recente)."""
        if batches is None:  # libreria non ancora letta
            self.scope_values = [self.scope]
            self.scope_combo["values"] = [t("st_reading")]
            self.scope_combo.current(0)
            return

        when = local_text

        rows = [("all", t("scope_all_n", count=sum(b["count"] for b in batches)))]
        total = 0
        for i, b in enumerate(batches[:self.MAX_BATCHES]):
            total += b["count"]
            if i == 0:
                rows.append(("last", t("scope_last", when=when(b["start"]), count=b["count"])))
            else:
                rows.append(("since:" + b["start"], t("scope_since", when=when(b["start"]), count=total)))
        values = [v for v, _label in rows]
        if self.scope not in values:
            # una scelta che non è tra le righe fisse (importazione più vecchia, filtro avanzato): resta, in fondo
            count = sum(b["count"] for b in batches if in_scope(b["start"], self.scope, batches))
            if self.scope.startswith("since:"):
                rows.append((self.scope, t("scope_since", when=when(self.scope[6:]), count=count)))
            elif self.scope.startswith("range:"):
                start, _sep, end = self.scope[6:].partition("|")
                rows.append((self.scope, t("scope_range", start=when(start) if start else "…",
                                           end=when(end) if end else "…", count=count)))
            elif self.scope.startswith("batches:"):
                rows.append((self.scope, t("scope_picked", n=len([x for x in self.scope[8:].split(";") if x]),
                                           count=count)))
            else:
                self.scope = "all" if not batches else "last"
            if self.scope not in values and self.scope not in ("all", "last"):
                values.append(self.scope)
        self.scope_values = values
        self.scope_combo["values"] = [label for _v, label in rows]
        self.scope_combo.current(values.index(self.scope))

    def open_advanced(self):
        batches = (self.info or {}).get("batches") if getattr(self, "info", None) else None
        if batches is None:
            return  # la libreria non è ancora stata letta
        d = AdvancedFilterDialog(self, batches, self.scope)
        self.wait_window(d)
        if d.result and d.result != self.scope:
            self.scope = d.result
            self.settings["scope"] = self.scope
            save_settings(self.settings)
            self.refresh()

    def on_scope_changed(self, _event=None):
        i = self.scope_combo.current()
        if i < 0 or i >= len(self.scope_values) or self.scope_values[i] == self.scope:
            return
        self.scope = self.scope_values[i]
        self.settings["scope"] = self.scope
        save_settings(self.settings)
        self.refresh()

    # ---- lettura

    def clear_list(self):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.items = []
        self.show_preview(None)
        for var in self.stat_vars.values():
            var.set("–")
        self.go.state(["disabled"])

    def refresh(self):
        self.update_library_label()
        self.clear_list()
        lib = self.settings.get("library")
        if not lib:
            self.set_status("info", t("st_choose"))
            return
        if not is_library(lib):
            self.set_status("warn", t("st_lib_gone"))
            return
        self.set_busy(True, t("st_reading"))
        self.finder = cs.ProfileFinder(self.settings.get("profiles_dir"))
        self.loading += 1
        token, scope, settings, finder = self.loading, self.scope, self.settings, self.finder

        def progress(done, total):
            self.after(0, lambda: self.loading == token and self.set_status("info", t("st_reading_n", done=done,
                                                                                    total=total)))

        def work():
            try:
                plan = matcher.build_plan(lib, scope, settings, finder, progress,
                                          locked=watcher.phone_presets(lib, settings))
                self.after(0, self.show, token, plan, library_open_reason(lib), None)
            except Exception as e:  # noqa: BLE001 — qualsiasi errore va mostrato all'utente
                self.after(0, self.show, token, None, None, e)
        threading.Thread(target=work, daemon=True).start()

    def show(self, token, plan, reason, error):
        if token != self.loading:
            return  # nel frattempo è partita un'altra lettura
        self.set_busy(False)
        if error:
            self.set_status("error", t("st_read_error", error=error))
            return
        self.items, self.reason, self.info = plan["items"], reason, plan
        self.fill_scope(plan.get("batches") or [])
        self.rows.clear()
        for item in self.items:
            row = self.tree.insert("", "end", values=self.values(item))
            self.rows[row] = item
            item["row"] = row
            self.paint(item)
        self.apply_sort()
        self.update_counts()

    def values(self, item):
        return (item["name"], os.path.dirname(item["path"]), item["camera"], when_text(item["captured"]),
                when_text(item["imported"]), style_text(item), item["current_name"], target_text(item),
                status_text(item))

    def paint(self, item):
        values = list(self.tree.item(item["row"], "values"))
        values[COLUMN_IDS.index("status")] = status_text(item)
        self.tree.item(item["row"], values=values, tags=(row_kind(item),))

    def todo(self):
        return [i for i in self.items if i["action"] == "apply" and i["status"] in ("apply", "manual")]

    def update_counts(self):
        items = self.items
        todo = self.todo()
        other = [i for i in items if i["status"] in ("nomatch", "nostyle", "missing")]
        nomatch = [i for i in items if i["status"] == "nomatch"]
        self.stat_vars["checked"].set(str(len(items)))
        self.stat_vars["apply"].set(str(len(todo)))
        self.stat_vars["ok"].set(str(sum(1 for i in items if i["status"] == "ok")))
        self.stat_vars["other"].set(str(len(other)))
        hint = (" " + t("st_nomatch_hint", count=len(nomatch), maps=t("mappings_btn"))) if nomatch else ""
        cameras = sorted({i["model"] for i in items if i["style_key"] and not self.finder.profiles_for(i["model"])})
        self.go.state(["disabled"])
        if not items:
            self.set_status("info", t("st_empty", count=self.info.get("in_scope", 0)))
        elif cameras and not todo:
            self.set_status("warn", t("st_no_profiles", camera=", ".join(cameras)))
        elif self.reason and todo:
            self.set_status("warn", t(self.reason))
        elif todo:
            self.set_status("info", t("st_ready", count=len(todo), go=t("go")) + hint)
            self.go.state(["!disabled"])
        else:
            self.set_status("ok" if not nomatch else "info", t("st_nothing") + hint)

    # ---- colonne

    def visible_columns(self):
        chosen = [c for c in (self.settings.get("columns") or DEFAULT_COLUMNS) if c in COLUMN_IDS]
        return chosen if "name" in chosen else ["name"] + chosen

    def menu(self):
        # niente voci disattivate: Windows le disegna "in rilievo", con un carattere che sembra diverso
        return tk.Menu(self, tearoff=0, bg="#2b2b2b", fg=TEXT, activebackground="#3d4a5c", activeforeground=TEXT,
                       selectcolor=TEXT, bd=0, relief="flat", font=self.f_body)

    def right_click(self, event):
        if self.tree.identify_region(event.x, event.y) in ("heading", "separator"):
            self.columns_menu(event.x_root, event.y_root, self.column_at(event.x))
        else:
            self.row_menu(event)

    def column_at(self, x):
        index = self.tree.identify_column(x)
        visible = self.visible_columns()
        try:
            return visible[int(index.lstrip("#")) - 1]
        except (ValueError, IndexError):
            return None

    def columns_menu_at_button(self):
        b = self.btn_columns
        self.columns_menu(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def columns_menu(self, x, y, clicked=None):
        menu = self.menu()
        visible = self.visible_columns()
        if clicked in visible:
            i = visible.index(clicked)
            if i > 0:
                menu.add_command(label="◀  " + t("col_move_left"), command=lambda: self.move_column(clicked, -1))
            if i < len(visible) - 1:
                menu.add_command(label=t("col_move_right") + "  ▶", command=lambda: self.move_column(clicked, 1))
            if len(visible) > 1:
                menu.add_separator()
        self._column_vars = {}
        for c, key, *_ in COLUMNS:
            if c == "name":
                continue
            var = tk.BooleanVar(value=c in visible)
            self._column_vars[c] = var
            menu.add_checkbutton(label=t(key), variable=var, command=lambda c=c: self.toggle_column(c))
        menu.add_separator()
        menu.add_command(label=t("columns_reset"), command=lambda: self.set_columns(list(DEFAULT_COLUMNS)))
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def toggle_column(self, col):
        visible = self.visible_columns()
        if self._column_vars[col].get() and col not in visible:
            before = [c for c in COLUMN_IDS[:COLUMN_IDS.index(col)] if c in visible]
            visible.insert(visible.index(before[-1]) + 1 if before else 0, col)
        elif not self._column_vars[col].get() and col in visible:
            visible.remove(col)
        self.set_columns(visible)

    def move_column(self, col, step):
        visible = self.visible_columns()
        i = visible.index(col)
        visible.insert(max(0, min(len(visible) - 1, i + step)), visible.pop(i))
        self.set_columns(visible)

    def set_columns(self, columns):
        self.settings["columns"] = columns
        save_settings(self.settings)
        self.tree.configure(displaycolumns=self.visible_columns())
        self.update_headings()

    def heading_press(self, event):
        self._drag_col = self.column_at(event.x) if self.tree.identify_region(event.x, event.y) == "heading" else None
        self._drag_x = event.x
        self._dragging = False

    def heading_motion(self, event):
        if getattr(self, "_drag_col", None) and abs(event.x - self._drag_x) > 12:
            self._dragging = True
            self.tree.configure(cursor="sb_h_double_arrow")

    def heading_release(self, event):
        col, dragging = getattr(self, "_drag_col", None), getattr(self, "_dragging", False)
        self._drag_col, self._dragging = None, False
        if not (col and dragging):
            return
        self.tree.configure(cursor="")
        target = self.column_at(min(max(event.x, 1), self.tree.winfo_width() - 1))
        visible = self.visible_columns()
        if target and target != col:
            visible.remove(col)
            visible.insert(visible.index(target) + (1 if event.x > self._drag_x else 0), col)
            self.set_columns(visible)

    def update_headings(self):
        col, reverse = self.sort
        for c, key, width, _stretch in COLUMNS:
            text = t(key) + ((" ▼" if reverse else " ▲") if c == col else "")
            need = self.f_small.measure(t(key) + " ▼") + 34
            self.tree.heading(c, text=text, anchor="w")
            self.tree.column(c, minwidth=need, width=max(width, need, self.tree.column(c, "width")))

    def sort_by(self, col):
        current, reverse = self.sort
        self.sort = (col, not reverse) if col == current else (col, False)
        self.settings["sort"] = list(self.sort)
        save_settings(self.settings)
        self.update_headings()
        self.apply_sort()

    def apply_sort(self):
        col, reverse = self.sort
        order = {"apply": 0, "manual": 1, "nomatch": 2, "ok": 3, "nostyle": 4, "missing": 5}

        def key(item):
            name = item["name"].lower()
            if col == "folder":
                return os.path.dirname(item["path"]).lower(), name
            if col == "status":
                return order.get(item["status"], 9), item["action"] != "apply", name
            value = {"name": name, "camera": item["camera"].lower(), "captured": item["captured"],
                     "imported": item["imported"], "style": style_text(item).lower(),
                     "current": item["current_name"].lower(), "target": (item["target"] or "").lower()}[col]
            return value, item["captured"]

        for index, item in enumerate(sorted(self.items, key=key, reverse=reverse)):
            self.tree.move(item["row"], "", index)

    # ---- scelte per foto

    def selected_items(self):
        return [self.rows[r] for r in self.tree.selection() if r in self.rows]

    def row_menu(self, event):
        if not self.rows or self.progress.winfo_ismapped():
            return
        row = self.tree.identify_row(event.y) if event.y else self.tree.focus()
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        self.tree.focus(row)
        items = self.selected_items()
        menu = self.menu()
        if any(i["status"] in ("apply", "manual") for i in items):
            menu.add_command(label=t("menu_apply"), command=lambda: self.set_action("apply"))
            menu.add_command(label=t("menu_skip"), command=lambda: self.set_action("skip"))
            menu.add_separator()
        styled = next((i for i in items if i["style_key"]), None)
        mine = self.profile_targets(items)
        if mine:
            label = t("menu_photo_profile") if len(mine) == 1 else t("menu_photos_profile", count=len(mine))
            menu.add_command(label=label, command=lambda: self.open_photo_profile(mine))
        if styled:
            menu.add_command(label=t("menu_mapping"), command=lambda: self.open_mappings(styled["style_key"]))
        if mine or styled:
            menu.add_separator()
        path = self.rows[row]["path"]
        menu.add_command(label=t("menu_explorer"), command=lambda: self.show_in_explorer(path))
        x = event.x_root if event.x_root else self.tree.winfo_rootx() + 40
        y = event.y_root if event.y_root else self.tree.winfo_rooty() + 40
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def set_action(self, action):
        lib = self.settings.get("library", "")
        choices = self.settings.setdefault("choices", {}).setdefault(lib, {})
        for item in self.selected_items():
            if item["status"] not in ("apply", "manual"):
                continue
            item["action"] = action
            default = "apply" if item["status"] == "apply" else "skip"
            if item.get("own") and action == "apply":
                pass  # resta il profilo scelto per questa foto, che si applica
            elif action == default:
                choices.pop(str(item["id"]), None)
            else:
                choices[str(item["id"])] = action
            self.paint(item)
        if not choices:
            self.settings["choices"].pop(lib, None)
        save_settings(self.settings)
        self.update_counts()

    def show_in_explorer(self, path):
        if os.path.exists(path):
            subprocess.Popen(f'explorer /select,"{path}"')
        elif os.path.isdir(os.path.dirname(path)):
            os.startfile(os.path.dirname(path))

    # ---- applicazione

    def cli(self):
        cli = self.settings.get("cli") or DEFAULT_CLI
        if is_cli(cli):
            return cli
        Dialog.ask(self, APP_NAME, t("cli_missing_title"), t("cli_missing"), level="warn")
        cli = filedialog.askopenfilename(title=t("cli_pick"), filetypes=[("lightcraft-cli.exe", "lightcraft-cli.exe")])
        if cli and is_cli(cli):
            self.settings["cli"] = os.path.normpath(cli)
            save_settings(self.settings)
            return self.settings["cli"]
        return None

    def confirm(self):
        todo = self.todo()
        if not todo:
            return
        lib = self.settings["library"]
        reason = library_open_reason(lib)
        if reason:
            self.reason = reason
            self.update_counts()
            Dialog.ask(self, APP_NAME, t("error_title"), t(reason), level="warn")
            return
        answer = Dialog.ask(self, t("confirm_title"), t("confirm_head", count=len(todo)), t("confirm_body"),
                            level="info", buttons=[("cancel", t("btn_cancel"), False), ("go", t("btn_go"), True)],
                            default="go")
        if answer != "go":
            return
        cli = self.cli()
        if not cli:
            return
        root, kept = backup_root(lib, self.settings), self.settings.get("backups_kept", BACKUPS_KEPT)
        self.set_busy(True, t("st_preparing"))

        def progress(done, total):
            self.after(0, lambda: self.set_status("info", t("st_working", done=done, total=total)))

        def work():
            try:
                self.after(0, self.done, matcher.apply_profiles(lib, cli, todo, root, kept, progress), None)
            except Exception as e:  # noqa: BLE001
                log(f"ERROR: {e}")
                self.after(0, self.done, None, e)
        threading.Thread(target=work, daemon=True).start()

    def done(self, result, error):
        self.set_busy(False)
        if error:
            message = t(str(error)) if str(error) in TEXTS else str(error)
            Dialog.ask(self, APP_NAME, t("error_title"), message, level="error")
        else:
            sections = []
            if result["failed"]:
                sections.append(t("done_failed") + "\n" + "\n".join(result["failed"][:20]))
            sections.append(t("done_backup") + "\n" + result["backup"])
            Dialog.ask(self, APP_NAME, t("done_warn_title") if result["failed"] else t("done_title"),
                       t("done_body", done=result["done"]), level="warn" if result["failed"] else "ok",
                       detail="\n\n".join(sections))
            # le scelte "applica comunque" sono state usate: non servono più
            choices = self.settings.get("choices", {}).get(self.settings.get("library", ""), {})
            for pid in result["done_ids"]:
                choices.pop(str(pid), None)
            save_settings(self.settings)
        self.refresh()

    # ---- finestre

    def open_settings(self):
        d = SettingsDialog(self)
        self.wait_window(d)
        if d.saved:
            before = i18n.LANG
            i18n.set_language(self.settings.get("language"))
            if i18n.LANG != before:
                self.outer.destroy()
                self._build()
            self.refresh()

    def profile_targets(self, items):
        """Le foto selezionate a cui si può scegliere un profilo: con uno stile letto, non bloccate da un preset del
        telefono, della stessa fotocamera della prima (i profili sono per fotocamera)."""
        ok = [i for i in items if i["style_key"] and i["model"] and i["status"] != "phone"
              and self.finder.profiles_for(i["model"])]
        return [i for i in ok if i["model"] == ok[0]["model"]] if ok else []

    def open_photo_profile(self, items):
        d = PhotoProfileDialog(self, items)
        self.wait_window(d)
        if d.saved:
            self.refresh()

    def open_mappings(self, focus_key=None):
        d = MappingsDialog(self, focus_key)
        self.wait_window(d)
        if d.saved:
            self.refresh()

    def open_backups(self):
        self.wait_window(BackupsDialog(self))

    def open_wizard(self):
        d = SetupWizard(self)
        self.wait_window(d)
        if not d.done:
            return
        self.finder = cs.ProfileFinder(self.settings.get("profiles_dir"))
        on = (self.settings.get("watch") or {}).get("enabled")
        self.btn_auto.configure(text=("● " if on else "") + t("auto_btn"))
        self.refresh()
        if d.open_ai_after.get():
            self.open_ai()

    def open_auto_import(self):
        d = AutoImportDialog(self)
        self.wait_window(d)
        if d.saved:
            on = (self.settings.get("watch") or {}).get("enabled")
            self.btn_auto.configure(text=("● " if on else "") + t("auto_btn"))

    def open_log(self):
        if os.path.exists(LOG):
            os.startfile(LOG)
        else:
            Dialog.ask(self, APP_NAME, t("open_log"), t("log_empty"))

    # ---- aggiornamenti

    def check_updates(self, manual=False, parent=None):
        def work():
            try:
                release, error = updater.latest_release(), None
            except Exception as e:  # noqa: BLE001 — rete assente, GitHub non raggiungibile…
                release, error = None, e
            self.after(0, self._update_checked, release, error, manual, parent)
        threading.Thread(target=work, daemon=True).start()

    def _update_checked(self, release, error, manual, parent):
        if not error:
            self.settings["last_update_check"] = time.time()
            save_settings(self.settings)
        if release and updater.is_newer(release["version"]):
            self.release = release
            self.show_update_link()
            if manual:
                self.offer_update(parent)
        elif manual:
            owner = parent if parent is not None and parent.winfo_exists() else self
            if error:
                Dialog.ask(owner, t("updates_label"), t("upd_check_failed", error=error), None, level="warn")
            else:
                Dialog.ask(owner, t("updates_label"), t("upd_none", version=VERSION), None, level="ok")

    def show_update_link(self):
        self.update_link.configure(text="⬆ " + t("upd_link", version=self.release["version"]))
        self.update_link.pack(side="left", padx=(10, 0))

    def offer_update(self, parent=None):
        rel = self.release
        if not rel:
            return
        owner = parent if parent is not None and parent.winfo_exists() else self
        notes = "\n".join(rel["notes"].splitlines()[:14]) or None
        answer = Dialog.ask(owner, t("upd_title"), t("upd_head", version=rel["version"]),
                            t("upd_body", current=VERSION), level="info", detail=notes,
                            buttons=[("later", t("btn_later"), False), ("update", t("btn_update_now"), True)])
        if answer != "update":
            return
        if isinstance(owner, tk.Toplevel):
            owner.destroy()
        self.set_busy(True, t("upd_downloading", percent=0))

        def progress(done, total):
            percent = int(done * 100 / total) if total else 0
            self.after(0, lambda: self.set_status("info", t("upd_downloading", percent=percent)))

        def work():
            try:
                self.after(0, self._update_downloaded, updater.download(rel, progress), None)
            except Exception as e:  # noqa: BLE001
                self.after(0, self._update_downloaded, None, e)
        threading.Thread(target=work, daemon=True).start()

    def _update_downloaded(self, path, error):
        self.set_busy(False)
        if error:
            Dialog.ask(self, t("upd_title"), t("upd_error", error=error), None, level="error")
            self.refresh()
            return
        log(f"update {VERSION} -> {self.release['version']}: {path}")
        updater.launch_installer(path)
        self.destroy()

    # ---- anteprima

    def on_select(self, _event=None):
        row = self.tree.focus() if self.tree.focus() in self.tree.selection() else \
            (self.tree.selection() or [None])[0]
        self.show_preview(self.rows.get(row) if row else None)

    def show_preview(self, item):
        self.current = item
        if item is None:
            self.preview_name.configure(text="")
            self.preview_info.configure(text="")
            self._tk_img = None
            self.preview_img.configure(image="", text=t("preview_hint"))
            return
        self.preview_name.configure(text=item["name"])
        lines = []
        if item.get("captured"):
            try:
                when = datetime.datetime.fromisoformat(item["captured"][:19])
                lines.append(t("taken", date=when.strftime(DATE_FORMATS.get(i18n.LANG, "%d/%m/%Y")),
                               time=when.strftime("%H:%M")))
            except ValueError:
                pass
        lines.append(" · ".join(x for x in (item["camera"], style_text(item) if item["style_key"] else "") if x))
        if item["target"]:
            lines.append(f"{item['current_name']}  →  {item['target']}" if item["status"] != "ok" else item["target"])
        lines.append(t("preview_camera"))
        self.preview_info.configure(text="\n".join(lines))
        path = item["path"]
        if not os.path.exists(path):
            self._tk_img = None
            self.preview_img.configure(image="", text=t("preview_none"))
            return
        if path in self.previews:
            self.render_preview()
            return
        self._tk_img = None
        self.preview_img.configure(image="", text=t("preview_loading"))

        def work():
            try:
                im = load_preview(path)
            except Exception:  # noqa: BLE001
                im = None
            self.after(0, self._preview_loaded, path, im)
        threading.Thread(target=work, daemon=True).start()

    def _preview_loaded(self, path, im):
        self.previews[path] = im
        if self.current and self.current["path"] == path:
            self.render_preview()

    def render_preview(self):
        item = self.current
        im = self.previews.get(item["path"]) if item else None
        if im is None:
            if item and item["path"] in self.previews:
                self.preview_img.configure(image="", text=t("preview_none"))
            return
        w, h = max(50, self.preview_img.winfo_width() - 8), max(50, self.preview_img.winfo_height() - 8)
        fit = im.copy()
        fit.thumbnail((w, h), Image.LANCZOS)
        self._tk_img = ImageTk.PhotoImage(fit)
        self.preview_img.configure(image=self._tk_img, text="")

    def open_large(self):
        item = self.current
        im = self.previews.get(item["path"]) if item else None
        if im is None:
            return
        win = tk.Toplevel(self)
        win.title(item["name"])
        win.configure(bg="#111111")
        set_icon(win)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        big = im.copy()
        big.thumbnail((int(sw * 0.85), int(sh * 0.8)), Image.LANCZOS)
        photo = ImageTk.PhotoImage(big)
        lbl = tk.Label(win, image=photo, bg="#111111", bd=0, cursor="hand2")
        lbl.image = photo
        lbl.pack(padx=12, pady=12)
        tk.Label(win, text=t("close_hint"), bg="#111111", fg=MUTED, font=self.f_small).pack(pady=(0, 8))
        win.geometry(f"+{(sw - big.width - 24) // 2}+{max(0, (sh - big.height - 60) // 2 - 20)}")
        for seq in ("<Escape>", "<Button-1>"):
            win.bind(seq, lambda e: win.destroy())
        dark_title_bar(win)
        win.focus_force()


SETUP_CACHE = os.path.join(DATA_DIR, "Setup", f"{APP_NAME}-Setup.exe")  # copia lasciata dall'installazione guidata
GUI_MUTEX = "Local\\PresetCraftAppLicator-gui"


def launch_uninstaller():
    """Voce di Impostazioni › App: apre la disinstallazione guidata (o quella di Windows Installer, se manca)."""
    if os.path.isfile(SETUP_CACHE):
        subprocess.Popen([SETUP_CACHE, "--uninstall"], close_fds=True)
        return 0
    import ctypes
    buf = ctypes.create_unicode_buffer(39)
    from version import UPGRADE_CODE
    if ctypes.windll.msi.MsiEnumRelatedProductsW(UPGRADE_CODE, 0, 0, buf) == 0:
        subprocess.Popen(["msiexec.exe", "/x", buf.value], close_fds=True)
    return 0


def already_open():
    """Se la finestra dell'app è già aperta la porta in primo piano e restituisce True (il mutex resta al processo)."""
    import ctypes
    k32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.CreateMutexW.restype = ctypes.c_void_p
    already_open.mutex = k32.CreateMutexW(None, False, GUI_MUTEX)
    if k32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return False
    hwnd = user32.FindWindowW("TkTopLevel", APP_NAME)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    return True


def main():
    args = sys.argv[1:]
    if "--watch" in args:  # importazione automatica in background: icona nell'area di notifica, nessuna finestra
        sys.exit(watcher.run(once="--once" in args, tray="--no-tray" not in args))
    # chiamate da Windows Installer (azioni dell'MSI): veloci e senza finestre
    if "--msi-stop" in args:  # aggiornamento: il processo in background si ferma, la nuova versione lo riavvia
        autostart.stop_running()
        sys.exit(0)
    if "--msi-remove" in args:  # disinstallazione: si ferma, l'attività pianificata e la copia del setup spariscono
        autostart.stop_running()
        try:
            autostart.disable()
        finally:
            # se la disinstallazione l'ha avviata proprio questa copia, è in uso: la toglie lei quando si chiude
            import shutil
            shutil.rmtree(os.path.dirname(SETUP_CACHE), ignore_errors=True)
        sys.exit(0)
    if "--msi-start" in args:
        autostart.start_now()
        sys.exit(0)
    if "--uninstall" in args:
        sys.exit(launch_uninstaller())
    if "--install-ai" in args:  # installazione del motore IA in un processo a sé: --install-ai <cartella> <modello>
        import ai
        i = args.index("--install-ai")
        sys.exit(ai.install_main(args[i + 1], args[i + 2], args[i + 3] if len(args) > i + 3 else None))
    if already_open():
        sys.exit(0)
    dpi_aware()
    App().mainloop()


if __name__ == "__main__":
    main()
