"""Icona nell'area di notifica dell'importazione automatica.

- Il pallino sull'icona dice lo stato: verde = attiva e in attesa di foto, blu = foto in arrivo o importazione in
  corso, giallo = aspetta che LightCraft venga chiuso, rosso = errore.
- Il doppio clic apre PresetCraftAppLicator.
- Il tasto destro mostra cosa sta facendo e quanto manca, più i comandi.
- Ricerca IA (motore facoltativo): una riga con l'analisi in corso, il comando di pausa/ripresa e una notifica di
  Windows quando un giro ha catalogato foto nuove.
"""

import datetime
import os
import subprocess
import sys
import threading
import time

import pystray
from PIL import Image, ImageDraw
from pystray._util import win32

import ai
import i18n
from i18n import registry_value, t
from lightcraft import DATA_DIR, log
from translations import DATE_FORMATS
from ui_theme import resource
from version import APP_NAME
from watcher import SETTINGS, Status, Watcher, load_json, save_json

LOG = os.path.join(DATA_DIR, "presetcraft.log")
WM_LBUTTONDBLCLK = 0x0203
DOTS = {"idle": "#4ade80", "done": "#4ade80", "incoming": "#60a5fa", "importing": "#60a5fa",
        "profiles": "#60a5fa", "metadata": "#60a5fa", "waiting": "#fbbf24", "error": "#f87171"}


def badge_icon(color):
    """L'icona dell'app con un pallino colorato in basso a destra."""
    try:
        base = Image.open(resource("assets", "icon.png")).convert("RGBA").resize((64, 64), Image.LANCZOS)
    except OSError:
        base = Image.new("RGBA", (64, 64), "#3b82f6")
    draw = ImageDraw.Draw(base)
    draw.ellipse((36, 36, 63, 63), fill="#111111")
    draw.ellipse((40, 40, 59, 59), fill=color)
    return base


