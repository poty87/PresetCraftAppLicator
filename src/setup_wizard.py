"""Installazione guidata di PresetCraftAppLicator, con la stessa grafica dell'app.

È un eseguibile unico (PyInstaller) che contiene l'MSI: lo installa, aggiorna o disinstalla attraverso
Windows Installer (msi.dll) senza mostrarne l'interfaccia, e disegna avanzamento e pagine in proprio.

    PresetCraftAppLicator-Setup-x.y.z.exe              installazione guidata
    PresetCraftAppLicator-Setup-x.y.z.exe --update     avviata dall'app per aggiornarsi
    PresetCraftAppLicator-Setup-x.y.z.exe --silent [--dir D] [--lang it] [--no-desktop]
    PresetCraftAppLicator-Setup-x.y.z.exe --silent --uninstall
"""

import argparse
import ctypes
import glob
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from ctypes import wintypes
from tkinter import filedialog, ttk

import autostart
import i18n
from i18n import registry_value, t
from translations import LANGUAGES
from ui_theme import ACCENT, MUTED, TEXT, Dialog, dark_title_bar, dpi_aware, resource, set_icon, setup_theme
from version import APP_NAME, EXE, UPGRADE_CODE, parse_version

LOCAL = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
DEFAULT_DIR = os.path.join(LOCAL, "Programs", APP_NAME)
LOG = os.path.join(tempfile.gettempdir(), f"{APP_NAME}-setup.log")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SIDEBAR = "#202020"

# ---------------------------------------------------------------- Windows Installer (msi.dll)

msi = ctypes.windll.msi
ERROR_SUCCESS, ERROR_NO_MORE_ITEMS, ERROR_SUCCESS_REBOOT_REQUIRED, ERROR_INSTALL_USEREXIT = 0, 259, 3010, 1602
INSTALLUILEVEL_NONE = 2
INSTALLSTATE_ABSENT = 2
INSTALLMESSAGE_TYPE_MASK = 0xFF000000
INSTALLMESSAGE_PROGRESS, INSTALLMESSAGE_ACTIONDATA = 0x0A000000, 0x09000000
LOGMODE_UI = 0x400 | 0x200 | 0x2 | 0x1  # PROGRESS | ACTIONDATA | ERROR | FATALEXIT
LOGMODE_FILE = 0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x40 | 0x80 | 0x100 | 0x200 | 0x400 | 0x800 | 0x1000  # quasi tutto
IDCANCEL = 2
UI_HANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p)
FIELD = re.compile(r"(\d+):\s*(-?\d+)")
MSIHANDLE = ctypes.c_ulong  # gli handle di msi.dll sono interi a 32 bit anche su Windows a 64 bit


def payload_msi():
    found = glob.glob(resource("payload", "*.msi"))
    if not found:
        raise FileNotFoundError("the setup does not contain the MSI package")
    return found[0]


def msi_property(path, name):
    """Una proprietà della tabella Property di un pacchetto MSI (ProductCode, ProductVersion…)."""
    db, view, rec = MSIHANDLE(), MSIHANDLE(), MSIHANDLE()
    if msi.MsiOpenDatabaseW(path, ctypes.c_wchar_p(0), ctypes.byref(db)) != 0:  # 0 = sola lettura
        raise OSError(f"cannot open {path}")
    try:
        sql = f"SELECT `Value` FROM `Property` WHERE `Property`='{name}'"
        msi.MsiDatabaseOpenViewW(db, sql, ctypes.byref(view))
        msi.MsiViewExecute(view, 0)
        if msi.MsiViewFetch(view, ctypes.byref(rec)) != 0:
            return None
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        msi.MsiRecordGetStringW(rec, 1, buf, ctypes.byref(size))
        return buf.value
    finally:
        for h in (rec, view, db):
            if h.value:
                msi.MsiCloseHandle(h)


def product_info(code, prop):
    size = wintypes.DWORD(1024)
    buf = ctypes.create_unicode_buffer(size.value)
    return buf.value if msi.MsiGetProductInfoW(code, prop, buf, ctypes.byref(size)) == 0 else ""


def installed_product():
    """(ProductCode, versione, cartella) della versione installata per questo utente, oppure None."""
    buf = ctypes.create_unicode_buffer(39)
    i = 0
    while msi.MsiEnumRelatedProductsW(UPGRADE_CODE, 0, i, buf) == ERROR_SUCCESS:
        code = buf.value
        version = product_info(code, "VersionString")
        if version:
            location = product_info(code, "InstallLocation").rstrip("\\")
            if not location and os.path.isfile(os.path.join(DEFAULT_DIR, EXE)):
                location = DEFAULT_DIR
            return code, version, location
        i += 1
    return None


