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

import autostart
import camera_styles as cs
import i18n
import matcher
import updater
import watcher
from i18n import registry_value, t
from lightcraft import (BACKUPS_KEPT, DATA_DIR, DEFAULT_CLI, LOCAL, backup_root, delete_backups, is_cli, is_library,
                        library_open_reason, list_backups, log)
from translations import DATE_FORMATS, LANGUAGES, TEXTS
from ui_theme import (LEVELS, MUTED, TEXT, Dialog, attach_scrollbars, center_on, dark_title_bar, dpi_aware, set_icon,
                      setup_theme)
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
        ttk.Button(buttons, text=t("btn_save"), style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(buttons, text=t("btn_cancel"), command=self.destroy).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self.destroy())

        center_on(self, app)
        dark_title_bar(self)
        self.deiconify()
        self.grab_set()

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
        self.geometry("760x460")
        self.minsize(620, 360)
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

    def open_folder(self):
        sel = self.tree.selection()
        path = self.backups[sel[0]]["path"] if sel else backup_root(self.lib, self.app.settings) if self.lib else None
        if path and os.path.isdir(path):
            os.startfile(path)
        elif path and os.path.isdir(os.path.dirname(path)):
            os.startfile(os.path.dirname(path))

    def delete(self, all_=False):
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
        self.tree = ttk.Treeview(box, columns=("folder", "library"), show="headings", height=4, selectmode="browse")
        self.tree.heading("folder", text=t("auto_col_folder"), anchor="w")
        self.tree.heading("library", text=t("auto_col_library"), anchor="w")
        self.tree.column("folder", width=420, anchor="w")
        self.tree.column("library", width=300, anchor="w")
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

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rules):
            self.tree.insert("", "end", iid=str(i), values=(r["folder"], self.lib_name(r["library"])))
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
        try:
            existing = watcher.new_files(folder, lib)
        except OSError as e:
            Dialog.ask(self, t("auto_title"), t("auto_folder_bad"), str(e), level="error")
            return
        import_existing = False
        if existing:
            answer = Dialog.ask(self, t("auto_title"), t("auto_existing_head", count=len(existing)),
                                t("auto_existing_body"), level="info",
                                buttons=[("import", t("btn_import_too"), False), ("ignore", t("btn_ignore"), True)],
                                default="ignore")
            if answer is None:
                return
            import_existing = answer == "import"
        rule = {"folder": folder, "library": lib}
        self.rules.append(rule)
        self.added.append((rule, import_existing))
        self.fill()

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
        enabled = bool(self.enabled.get()) and bool(self.rules)
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


# ---------------------------------------------------------------- finestra principale

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
        if self.settings.get("check_updates", True):
            self.after(2500, self.check_updates)

    def _build(self):
        self.outer = outer = ttk.Frame(self, padding=(24, 20, 24, 18))
        outer.pack(fill="both", expand=True)

        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Button(head, text="⚙  " + t("settings"), command=self.open_settings).pack(side="right", anchor="n")
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
        # quali foto esaminare: una riga a parte, così i pulsanti della libreria hanno sempre spazio
        scope = ttk.Frame(card)
        scope.pack(fill="x", pady=(10, 0))
        ttk.Label(scope, text=t("scope_label"), style="Muted.TLabel").pack(side="left", padx=(0, 14))
        self.scope = tk.StringVar(value=self.settings.get("scope", "recent"))
        for value, key in (("recent", "scope_recent"), ("all", "scope_all")):
            ttk.Radiobutton(scope, text=t(key), value=value, variable=self.scope,
                            command=self.on_scope_changed).pack(side="left", padx=(0, 18))
        self.update_library_label()

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

        foot = ttk.Frame(outer)
        foot.pack(side="bottom", fill="x", pady=(14, 0))
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

    def on_scope_changed(self):
        self.settings["scope"] = self.scope.get()
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
        token, scope, settings, finder = self.loading, self.scope.get(), self.settings, self.finder

        def progress(done, total):
            self.after(0, lambda: self.loading == token and self.set_status("info", t("st_reading_n", done=done,
                                                                                    total=total)))

        def work():
            try:
                plan = matcher.build_plan(lib, scope, settings, finder, progress)
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
                when_text(item["imported"]), style_text(item), item["current_name"], item["target"] or "—",
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
        if styled:
            menu.add_command(label=t("menu_mapping"), command=lambda: self.open_mappings(styled["style_key"]))
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
            if action == default:
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

    def open_mappings(self, focus_key=None):
        d = MappingsDialog(self, focus_key)
        self.wait_window(d)
        if d.saved:
            self.refresh()

    def open_backups(self):
        self.wait_window(BackupsDialog(self))

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
    if already_open():
        sys.exit(0)
    dpi_aware()
    App().mainloop()


if __name__ == "__main__":
    main()
