"""Anteprime sviluppate per ViewerCraftStar.

Il telefono mostra l'anteprima che la fotocamera ha messo nel file: la foto com'era allo scatto. Per le foto ritoccate
in LightCraft (oltre al profilo della fotocamera che dà PresetCraftAppLicator, che riproduce proprio lo stile della
fotocamera) qui si prepara un'anteprima sviluppata con LightCraft stesso: lightcraft-cli apre la foto in una sessione
sua, senza la libreria, le dà le impostazioni di sviluppo lette dal catalogo e la esporta in JPEG.
Sola lettura su librerie e foto. Scrive nella cartella di servizio:
  libraries/<libreria>/previews/<id>.jpg   le anteprime (lato lungo 2048 pixel)
  libraries/<libreria>/previews.json       {"version", "size", "photos": {id: firma dello sviluppo}}
La firma cambia quando cambia lo sviluppo: l'anteprima si rifà; se la foto torna com'era (o sparisce) si cancella.
Il lavoro gira in un thread del processo in background, a priorità minima, due foto alla volta.
"""
import concurrent.futures as cf
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time

from lightcraft import DATA_DIR, DEFAULT_CLI, NO_WINDOW, is_cli, library_open_reason, log, read_catalog, read_prefs

PREVIEWS_DIR = "previews"
LIST_FILE = "previews.json"
SIZE = 2048  # lato lungo: nitida a schermo intero sul telefono, ~600 KB
QUALITY = 82
FORMAT = 1  # cambia se cambia il modo di fare le anteprime: tutte si rifanno
WORKERS = 2
MAX_TRIES = 3
IDLE = 0x00000040  # IDLE_PRIORITY_CLASS: il PC resta libero per chi lo usa
STATE = os.path.join(DATA_DIR, "watch", "previews-state.json")  # per libreria: catalogo già fatto
STATUS = os.path.join(DATA_DIR, "watch", "previews-status.json")  # per la finestra dell'app
DEFAULTS = os.path.join(DATA_DIR, "watch", "develop-defaults.json")
IGNORED = ("version", "process")  # numeri di versione delle impostazioni: non cambiano l'aspetto


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def enabled(settings):
    """Anteprime attive (settings["phone"]["previews"], di base sì). settings["phone"]["previews_libraries"], se c'è,
    limita il lavoro a quelle librerie."""
    return bool((settings.get("phone") or {}).get("previews", True))


# ---------------------------------------------------------------- quali foto

def _empty(v):
    return v is None or v == [] or v == {} or v == ""