class MsiRunner:
    """Esegue un'operazione di Windows Installer senza la sua interfaccia, riportando l'avanzamento (0–1)."""

    def __init__(self, on_progress):
        self.on_progress = on_progress
        self.cancel = False
        self._handler = UI_HANDLER(self._message)  # va tenuto vivo finché msi.dll lo può chiamare
        self._reset(1, True)

    def _reset(self, total, forward):
        self.total, self.forward, self.pos = max(1, total), forward, 0 if forward else total
        self.action_step, self.action_data = 0, False

    def _message(self, _ctx, kind, text):
        if self.cancel:
            return IDCANCEL
        kind &= INSTALLMESSAGE_TYPE_MASK
        if kind == INSTALLMESSAGE_PROGRESS and text:
            fields = {int(k): int(v) for k, v in FIELD.findall(text)}  # "1: 0 2: 1500 3: 0 4: 1"
            f1 = fields.get(1)
            if f1 == 0:  # nuova fase: totale di passi e direzione
                self._reset(fields.get(2, 1), fields.get(3, 0) == 0)
            elif f1 == 1 and fields.get(3) == 1:  # ogni ActionData vale un certo numero di passi
                self.action_step, self.action_data = fields.get(2, 0), True
            elif f1 == 2:
                self.pos += fields.get(2, 0) if self.forward else -fields.get(2, 0)
            elif f1 == 3:
                self.total += fields.get(2, 0)
            self.on_progress(min(1.0, max(0.0, self.pos / self.total)))
        elif kind == INSTALLMESSAGE_ACTIONDATA and self.action_data:
            self.pos += self.action_step
            self.on_progress(min(1.0, max(0.0, self.pos / self.total)))
        return 0  # 0 = lascia a Windows Installer la risposta predefinita

    def _prepare(self):
        msi.MsiSetInternalUI(INSTALLUILEVEL_NONE, None)
        msi.MsiSetExternalUIW(self._handler, LOGMODE_UI, None)
        msi.MsiEnableLogW(LOGMODE_FILE, LOG, 0)

    def install(self, package, properties):
        self._prepare()
        cmd = " ".join(f'{k}="{v}"' for k, v in properties.items())
        return msi.MsiInstallProductW(package, cmd)

    def uninstall(self, product_code):
        self._prepare()
        return msi.MsiConfigureProductExW(product_code, 0, INSTALLSTATE_ABSENT, "")


def app_running():
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh", "/fi", f"imagename eq {EXE}"], capture_output=True,
                         text=True, creationflags=NO_WINDOW).stdout
    return EXE.lower() in out.lower()


def install_properties(folder, lang, desktop, reinstall=False):
    props = {"TARGETDIR": folder.rstrip("\\") + "\\", "PCA_LANGUAGE": lang, "PCA_DESKTOP": "1" if desktop else "0",
             "REBOOT": "ReallySuppress"}
    if reinstall:  # stesso pacchetto già installato: lo si reinstalla per intero
        props.update(REINSTALL="ALL", REINSTALLMODE="amus")
    return props


# ---------------------------------------------------------------- installazione guidata

