"""Ricerca dei volti nelle librerie, in background, con il riconoscimento dei volti di LightCraft.

LightCraft trova i volti (modello YuNet) solo quando glielo si chiede. Qui, per le librerie condivise col telefono, si
chiede a lightcraft-cli di cercarli nelle foto mai analizzate (faces.detect), a blocchi, con LightCraft chiuso e dopo
un backup della libreria (uno al giorno): i riquadri trovati entrano nel catalogo come volti senza nome, l'indice del
telefono li porta a ViewerCraftStar e lì (o in LightCraft) si dà loro un nome.
Si fa solo se in LightCraft il riconoscimento dei volti è attivo e il modello che li trova è installato.
Le foto già analizzate (con o senza volti) si ricordano in DATA_DIR/watch/faces-state.json; quelle che hanno già dei
volti (per esempio arrivati da Lightroom) non si ricontrollano.
"""
import datetime
import json
import os
import subprocess
import threading
import time

from lightcraft import (BACKUPS_KEPT, DATA_DIR, DEFAULT_CLI, NO_WINDOW, backup_library, backup_root, catalog_signature,
                        is_cli,
                        library_open_reason, log, read_catalog, run_cli)

STATE = os.path.join(DATA_DIR, "watch", "faces-state.json")
STATUS = os.path.join(DATA_DIR, "watch", "faces-status.json")  # per la finestra dell'app
BATCH = 100  # foto per ogni esecuzione di lightcraft-cli (~30 s)
DETECTOR = "yunet-2023mar"
CHECK_EVERY = 3600  # secondi tra un controllo e l'altro dei modelli di LightCraft

_runner = None
_stop = threading.Event()
_models = {"at": 0, "ok": False}


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def enabled(settings):
    return bool((settings.get("faces") or {}).get("detect", True))


def detector_ready(cli):
    """Il riconoscimento dei volti è attivo in LightCraft e il modello che li trova è installato (controllo ogni ora)."""
    now = time.time()
    if now - _models["at"] < CHECK_EVERY:
        return _models["ok"]
    ok = False
    try:
        r = subprocess.run([cli, "run", "--demo", "faces.models.list"], capture_output=True, text=True,
                           encoding="utf-8", creationflags=NO_WINDOW, timeout=120)
        for line in r.stdout.splitlines():
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            res = reply.get("result") or {}
            if reply.get("ok") and isinstance(res, dict):
                ok = bool(res.get("enabled")) and any(m.get("id") == DETECTOR and m.get("installed")
                                                      for m in res.get("models") or [])
    except (OSError, subprocess.SubprocessError):
        ok = False
    _models.update(at=now, ok=ok)
    return ok


SUGGEST_THRESHOLD = 0.5  # somiglianza minima (provato sui volti con nome: 97% giusti fino a 0,4)


def suggestions(settings, lib):
    """{(id della foto, posizione del volto): (nome, somiglianza)} per i volti senza nome, dai volti a cui hai già dato
    un nome (riconoscimento di LightCraft; nessun nome viene dato). Vuoto se il riconoscimento non è attivo."""
    cli = settings.get("cli") or DEFAULT_CLI
    if not is_cli(cli) or not detector_ready(cli):
        return {}
    photos = read_catalog(lib)["photos"].values()
    regions = [r for p in photos if not p.get("deleted") for r in (p.get("meta") or {}).get("regions") or []]
    if not any(r.get("name") for r in regions) or all(r.get("name") for r in regions):
        return {}  # nessuno da cui imparare, o nessuno da indovinare
    ids = [int(p["id"]) for p in photos if not p.get("deleted")
           and any(not r.get("name") for r in (p.get("meta") or {}).get("regions") or [])]
    replies = run_cli(cli, lib, [("faces.suggest", {"ids": ids, "threshold": SUGGEST_THRESHOLD, "budgetMs": 60000})],
                      check=False)
    out = {}
    for p in ((replies[-1].get("result") or {}).get("photos") or []) if replies else []:
        for f in p.get("faces") or []:
            s = f.get("suggestion")
            if s and s.get("name"):
                out[(int(p["id"]), int(f["index"]))] = (s["name"], float(s.get("score") or 0))
    return out