def clock(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")


def when_text(iso):
    try:
        when = datetime.datetime.fromisoformat(iso[:19])
    except (TypeError, ValueError):
        return ""
    day = t("tray_today") if when.date() == datetime.date.today() else \
        when.strftime(DATE_FORMATS.get(i18n.LANG, "%d/%m/%Y"))
    return f"{day} {when.strftime('%H:%M')}"


def duration(seconds):
    if seconds is None:
        return ""
    if seconds < 60:
        return t("tray_eta_s", n=max(5, int(round(seconds / 5.0)) * 5))
    return t("tray_eta_m", n=int(round(seconds / 60.0)))


def describe(s):
    """(riga principale, riga secondaria) del menu e testo del suggerimento sull'icona."""
    phase = s.get("phase", "idle")
    lib = s.get("library", "")
    if phase == "incoming":
        main = t("tray_incoming", count=s.get("count", 0))
        sub = t("tray_incoming_sub", n=s.get("quiet_left", 0))
    elif phase == "waiting":
        main = t("tray_waiting", count=s.get("count", 0))
        sub = t("tray_waiting_sub")
    elif phase in ("importing", "profiles"):
        key = "tray_importing" if phase == "importing" else "tray_profiles"
        main = t(key, done=s.get("done", 0), total=s.get("total", 0))
        left = duration(s.get("eta"))
        sub = t("tray_left", time=left) if left else lib
    elif phase == "metadata":
        main = t("tray_metadata", count=s.get("count", 0))
        sub = lib
    elif phase == "error":
        main = t("tray_error")
        sub = (s.get("error") or "")[:90]
    else:
        main = t("tray_idle")
        sub = t("tray_next", time=clock(s["next_check"])) if s.get("next_check") else ""
    return main, sub, last_text(s.get("last"))


def last_text(last):
    if not last:
        return ""
    if last.get("imported"):
        return t("tray_last", count=last["imported"], when=when_text(last.get("when")))
    if last.get("profiles"):
        return t("tray_last_profiles", count=last["profiles"], when=when_text(last.get("when")))
    return t("tray_last_metadata", count=last.get("metadata", 0), when=when_text(last.get("when")))


def ai_text(settings):
    """La riga della ricerca IA nel menu (vuota se il motore non c'è o non ha niente da dire)."""
    cfg = ai.config(settings)
    folder = ai.engine_dir(settings)
    if not cfg.get("enabled") or not ai.queue(settings) or not ai.installed_info(folder):
        return ""
    if cfg.get("paused"):
        return t("tray_ai_paused")
    st = ai.worker_status(folder)
    phase = st.get("phase")
    if st["running"] and phase in ("previews", "embeddings") and st.get("total"):
        pct = (st.get("done") or 0) * 100 // max(1, st["total"])
        return t("tray_ai_running", library=st.get("library") or "", pct=pct)
    if st["running"]:
        return t("tray_ai_working")
    if phase == "gpu_busy":
        return t("tray_ai_gpu_busy")
    if phase == "waiting_window":
        return t("tray_ai_window")
    return ""


class TrayIcon(pystray.Icon):
    """pystray con il doppio clic (pystray conosce solo il clic singolo, che qui non fa niente)."""

    def __init__(self, *args, on_double_click=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_double_click = on_double_click

    def _on_notify(self, wparam, lparam):
        if lparam == WM_LBUTTONDBLCLK:
            if self.on_double_click:
                self.on_double_click()
            return
        if lparam == win32.WM_LBUTTONUP:
            return
        return super()._on_notify(wparam, lparam)


def open_app():
    """Avvia la finestra di PresetCraftAppLicator (se è già aperta, l'app porta in primo piano quella)."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        cmd = [exe if os.path.exists(exe) else sys.executable,
               os.path.join(os.path.dirname(os.path.abspath(__file__)), "presetcraft.py")]
    subprocess.Popen(cmd, close_fds=True)


def open_log():
    if os.path.exists(LOG):
        os.startfile(LOG)


class Tray:
    def __init__(self):
        settings = load_json(SETTINGS)
        i18n.set_language(settings.get("language") or registry_value("Language"))
        self.images = {}
        self.lock = threading.Lock()
        self.status = Status(on_change=self.changed)
        self.watcher = Watcher(self.status)
        self.icon = TrayIcon("PresetCraftAppLicator-watch", self.image("idle"), APP_NAME, menu=self.menu(),
                             on_double_click=open_app)
        self.shown_phase = None
        self.ai_line = ""
        self.ai_queue = False

    def image(self, phase):
        color = DOTS.get(phase, DOTS["idle"])
        if color not in self.images:
            self.images[color] = badge_icon(color)
        return self.images[color]

    def menu(self):
        def text(index):
            return lambda _item: describe(self.status.get())[index] or " "

        def visible(index):
            return lambda _item: bool(describe(self.status.get())[index])

        return pystray.Menu(
            pystray.MenuItem(text(0), lambda: None, enabled=False),
            pystray.MenuItem(text(1), lambda: None, enabled=False, visible=visible(1)),
            pystray.MenuItem(text(2), lambda: None, enabled=False, visible=visible(2)),
            pystray.MenuItem(lambda _i: self.ai_line or " ", lambda: None, enabled=False,
                             visible=lambda _i: bool(self.ai_line)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("tray_open"), lambda: open_app(), default=True),
            pystray.MenuItem(lambda _i: t("tray_ai_resume") if self.ai_paused() else t("tray_ai_pause"),
                             lambda: self.toggle_ai_pause(), visible=lambda _i: self.ai_queue),
            pystray.MenuItem(t("tray_check_now"), lambda: self.watcher.wake()),
            pystray.MenuItem(t("open_log"), lambda: open_log()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("tray_quit"), lambda: self.watcher.stop()),
        )

    def changed(self, s):
        """Chiamato dal ciclo dei controlli a ogni cambio di stato: aggiorna icona, suggerimento e menu."""
        if not self.icon.visible:
            return
        phase = s.get("phase", "idle")
        main, sub, _last = describe(s)
        try:
            if phase != self.shown_phase:
                self.icon.icon = self.image(phase)
            self.icon.title = f"{APP_NAME}\n{main}"[:127]
            self.icon.update_menu()
            if phase == "done" and self.shown_phase != "done" and s.get("last"):
                last = s["last"]
                if last.get("imported"):
                    self.icon.notify(t("tray_done_body", count=last["imported"], profiles=last.get("profiles", 0),
                                       library=last.get("library", "")), t("tray_done_title"))
            elif phase == "waiting" and s.get("first_wait"):
                self.icon.notify(t("tray_waiting_body", count=s.get("count", 0)), APP_NAME)
        except Exception as e:  # noqa: BLE001 — l'icona non deve mai fermare l'importazione
            log(f"tray: {e!r}")
        self.shown_phase = phase

    def loop(self, icon):
        icon.visible = True
        # durante un'importazione il tempo che manca cambia anche senza nuovi passi: il menu si rinfresca
        ticker = threading.Thread(target=self.tick, daemon=True)
        ticker.start()
        try:
            self.code = self.watcher.run()
        finally:
            try:
                icon.visible = False  # toglie subito l'icona dall'area di notifica (niente icone fantasma)
            finally:
                icon.stop()

    def ai_paused(self):
        return bool(ai.config(load_json(SETTINGS)).get("paused"))

    def toggle_ai_pause(self):
        settings = load_json(SETTINGS)
        ai.set_paused(settings, not ai.config(settings).get("paused"))
        save_json(SETTINGS, settings)
        if not ai.config(settings).get("paused"):
            self.watcher.wake()  # riparte subito, senza aspettare il prossimo giro
        self.ai_tick(force=True)

    def ai_tick(self, force=False):
        """Riga della ricerca IA nel menu e notifica quando un giro ha catalogato foto nuove."""
        settings = load_json(SETTINGS)
        line = ai_text(settings)
        queued = bool(ai.queue(settings)) and bool(ai.installed_info(ai.engine_dir(settings)))
        if force or line != self.ai_line or queued != self.ai_queue:
            self.ai_line, self.ai_queue = line, queued
            self.icon.update_menu()
        folder = ai.engine_dir(settings)
        last = ai.pending_notification(folder)
        if last and not ai.worker_status(folder)["running"]:
            ai.mark_notified(folder, last)
            libs = last.get("libraries") or {}
            body = "\n".join(t("tray_ai_done_line", count=n, library=name) for name, n in libs.items())
            if body:
                self.icon.notify(body, t("tray_ai_done_title"))

    def tick(self):
        n = 0
        while True:
            time.sleep(2)
            n += 1
            if self.status.get().get("phase") in ("incoming", "importing", "profiles", "metadata"):
                try:
                    self.icon.update_menu()
                except Exception:  # noqa: BLE001
                    pass
            if n % 3 == 0:  # ogni ~6 secondi: stato della ricerca IA
                try:
                    self.ai_tick()
                except Exception as e:  # noqa: BLE001 — l'icona non deve mai fermarsi per il motore IA
                    log(f"tray ai: {e!r}")

    def run(self):
        self.code = 0
        self.icon.run(setup=self.loop)
        return self.code


def run():
    return Tray().run()
