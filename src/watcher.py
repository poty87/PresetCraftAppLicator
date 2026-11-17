"""Importazione automatica: osserva le cartelle scelte, importa in LightCraft le foto nuove e dà a ciascuna il
profilo dello stile con cui è stata scattata.

Gira in background (PresetCraftAppLicator.exe --watch), avviato all'accesso a Windows da un'attività pianificata
(vedi autostart.py). Ogni regola è "cartella → libreria". Per ogni regola un file di stato in DATA_DIR\\watch\\ ricorda
i file già trattati: così una foto tolta dalla libreria non torna dentro da sola, e le foto che c'erano già quando
la regola è stata creata si possono ignorare.
"""

import ctypes
import datetime
import hashlib
import json
import os
import time

import camera_styles as cs
import matcher
from lightcraft import (BACKUPS_KEPT, DATA_DIR, DEFAULT_CLI, backup_library, backup_root, is_cli, is_library,
                        library_open_reason, log, read_catalog, run_cli)

SETTINGS = os.path.join(DATA_DIR, "settings.json")
STATE_DIR = os.path.join(DATA_DIR, "watch")
STATUS = os.path.join(STATE_DIR, "status.json")  # ultimo giro del processo in background, per la finestra
MUTEX = "Local\\PresetCraftAppLicator-watch"  # stessi nomi in autostart.py (usato dall'installazione guidata)
STOP_EVENT = "Local\\PresetCraftAppLicator-watch-stop"
DEFAULT_INTERVAL = 2  # minuti tra un controllo e l'altro
SETTLE = 30  # secondi: un file modificato da meno tempo potrebbe essere ancora in copia
MAX_TRIES = 5  # giri falliti dopo i quali un file non si riprova più (resta nel registro)
# formati che LightCraft sa importare (crates/engine/src/import/files.rs)
PHOTO_EXTS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".dng", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".sr2",
    ".srf", ".raf", ".orf", ".rw2", ".rwl", ".raw", ".pef", ".srw", ".psd", ".psb", ".jxl", ".gif", ".bmp",
    ".heic", ".heif", ".iiq", ".crw", ".mrw", ".x3f", ".kdc", ".mos", ".erf", ".3fr", ".fff", ".mef",
}
# cartelle di servizio di Synology e Windows: mai guardarci dentro
SKIP_DIRS = {"#recycle", "@eadir", "#snapshot", ".synologyworkingdirectory", "$recycle.bin"}


# ---------------------------------------------------------------- file JSON