def todo_of(lib, scanned):
    """Le foto da analizzare, dalla più recente: non nel cestino, foto o raw, senza volti, mai analizzate."""
    photos = [p for p in read_catalog(lib)["photos"].values()
              if not p.get("deleted") and p.get("kind") in ("raw", "image")
              and not (p.get("meta") or {}).get("regions") and int(p["id"]) not in scanned]
    photos.sort(key=lambda p: p.get("captured") or "", reverse=True)
    return [int(p["id"]) for p in photos]


def stop():
    _stop.set()


def running():
    return bool(_runner and _runner.is_alive())


def maybe_start(settings):
    """Avvia la ricerca se una libreria del telefono ha foto mai analizzate. True se l'ha avviata."""
    global _runner
    import watcher
    if running() or not enabled(settings):
        return False
    _folder, libs = watcher.phone_settings(settings)
    cli = settings.get("cli") or DEFAULT_CLI
    if not libs or not is_cli(cli):
        return False
    state = load_json(STATE)
    todo = []
    for lib in libs:
        if library_open_reason(lib):
            continue
        try:
            sig = catalog_signature(lib)
        except OSError:
            continue
        st = state.get(lib) or {}
        if st.get("snap") == sig:
            continue  # nessuna novità dall'ultima volta
        todo.append(lib)
    if not todo or not detector_ready(cli):
        return False
    _stop.clear()
    _runner = threading.Thread(target=_work, args=(settings, cli, todo), name="faces", daemon=True)
    _runner.start()
    return True


def _status(**values):
    try:
        save_json(STATUS, {"pid": os.getpid(), **values})
    except OSError:
        pass


def _work(settings, cli, libs):
    try:
        for lib in libs:
            if _stop.is_set():
                return
            try:
                _library(settings, cli, lib)
            except Exception as e:  # noqa: BLE001 — una libreria che non si legge non ferma le altre
                log(f"faces ERROR {os.path.basename(lib)}: {e!r}")
    finally:
        _status(running=False)


def _library(settings, cli, lib):
    name = os.path.basename(os.path.normpath(lib))
    state = load_json(STATE)
    st = state.setdefault(lib, {})
    scanned = set(st.get("scanned") or [])
    todo = todo_of(lib, scanned)
    total, done, found = len(todo), 0, 0
    if todo:
        log(f"faces: {name}: looking for faces in {total} photos")
    started = time.time()
    view = os.path.join(lib, "view.json")
    for start in range(0, total, BATCH):
        if _stop.is_set():
            break
        if library_open_reason(lib):
            log(f"faces: {name}: LightCraft opened, stopping ({done} of {total} done)")
            break
        batch = todo[start:start + BATCH]
        _status(running=True, library=name, done=done, total=total, started=started)
        today = datetime.date.today().isoformat()
        if st.get("backup") != today:
            backup_library(lib, backup_root(lib, settings), settings.get("backups_kept", BACKUPS_KEPT))
            st["backup"] = today
        try:
            with open(view, "rb") as f:
                saved_view = f.read()
        except OSError:
            saved_view = None
        try:
            replies = run_cli(cli, lib, [("library.select", {"ids": batch, "active": batch[0]}),
                                         ("faces.detect", {"ids": batch, "apply": True})], check=False)
        finally:
            if saved_view is not None:  # LightCraft si riapre sulla vista di prima
                with open(view, "wb") as f:
                    f.write(saved_view)
        reply = replies[-1] if replies else {}
        if not reply.get("ok"):
            log(f"faces ERROR {name}: {reply.get('error') or 'no reply'}")
            break
        photos = (reply.get("result") or {}).get("photos") or []
        found += sum(len(p.get("faces") or []) for p in photos)
        scanned.update(batch)
        done += len(batch)
        st["scanned"] = sorted(scanned)
        save_json(STATE, state)
    if done:
        log(f"faces: {name}: {done} photos checked, {found} faces found")
    # impronte dei volti (per i suggerimenti): LightCraft le fa anche da solo quando è aperto
    for _ in range(200):
        if _stop.is_set() or library_open_reason(lib):
            break
        replies = run_cli(cli, lib, [("faces.index", {"budgetMs": 60000})], check=False)
        res = (replies[-1].get("result") or {}) if replies else {}
        if not replies or not replies[-1].get("ok") or not res.get("pendingPhotos"):
            break
        _status(running=True, library=name, done=done, total=total, started=started)
    if done == total:  # tutto fatto: fino alla prossima modifica del catalogo non si ricontrolla
        try:
            st["snap"] = catalog_signature(lib)
        except OSError:
            pass
    save_json(STATE, state)