class Wizard(tk.Tk):
    def __init__(self, update_mode=False):
        super().__init__()
        self.withdraw()
        self.resizable(False, False)
        set_icon(self)
        setup_theme(self)
        self.package = payload_msi()
        self.version = msi_property(self.package, "ProductVersion")
        self.product_code = msi_property(self.package, "ProductCode")
        self.installed = installed_product()
        self.lang_code = registry_value("Language") or "auto"
        i18n.set_language(self.lang_code)
        self.folder = tk.StringVar(value=(self.installed[2] if self.installed and self.installed[2] else DEFAULT_DIR))
        previous_desktop = registry_value("DesktopShortcut")
        self.desktop = tk.BooleanVar(value=True if previous_desktop is None else previous_desktop == 1)
        self.accepted = tk.BooleanVar(value=False)
        self.launch = tk.BooleanVar(value=True)
        self.events = queue.Queue()
        self.runner = None
        self.result_kind = "install"
        self.result = None  # (operazione, codice di ritorno)
        self.mode = self._mode()
        self.update_mode = update_mode
        self.page = "welcome"
        self.protocol("WM_DELETE_WINDOW", self.ask_cancel)
        self.render()
        width, height = 860, 560
        self.geometry(f"{width}x{height}+{(self.winfo_screenwidth() - width) // 2}+"
                      f"{(self.winfo_screenheight() - height) // 3}")
        dark_title_bar(self)
        self.deiconify()

    def _mode(self):
        if not self.installed:
            return "install"
        old = parse_version(self.installed[1])
        new = parse_version(self.version)
        return "upgrade" if old < new else "same" if old == new else "newer"

    # ---- struttura

    def steps(self):
        if self.mode == "install":
            return ["welcome", "license", "folder", "progress", "finish"]
        return ["welcome", "progress", "finish"]

    def render(self):
        for w in self.winfo_children():
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        self.title(t("w_title"))
        side = tk.Frame(self, bg=SIDEBAR, width=230)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        try:
            self._logo = tk.PhotoImage(file=resource("assets", "icon.png")).subsample(4, 4)
            tk.Label(side, image=self._logo, bg=SIDEBAR).pack(anchor="w", padx=24, pady=(28, 10))
        except tk.TclError:
            pass
        tk.Label(side, text=APP_NAME, bg=SIDEBAR, fg=TEXT, font=self.f_strong).pack(anchor="w", padx=24)
        tk.Label(side, text=t("about", version=self.version), bg=SIDEBAR, fg=MUTED, font=self.f_small).pack(
            anchor="w", padx=24)
        names = {"welcome": "w_s_welcome", "license": "w_s_license", "folder": "w_s_folder",
                 "progress": "w_s_install", "finish": "w_s_done"}
        current = self.steps().index(self.page if self.page != "ready" else "progress")
        steps_box = tk.Frame(side, bg=SIDEBAR)
        steps_box.pack(anchor="w", padx=24, pady=(30, 0))
        for i, step in enumerate(self.steps()):
            mark, color, font = ("✓", MUTED, self.f_body) if i < current else \
                ("●", ACCENT, self.f_strong) if i == current else ("○", MUTED, self.f_body)
            row = tk.Frame(steps_box, bg=SIDEBAR)
            row.pack(anchor="w", pady=5)
            tk.Label(row, text=mark, bg=SIDEBAR, fg=color, font=self.f_body, width=2).pack(side="left")
            tk.Label(row, text=t(names[step]), bg=SIDEBAR, fg=TEXT if i == current else MUTED, font=font).pack(
                side="left")

        main = ttk.Frame(self, padding=(32, 30, 32, 22))
        main.pack(side="left", fill="both", expand=True)
        self.nav = ttk.Frame(main)
        self.nav.pack(side="bottom", fill="x", pady=(18, 0))
        ttk.Separator(main).pack(side="bottom", fill="x")
        self.body = ttk.Frame(main)
        self.body.pack(fill="both", expand=True)
        getattr(self, "page_" + self.page)()

    def heading(self, title, text=None):
        ttk.Label(self.body, text=title, style="Title.TLabel", wraplength=540, justify="left").pack(anchor="w")
        if text:
            ttk.Label(self.body, text=text, style="Body.TLabel", wraplength=540, justify="left").pack(
                anchor="w", pady=(10, 0))

    def buttons(self, *specs):
        """specs: (testo, comando, principale?, attivo?) da sinistra a destra; Annulla è sempre a sinistra."""
        if self.page not in ("finish",):
            ttk.Button(self.nav, text=t("btn_cancel"), command=self.ask_cancel).pack(side="left")
        for text, command, primary, active in reversed(specs):
            b = ttk.Button(self.nav, text=text, command=command, style="Accent.TButton" if primary else "TButton")
            b.pack(side="right", padx=(8, 0))
            if not active:
                b.state(["disabled"])
            if primary:
                self.primary = b

    def go(self, page):
        self.page = page
        self.render()

    # ---- pagine

    def page_welcome(self):
        old = self.installed[1] if self.installed else ""
        if self.mode == "install":
            self.heading(t("w_welcome_head", version=self.version), t("w_welcome_body"))
        elif self.mode == "upgrade":
            self.heading(t("w_upgrade_head"), t("w_upgrade_body", old=old, new=self.version))
        elif self.mode == "same":
            self.heading(t("w_same_head", version=self.version), t("w_same_body"))
        else:
            self.heading(t("w_newer_head"), t("w_newer_body", old=old, new=self.version))

        if self.mode != "newer":
            ttk.Label(self.body, text=t("lang_label").upper(), style="Muted.TLabel").pack(anchor="w", pady=(28, 4))
            codes = ["auto", *LANGUAGES]
            combo = ttk.Combobox(self.body, values=[t("lang_auto"), *LANGUAGES.values()], state="readonly", width=36)
            combo.current(codes.index(self.lang_code) if self.lang_code in codes else 0)
            combo.pack(anchor="w")

            def changed(_e):
                self.lang_code = codes[combo.current()]
                i18n.set_language(self.lang_code)
                self.render()
            combo.bind("<<ComboboxSelected>>", changed)

        if self.mode == "install":
            self.buttons((t("w_next"), lambda: self.go("license"), True, True))
        elif self.mode == "upgrade":
            self.buttons((t("w_update"), self.start_install, True, True))
        elif self.mode == "same":
            self.buttons((t("w_uninstall"), self.start_uninstall, False, True),
                         (t("w_reinstall"), lambda: self.start_install(reinstall=True), True, True))
        else:
            for w in self.nav.winfo_children():
                w.destroy()
            ttk.Button(self.nav, text=t("w_close"), style="Accent.TButton", command=self.destroy).pack(side="right")

    def page_license(self):
        self.heading(t("w_s_license"), t("w_license_body"))
        box = tk.Text(self.body, height=12, wrap="word", relief="flat", bg="#141414", fg=TEXT, padx=12, pady=10,
                      font=("Consolas", 9), insertbackground=TEXT)
        try:
            with open(resource("LICENSE"), encoding="utf-8") as f:
                box.insert("1.0", f.read())
        except OSError:
            box.insert("1.0", "MIT License")
        box.configure(state="disabled")
        box.pack(fill="both", expand=True, pady=(14, 10))
        ttk.Checkbutton(self.body, text=t("w_accept"), variable=self.accepted,
                        command=lambda: self.primary.state(["!disabled" if self.accepted.get() else "disabled"])).pack(
            anchor="w")
        self.buttons((t("w_back"), lambda: self.go("welcome"), False, True),
                     (t("w_next"), lambda: self.go("folder"), True, self.accepted.get()))

    def page_folder(self):
        self.heading(t("w_folder_head"), t("w_folder_body"))
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=(24, 0))
        ttk.Entry(row, textvariable=self.folder).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text=t("btn_browse"), command=self.browse).pack(side="left", padx=(8, 0))
        size = os.path.getsize(self.package) * 3.1  # l'MSI è compresso: installato occupa circa il triplo
        ttk.Label(self.body, text=t("w_space", size=f"{size / 1048576:.0f} MB"), style="Muted.TLabel").pack(
            anchor="w", pady=(6, 0))
        ttk.Checkbutton(self.body, text=t("w_desktop"), variable=self.desktop).pack(anchor="w", pady=(24, 0))
        self.buttons((t("w_back"), lambda: self.go("license"), False, True),
                     (t("w_install"), self.start_install, True, True))

    def page_progress(self):
        title = t("w_uninstalling") if self.result_kind == "uninstall" else t("w_installing")
        self.heading(title, t("w_wait"))
        self.bar = ttk.Progressbar(self.body, mode="determinate", maximum=1000)
        self.bar.pack(fill="x", pady=(40, 0))
        self.percent = ttk.Label(self.body, text="0%", style="Muted.TLabel")
        self.percent.pack(anchor="e", pady=(6, 0))
        self.buttons()

    def page_finish(self):
        kind, code = self.result
        ok = code in (ERROR_SUCCESS, ERROR_SUCCESS_REBOOT_REQUIRED)
        if not ok:
            self.heading(t("w_failed_head"), t("w_failed_body", code=code))
            path = ttk.Entry(self.body)
            path.insert(0, LOG)
            path.configure(state="readonly")
            path.pack(fill="x", pady=(14, 0))
        elif kind == "uninstall":
            self.heading(t("w_removed_head"), t("w_removed_body"))
        else:
            head = t("w_updated_head", version=self.version) if self.mode == "upgrade" else t("w_done_head")
            self.heading(head, t("w_done_body"))
            ttk.Checkbutton(self.body, text=t("w_launch"), variable=self.launch).pack(anchor="w", pady=(28, 0))
        self.buttons((t("w_finish"), self.finish, True, True))

    # ---- azioni

    def browse(self):
        start = self.folder.get() if os.path.isdir(self.folder.get()) else LOCAL
        path = filedialog.askdirectory(parent=self, title=t("w_folder_head"), initialdir=start)
        if path:
            path = os.path.normpath(path)
            # una cartella esistente riceve la sottocartella col nome dell'app, come fanno gli installer comuni
            if os.path.basename(path).lower() != APP_NAME.lower():
                path = os.path.join(path, APP_NAME)
            self.folder.set(path)

    def ensure_app_closed(self):
        # l'importazione automatica in background usa lo stesso .exe: si ferma da sola e riparte alla fine
        self.watcher_was_running = autostart.stop_running() or getattr(self, "watcher_was_running", False)
        while app_running():
            answer = Dialog.ask(self, t("w_title"), t("w_app_running_head"), t("w_app_running_body"), level="warn",
                                buttons=[("cancel", t("btn_cancel"), False), ("retry", t("w_retry"), True)])
            if answer != "retry":
                return False
        return True

    def start_install(self, reinstall=False):
        folder = self.folder.get().strip()
        if not folder or not os.path.isabs(folder):
            Dialog.ask(self, t("w_title"), t("w_folder_bad"), None, level="warn")
            return
        if not self.ensure_app_closed():
            return
        reinstall = reinstall and self.installed and self.installed[0] == self.product_code
        props = install_properties(folder, self.lang_code, self.desktop.get(), reinstall)
        self.run_operation("install", lambda r: r.install(self.package, props))

    def start_uninstall(self):
        if not self.ensure_app_closed():
            return
        code = self.installed[0]
        self.run_operation("uninstall", lambda r: r.uninstall(code))

    def run_operation(self, kind, call):
        self.result_kind = kind
        self.go("progress")
        self.runner = MsiRunner(lambda value: self.events.put(("progress", value)))

        def work():
            try:
                code = call(self.runner)
            except Exception:  # noqa: BLE001
                code = 1603
            self.events.put(("done", code))
        threading.Thread(target=work, daemon=True).start()
        self.after(50, self.poll)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress" and self.page == "progress":
                    self.bar["value"] = value * 1000
                    self.percent.configure(text=f"{value * 100:.0f}%")
                elif kind == "done":
                    self.result = (self.result_kind, value)
                    self.runner = None
                    self.go("finish")
                    return
        except queue.Empty:
            pass
        self.after(50, self.poll)

    def ask_cancel(self):
        if self.page == "finish":
            self.finish()
            return
        answer = Dialog.ask(self, t("w_title"), t("w_cancel_q"), None, level="warn",
                            buttons=[("no", t("w_no"), False), ("yes", t("w_yes"), True)], default="no")
        if answer != "yes":
            return
        if self.runner:
            self.runner.cancel = True  # Windows Installer annulla e ripristina; poi arriva la pagina finale
        else:
            self.destroy()

    def finish(self):
        kind, code = self.result or ("", -1)
        if kind == "uninstall" and code in (0, 3010):
            try:
                autostart.disable()  # l'attività punterebbe a un programma che non c'è più
            except Exception:  # noqa: BLE001
                pass
        elif getattr(self, "watcher_was_running", False):
            autostart.start_now()  # dopo l'installazione (o se è stata annullata) l'importazione automatica riparte
        if kind == "install" and code in (0, 3010) and self.launch.get():
            exe = os.path.join(self.folder.get(), EXE)
            if os.path.isfile(exe):
                subprocess.Popen([exe], cwd=self.folder.get(), close_fds=True)
        self.destroy()


