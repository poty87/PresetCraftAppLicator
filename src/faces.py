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
        # si rifà anche quando cambiano le impostazioni dei nomi dati da soli (attivati, soglia)
        if st.get("snap") == sig and st.get("auto") == list(auto_config(settings)):
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
    try:  # nomi dati da soli ai volti molto somiglianti (se chiesto)
        auto_name(settings, cli, lib)
    except Exception as e:  # noqa: BLE001
        log(f"faces ERROR {name}: automatic names: {e!r}")
    if done == total:  # tutto fatto: fino alla prossima modifica del catalogo non si ricontrolla
        try:
            st["snap"] = catalog_signature(lib)
            st["auto"] = list(auto_config(settings))
        except OSError:
            pass
    save_json(STATE, state)


# ---------------------------------------------------------------- nomi dati da soli

AUTO_MARGIN = 0.20  # la seconda persona più somigliante deve stare almeno così sotto (fratelli, genitori e figli)
AUTO_MIN_KNOWN = 5  # volti a cui hai dato il nome tu, prima di dare quel nome da solo
AUTO_FILE = "faces-auto.json"


def auto_config(settings):
    """(attivo, somiglianza minima 0..1) dei nomi dati da soli (settings["faces"]: "auto", "auto_min")."""
    f = settings.get("faces") or {}
    return bool(f.get("auto")), min(0.99, max(0.5, float(f.get("auto_min") or 0.85)))


def auto_path(settings, lib):
    """Il registro dei nomi dati da soli: nella cartella della libreria (cartella di servizio), altrimenti sul PC."""
    import hashlib
    import watcher
    folder, _libs = watcher.phone_settings(settings)
    if folder:
        return os.path.join(watcher.library_dir(folder, lib), AUTO_FILE)
    digest = hashlib.sha1(os.path.normcase(os.path.normpath(lib)).encode()).hexdigest()[:8]
    return os.path.join(DATA_DIR, "watch", f"faces-auto-{digest}.json")


def rect_of(region):
    r = region.get("rect") or {}
    return [round(float(r.get(k, 0)), 4) for k in ("x0", "y0", "x1", "y1")]


def same_rect(a, b):
    return max(abs(float(x) - float(y)) for x, y in zip(a, b)) < 0.003


def load_auto(settings, lib):
    """[{id, rect, name, score, at}] dei nomi dati da soli e non ancora confermati o cambiati."""
    return [e for e in load_json(auto_path(settings, lib), {}).get("faces") or [] if isinstance(e, dict)]


def save_auto(settings, lib, entries):
    path = auto_path(settings, lib)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    save_json(path, {"version": 1, "library": os.path.basename(os.path.normpath(lib)), "faces": entries})


def current_auto(settings, lib, photos):
    """I nomi dati da soli che sono ancora come li ha lasciati PresetCraftAppLicator (non cambiati da te):
    {(id della foto, posizione del volto)}."""
    out = set()
    for e in load_auto(settings, lib):
        regions = ((photos.get(str(e.get("id"))) or {}).get("meta") or {}).get("regions") or []
        for i, r in enumerate(regions):
            if isinstance(r, dict) and same_rect(rect_of(r), e.get("rect") or [9, 9, 9, 9]) \
                    and (r.get("name") or "") == e.get("name"):
                out.add((int(e["id"]), i))
    return out


def forget_auto(settings, lib, faces):
    """Toglie dal registro i volti [(id, riquadro)] a cui hai dato (o confermato) il nome tu."""
    entries = load_auto(settings, lib)
    keep = [e for e in entries if not any(int(e.get("id", -1)) == pid and same_rect(e.get("rect") or [9] * 4, rect)
                                          for pid, rect in faces)]
    if len(keep) != len(entries):
        save_auto(settings, lib, keep)