def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def save_json(path, data):
    """Scrittura atomica: chi legge nello stesso momento trova il file vecchio o quello nuovo, mai mezzo."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- regole e stato

def key(path):
    return os.path.normcase(os.path.normpath(path))


def rule_state_path(rule):
    digest = hashlib.sha1(f"{key(rule['folder'])}|{key(rule['library'])}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(STATE_DIR, f"rule-{digest}.json")


def load_rule_state(rule):
    return load_json(rule_state_path(rule))


def save_rule_state(rule, state):
    save_json(rule_state_path(rule), state)


def forget_rule(rule):
    try:
        os.remove(rule_state_path(rule))
    except OSError:
        pass


def list_photos(folder, subfolders=False):
    """Foto nella cartella: {percorso: (dimensione, data di modifica)}."""
    found = {}
    stack = [folder]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            if current == folder:
                raise
            continue
        for e in entries:
            if e.name.startswith("."):
                continue
            try:
                if e.is_dir():
                    if subfolders and e.name.lower() not in SKIP_DIRS:
                        stack.append(e.path)
                elif os.path.splitext(e.name)[1].lower() in PHOTO_EXTS:
                    st = e.stat()
                    found[e.path] = (st.st_size, st.st_mtime)
            except OSError:
                continue
    return found


def library_paths(lib):
    """{percorso normalizzato: id} di tutte le foto della libreria, anche quelle in "Recently Deleted"."""
    paths = {}
    for p in read_catalog(lib)["photos"].values():
        path = (p.get("source") or {}).get("path")
        if path:
            paths[key(path)] = int(p["id"])
    return paths


def new_files(folder, lib, subfolders=False):
    """Foto della cartella che la libreria non ha (per chiedere cosa farne quando si crea una regola)."""
    known = library_paths(lib)
    return [p for p in list_photos(folder, subfolders) if key(p) not in known]


def create_rule_state(rule, import_existing):
    """Stato iniziale di una regola nuova: i file già presenti si ignorano, a meno che si vogliano importare."""
    files = list_photos(rule["folder"], rule.get("subfolders"))
    known = library_paths(rule["library"])
    handled = sorted(key(p) for p in files if key(p) in known or not import_existing)
    save_rule_state(rule, {"handled": handled, "seen": {}, "tries": {},
                           "created": datetime.datetime.now().isoformat(timespec="seconds")})


# ---------------------------------------------------------------- un giro su una regola

def cycle(rule, settings, finder, now=None):
    """Controlla una cartella; importa le foto pronte e dà loro il profilo. Restituisce un riepilogo o None."""
    folder, lib = rule["folder"], rule["library"]
    if not os.path.isdir(folder) or not is_library(lib):
        return None  # NAS spento, unità non ancora collegata, libreria spostata: si riprova al prossimo giro
    now = now or time.time()
    state = load_rule_state(rule)
    first = "handled" not in state
    handled = set(state.get("handled", []))
    files = list_photos(folder, rule.get("subfolders"))
    known = library_paths(lib)
    if first:
        # stato perso o regola scritta a mano: per prudenza si ignora quello che c'è già
        handled = {key(p) for p in files}
        state.update(handled=sorted(handled), seen={}, tries={})
        save_rule_state(rule, state)
        return None

    # file arrivati nella libreria per un'altra strada (trascinati, o importati da LightCraft stesso)
    arrived = [p for p in files if key(p) in known and key(p) not in handled]
    seen, ready = {}, []
    for path, (size, mtime) in files.items():
        k = key(path)
        if k in known or k in handled:
            continue
        before = state.get("seen", {}).get(k)
        seen[k] = [size, mtime]
        # pronto quando non cresce più tra due controlli e non è stato toccato negli ultimi secondi
        if before == [size, mtime] and size > 0 and now - mtime >= SETTLE:
            ready.append(path)
    state["seen"] = seen
    if not ready and not arrived:
        state.pop("waiting", None)
        save_rule_state(rule, state)
        return None

    reason = library_open_reason(lib)
    if reason:
        if not state.get("waiting"):
            log(f"watch: {len(ready) + len(arrived)} new photos for {lib}, waiting until LightCraft is closed")
        state["waiting"] = True
        save_rule_state(rule, state)
        return None
    state.pop("waiting", None)
    cli = settings.get("cli") or DEFAULT_CLI
    if not is_cli(cli):
        log(f"watch: lightcraft-cli not found ({cli})")
        save_rule_state(rule, state)
        return None

    root, kept = backup_root(lib, settings), settings.get("backups_kept", BACKUPS_KEPT)
    backup = backup_library(lib, root, kept)
    log(f"--- watch: {len(ready)} new photos in {folder} -> {lib} (backup: {backup})")
    view = os.path.join(lib, "view.json")
    try:
        with open(view, "rb") as f:
            saved_view = f.read()
    except OSError:
        saved_view = None
    summary = {"when": datetime.datetime.now().isoformat(timespec="seconds"), "imported": 0, "profiles": 0,
               "failed": []}
    ids = [known[key(p)] for p in arrived]
    try:
        if ready:
            reply = run_cli(cli, lib, [("library.import", {"paths": ready, "mode": "add", "onDeleted": "skip"})],
                            check=False)[0]
            tries = state.setdefault("tries", {})
            if reply.get("ok"):
                result = reply.get("result") or {}
                new = [int(i) for k in ("imported", "restored") for i in result.get(k) or []]
                ids += new
                summary["imported"] = len(new)
                for item in result.get("failed") or []:
                    summary["failed"].append(str(item))
                for path in ready:
                    handled.add(key(path))
                    tries.pop(key(path), None)
                    seen.pop(key(path), None)
                log(f"watch: imported {len(new)} of {len(ready)}" +
                    (f", not imported: {summary['failed']}" if summary["failed"] else ""))
            else:
                error = f"library.import: {reply.get('error')}"
                summary["failed"].append(error)
                log(f"watch ERROR {error}")
                for path in ready:
                    k = key(path)
                    tries[k] = tries.get(k, 0) + 1
                    if tries[k] >= MAX_TRIES:  # non si riprova all'infinito: resta da importare a mano
                        handled.add(k)
                        tries.pop(k)
                        log(f"watch: giving up on {path}")
        for path in arrived:
            handled.add(key(path))
        if ids:
            plan = matcher.build_plan(lib, "all", settings, finder, ids=ids)
            todo = [i for i in plan["items"] if i["status"] == "apply" and i["action"] == "apply"]
            if todo:
                result = matcher.apply_profiles(lib, cli, todo, root, kept, backup=backup)
                summary["profiles"] = result["done"]
                summary["failed"] += result["failed"]
    finally:
        if saved_view is not None:  # LightCraft si riapre sulla vista di prima
            with open(view, "wb") as f:
                f.write(saved_view)
        state["handled"] = sorted(handled)
        state["last"] = summary
        save_rule_state(rule, state)
    return summary


# ---------------------------------------------------------------- processo in background

def watch_settings(settings):
    w = settings.get("watch") or {}
    rules = [r for r in w.get("rules") or [] if r.get("folder") and r.get("library")]
    return bool(w.get("enabled")) and bool(rules), rules, max(1, min(60, int(w.get("interval") or DEFAULT_INTERVAL)))


def single_instance():
    """True se questo è l'unico processo in background. Il mutex è posseduto finché il processo vive: l'installazione
    guidata lo aspetta per sapere quando il processo si è chiuso."""
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, True, MUTEX)
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


_mutex = None


def stop_event():
    """Evento con cui l'installazione guidata chiede al processo di chiudersi (tra un giro e l'altro, mai a metà)."""
    return ctypes.windll.kernel32.CreateEventW(None, True, False, STOP_EVENT)


def run(once=False):
    if not single_instance():
        return 0
    stop = stop_event()
    log("watch: started")
    while True:
        if ctypes.windll.kernel32.WaitForSingleObject(stop, 0) == 0:
            log("watch: stopped (update or uninstall)")
            return 0
        settings = load_json(SETTINGS)
        enabled, rules, interval = watch_settings(settings)
        if not enabled:
            log("watch: stopped (automatic import is off)")
            return 0
        finder = cs.ProfileFinder(settings.get("profiles_dir"))
        for rule in rules:
            try:
                cycle(rule, settings, finder)
            except Exception as e:  # noqa: BLE001 — un errore non deve fermare il processo in background
                log(f"watch ERROR {rule.get('folder')}: {e!r}")
        save_json(STATUS, {"pid": os.getpid(), "checked": datetime.datetime.now().isoformat(timespec="seconds"),
                           "interval": interval})
        if once:
            return 0
        ctypes.windll.kernel32.WaitForSingleObject(stop, interval * 60 * 1000)  # pausa, interrotta dallo stop


def status():
    """Stato per la finestra: ultimo controllo del processo in background e ultime importazioni per regola."""
    return load_json(STATUS)