def differs(a, b):
    """True se due impostazioni di sviluppo danno un aspetto diverso (vuoto = mancante, decimali arrotondati)."""
    if isinstance(a, dict) or isinstance(b, dict):
        a, b = a or {}, b or {}
        if not isinstance(a, dict) or not isinstance(b, dict):
            return True
        return any(differs(a.get(k), b.get(k)) for k in set(a) | set(b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) > 1e-6
    if _empty(a) and _empty(b):
        return False
    return a != b


def normalized(dev):
    """Lo sviluppo senza profilo e numeri di versione; il bilanciamento "come scattato" vale qualunque numero."""
    d = {k: v for k, v in (dev or {}).items() if k not in IGNORED and k != "profile"}
    wb = d.get("wb") or {}
    if wb.get("mode") == "asShot":
        d["wb"] = {"mode": "asShot"}
    return d


def profile_id(dev):
    return ((dev or {}).get("profile") or {}).get("id")


def cli_signature(cli):
    st = os.stat(cli)
    return f"{st.st_size}-{int(st.st_mtime)}"


def default_develop(cli, photo, cache):
    """Lo sviluppo che LightCraft dà a una foto appena importata (per formato, fotocamera e obiettivo), chiesto una
    volta a lightcraft-cli e ricordato."""
    meta = photo.get("meta") or {}
    k = "|".join([cli_signature(cli), str(photo.get("format") or ""), str(meta.get("make") or ""),
                  str(meta.get("camera") or ""), str(meta.get("lens") or "")])
    if k in cache:
        return cache[k]
    path = (photo.get("source") or {}).get("path") or ""
    r = subprocess.run([cli, "run", "--import", path, "develop.get"], capture_output=True, text=True,
                       encoding="utf-8", creationflags=NO_WINDOW | IDLE, timeout=300)
    for line in reversed(r.stdout.splitlines()):
        try:
            reply = json.loads(line)
        except ValueError:
            continue
        if reply.get("ok") and isinstance(reply.get("result"), dict):
            cache[k] = reply["result"]
            return cache[k]
    return None


def signature(photo):
    data = {"d": {k: v for k, v in (photo.get("develop") or {}).items()}, "h": photo.get("content_hash"),
            "z": photo.get("file_size"), "s": SIZE, "f": FORMAT}
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def plan(lib, settings, cli):
    """{id: (foto, firma)} delle foto che hanno bisogno di un'anteprima sviluppata."""
    cat = read_catalog(lib)
    cache = load_json(DEFAULTS)
    size0 = len(cache)
    want, profile_only = {}, []
    for p in cat["photos"].values():
        if p.get("deleted") or p.get("kind") not in ("raw", "image") or not p.get("edited"):
            continue
        if not (p.get("source") or {}).get("path"):
            continue
        dev = p.get("develop") or {}
        try:
            base = default_develop(cli, p, cache)
        except (OSError, subprocess.SubprocessError):
            base = None
        if base is None:
            continue  # file irraggiungibile: si riprova al giro dopo
        if differs(normalized(dev), normalized(base)):
            want[int(p["id"])] = p
        elif profile_id(dev) != profile_id(base):
            profile_only.append(p)
    if len(cache) != size0:
        save_json(DEFAULTS, cache)
    if profile_only:
        # solo il profilo è cambiato: se è quello della fotocamera dato da PresetCraftAppLicator (stato "ok"),
        # l'anteprima della fotocamera è già giusta; un profilo scelto a mano invece cambia l'aspetto
        import camera_styles as cs
        import matcher
        finder = cs.ProfileFinder(settings.get("profiles_dir"))
        ids = [int(p["id"]) for p in profile_only]
        auto = {it["id"] for it in matcher.build_plan(lib, "all", settings, finder, ids=ids)["items"]
                if it["status"] == "ok"}
        for p in profile_only:
            if int(p["id"]) not in auto:
                want[int(p["id"])] = p
    return {pid: (p, signature(p)) for pid, p in want.items()}


# ---------------------------------------------------------------- sviluppo di una foto

def render(cli, photo, profiles, dest):
    """Sviluppa la foto con le sue impostazioni e scrive il JPEG in dest (in modo atomico)."""
    dev = json.loads(json.dumps(photo.get("develop") or {}))
    commands = []
    prof = profiles.get(profile_id(dev))
    if prof and prof.get("file"):
        commands.append({"command": "profile.import", "params": {"paths": [prof["file"]]}})
    work = tempfile.mkdtemp(prefix="presetcraft-preview-")
    try:
        out = os.path.join(work, "preview.jpg")
        script = os.path.join(work, "script.jsonl")
        tail = [{"command": "develop.merge", "params": {"settings": dev}},
                {"command": "app.export", "params": {"path": out, "longEdge": SIZE, "quality": QUALITY,
                                                     "metadata": "none", "conflict": "overwrite"}}]
        # il profilo importato nella sessione ha un nome diverso da quello della libreria: lo si chiede a LightCraft
        # la prima volta (per ogni file di profilo) e poi si ricorda
        if commands:
            imported = _imported_ids.get(prof["file"])
            if not imported:
                r = _run(cli, photo, work, commands)
                imported = ((r[0].get("result") or {}).get("imported") or [{}])[0].get("id") if r else None
                if not imported:
                    raise RuntimeError(f"profile not imported: {prof.get('file')}")
                _imported_ids[prof["file"]] = imported
            dev["profile"] = dict(dev.get("profile") or {}, id=imported)
            tail[0]["params"]["settings"] = dev
        with open(script, "w", encoding="utf-8", newline="\n") as f:
            for c in commands + tail:
                f.write(json.dumps(c) + "\n")
        r = subprocess.run([cli, "run", "--import", photo["source"]["path"], "--script", script],
                           capture_output=True, text=True, encoding="utf-8", creationflags=NO_WINDOW | IDLE,
                           timeout=900)
        if r.returncode != 0 or not os.path.isfile(out):
            raise RuntimeError(f"lightcraft-cli ({r.returncode}): {(r.stdout + r.stderr)[-300:]}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"
        shutil.copyfile(out, tmp)
        os.replace(tmp, dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)


_imported_ids = {}  # file del profilo -> nome che gli dà una sessione di lightcraft-cli


def _run(cli, photo, work, commands):
    script = os.path.join(work, "probe.jsonl")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        for c in commands:
            f.write(json.dumps(c) + "\n")
    r = subprocess.run([cli, "run", "--import", photo["source"]["path"], "--script", script],
                       capture_output=True, text=True, encoding="utf-8", creationflags=NO_WINDOW | IDLE, timeout=300)
    out = []
    for line in r.stdout.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


# ---------------------------------------------------------------- il lavoro in background

_runner = None
_stop = threading.Event()


def stop():
    _stop.set()


def running():
    return bool(_runner and _runner.is_alive())


def snap_signature(lib):
    st = os.stat(os.path.join(lib, "catalog.snap"))
    return [st.st_size, st.st_mtime_ns, FORMAT]


def maybe_start(settings):
    """Avvia il lavoro se una libreria del telefono è cambiata dall'ultima volta. True se l'ha avviato."""
    global _runner
    import watcher
    if running() or not enabled(settings):
        return False
    folder, libs = watcher.phone_settings(settings)
    only = (settings.get("phone") or {}).get("previews_libraries")
    if only is not None:  # solo alcune librerie (per esempio per provare su una piccola prima delle grandi)
        keys = {watcher.key(x) for x in only}
        libs = [lib for lib in libs if watcher.key(lib) in keys]
    cli = settings.get("cli") or DEFAULT_CLI
    if not libs or not is_cli(cli) or watcher.old_layout_folders(folder):
        return False
    state = load_json(STATE)
    todo = []
    for lib in libs:
        try:
            sig = snap_signature(lib)
        except OSError:
            continue
        if state.get(lib) == sig or library_open_reason(lib):
            continue  # già fatta, o LightCraft aperto (il catalogo sul disco non è ancora aggiornato)
        todo.append((lib, sig))
    if not todo:
        return False
    _stop.clear()
    _runner = threading.Thread(target=_work, args=(settings, folder, cli, todo), name="previews", daemon=True)
    _runner.start()
    return True


def _status(**values):
    try:
        save_json(STATUS, {"pid": os.getpid(), **values})
    except OSError:
        pass


def _work(settings, folder, cli, todo):
    import watcher
    try:
        for lib, sig in todo:
            if _stop.is_set():
                return
            name = os.path.basename(os.path.normpath(lib))
            try:
                done = _library(settings, watcher.library_dir(folder, lib), lib, name, cli)
            except Exception as e:  # noqa: BLE001 — una libreria che non si legge non ferma le altre
                log(f"previews ERROR {name}: {e!r}")
                done = False
            if done:
                state = load_json(STATE)
                state[lib] = sig
                save_json(STATE, state)
    finally:
        _status(running=False)


def _library(settings, libdir, lib, name, cli):
    """Allinea le anteprime di una libreria. True se è tutto fatto (nessun errore, non interrotto)."""
    started = time.time()
    _status(running=True, library=name, phase="plan", done=0, total=0, started=started)
    want = plan(lib, settings, cli)
    list_path = os.path.join(libdir, LIST_FILE)
    current = load_json(list_path, {})
    photos = {int(k): v for k, v in (current.get("photos") or {}).items()} if current.get("size") == SIZE else {}
    # foto non riuscite: [firma, tentativi]; si riprovano (fino a MAX_TRIES) quando il catalogo cambia di nuovo
    failed = {int(k): v for k, v in (current.get("failed") or {}).items() if isinstance(v, list)}
    pdir = os.path.join(libdir, PREVIEWS_DIR)
    # anteprime che non servono più: foto ripristinate o tolte dalla libreria
    for pid in [pid for pid in photos if pid not in want]:
        try:
            os.remove(os.path.join(pdir, f"{pid}.jpg"))
        except OSError:
            pass
        photos.pop(pid, None)
    for pid in [pid for pid in failed if pid not in want or failed[pid][0] != want[pid][1]]:
        failed.pop(pid)  # foto cambiata o non più da fare: si riparte da zero
    jobs = [(pid, p, s) for pid, (p, s) in want.items()
            if photos.get(pid) != s and failed.get(pid, [s, 0])[1] < MAX_TRIES]
    lock = threading.Lock()
    state = {"done": 0, "saved": time.time(), "errors": 0}

    def save():
        os.makedirs(libdir, exist_ok=True)
        save_json(list_path, {"version": 1, "size": SIZE, "photos": {str(k): v for k, v in sorted(photos.items())},
                              "failed": {str(k): v for k, v in failed.items()}})

    if not jobs:
        if current.get("photos") != {str(k): v for k, v in sorted(photos.items())} or not os.path.exists(list_path):
            save()
        return True
    log(f"previews: {name}: {len(jobs)} to develop ({len(want)} edited photos)")
    _status(running=True, library=name, phase="render", done=0, total=len(jobs), started=started)
    profiles = {}
    prefs = read_prefs(lib)
    for p in (prefs.get("dcpProfiles") or []) + (prefs.get("lutProfiles") or []):
        if p.get("id"):
            profiles[p["id"]] = p

    def one(job):
        pid, photo, sig = job
        if _stop.is_set():
            return
        try:
            render(cli, photo, profiles, os.path.join(pdir, f"{pid}.jpg"))
            ok = True
        except Exception as e:  # noqa: BLE001
            log(f"previews ERROR {name} #{pid} {photo.get('file_name')}: {e!r}")
            ok = False
        with lock:
            if ok:
                photos[pid] = sig
                failed.pop(pid, None)
            else:
                failed[pid] = [sig, failed.get(pid, [sig, 0])[1] + 1]
                state["errors"] += 1
            state["done"] += 1
            if time.time() - state["saved"] > 20:
                save()
                state["saved"] = time.time()
            _status(running=True, library=name, phase="render", done=state["done"], total=len(jobs), started=started)

    with cf.ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(one, jobs))
    save()
    log(f"previews: {name}: {state['done'] - state['errors']} developed, {state['errors']} errors"
        + (" (stopped)" if _stop.is_set() else ""))
    return not _stop.is_set()