def auto_name(settings, cli, lib):
    """Dà da solo il nome ai volti che somigliano molto a una persona già conosciuta (somiglianza almeno quella scelta,
    nessun dubbio con una seconda persona, almeno AUTO_MIN_KNOWN volti con quel nome dati da te). Con LightCraft chiuso,
    dopo un backup (uno al giorno). Ogni nome finisce nel registro, per riconoscerlo e per poterlo annullare."""
    on, minimum = auto_config(settings)
    if not on or library_open_reason(lib) or not detector_ready(cli):
        return 0
    photos = read_catalog(lib)["photos"]
    auto = current_auto(settings, lib, photos)
    known = {}
    for p in photos.values():
        if p.get("deleted"):
            continue
        for i, r in enumerate((p.get("meta") or {}).get("regions") or []):
            if isinstance(r, dict) and r.get("name") and (int(p["id"]), i) not in auto:
                known[r["name"]] = known.get(r["name"], 0) + 1
    ids = [int(p["id"]) for p in photos.values() if not p.get("deleted")
           and any(isinstance(r, dict) and not r.get("name") for r in (p.get("meta") or {}).get("regions") or [])]
    if not ids or not any(n >= AUTO_MIN_KNOWN for n in known.values()):
        return 0
    replies = run_cli(cli, lib, [("faces.suggest", {"ids": ids, "threshold": minimum, "budgetMs": 60000})],
                      check=False)
    commands, new = [], []
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for p in ((replies[-1].get("result") or {}).get("photos") or []) if replies else []:
        regions = ((photos.get(str(p["id"])) or {}).get("meta") or {}).get("regions") or []
        for f in p.get("faces") or []:
            s = f.get("suggestion") or {}
            score, name = float(s.get("score") or 0), s.get("name")
            runner = float((s.get("runnerUp") or {}).get("score") or 0)
            i = int(f.get("index", -1))
            if not name or score < minimum or runner > score - AUTO_MARGIN or known.get(name, 0) < AUTO_MIN_KNOWN:
                continue
            if not 0 <= i < len(regions) or regions[i].get("name"):
                continue
            commands.append(("faces.setName", {"id": int(p["id"]), "index": i, "name": name}))
            new.append({"id": int(p["id"]), "rect": rect_of(regions[i]), "name": name, "score": round(score, 3),
                        "at": now})
    if not commands:
        return 0
    state = load_json(STATE)
    st = state.setdefault(lib, {})
    today = datetime.date.today().isoformat()
    if st.get("backup") != today:
        backup_library(lib, backup_root(lib, settings), settings.get("backups_kept", BACKUPS_KEPT))
        st["backup"] = today
        save_json(STATE, state)
    view = os.path.join(lib, "view.json")
    try:
        with open(view, "rb") as fh:
            saved_view = fh.read()
    except OSError:
        saved_view = None
    try:
        replies = run_cli(cli, lib, commands, check=False)
    finally:
        if saved_view is not None:
            with open(view, "wb") as fh:
                fh.write(saved_view)
    done = [e for e, r in zip(new, replies) if r.get("ok")]
    save_auto(settings, lib, load_auto(settings, lib) + done)
    log(f"faces: {os.path.basename(os.path.normpath(lib))}: {len(done)} faces named automatically "
        f"(similarity >= {minimum:.2f})")
    return len(done)


def undo_auto(settings, cli, lib):
    """Toglie i nomi dati da soli e mai confermati o cambiati da te. Restituisce quanti; None se LightCraft è aperto."""
    if library_open_reason(lib):
        return None
    photos = read_catalog(lib)["photos"]
    auto = current_auto(settings, lib, photos)
    commands = [("faces.setName", {"id": pid, "index": i, "name": ""}) for pid, i in sorted(auto)]
    if commands:
        backup_library(lib, backup_root(lib, settings), settings.get("backups_kept", BACKUPS_KEPT))
        run_cli(cli, lib, commands, check=False)
        log(f"faces: {os.path.basename(os.path.normpath(lib))}: {len(commands)} automatic names removed")
    save_auto(settings, lib, [])
    return len(commands)