# ---------------------------------------------------------------- senza interfaccia

def silent(args):
    package = payload_msi()
    runner = MsiRunner(lambda _v: None)
    installed = installed_product()
    was_running = autostart.stop_running()
    if args.uninstall:
        code = runner.uninstall(installed[0]) if installed else 0
        if code in (0, 3010):
            try:
                autostart.disable()
            except Exception:  # noqa: BLE001
                pass
        return code
    try:
        if installed and parse_version(installed[1]) > parse_version(msi_property(package, "ProductVersion")):
            return 1638  # una versione più recente è già installata
        folder = args.dir or (installed[2] if installed and installed[2] else DEFAULT_DIR)
        lang = args.lang or registry_value("Language") or "auto"
        desktop = not args.no_desktop
        reinstall = bool(installed and installed[0] == msi_property(package, "ProductCode"))
        return runner.install(package, install_properties(folder, lang, desktop, reinstall))
    finally:
        if was_running:
            autostart.start_now()


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dir")
    parser.add_argument("--lang", choices=["auto", *LANGUAGES])
    parser.add_argument("--no-desktop", action="store_true")
    args, _ = parser.parse_known_args()
    if args.silent:
        sys.exit(silent(args))
    dpi_aware()
    watcher_was_running = autostart.stop_running()
    if args.update:
        # l'app si è appena chiusa da sola per farsi aggiornare: le si lascia un attimo per uscire
        for _ in range(20):
            if not app_running():
                break
            threading.Event().wait(0.5)
    wizard = Wizard(update_mode=args.update)
    wizard.watcher_was_running = watcher_was_running
    wizard.mainloop()
    if watcher_was_running and not getattr(wizard, "result", None):
        autostart.start_now()  # finestra chiusa senza installare: l'importazione automatica riparte


if __name__ == "__main__":
    main()
