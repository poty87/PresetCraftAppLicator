"""Anteprime per ViewerCraftStar, per TUTTE le foto delle librerie del telefono.

Ogni foto si sviluppa con LightCraft stesso (lightcraft-cli la apre in una sessione sua, senza la libreria, le dà le
impostazioni di sviluppo lette dal catalogo e la esporta), così il telefono la vede esattamente come in LightCraft.
Dall'esportazione si ricavano due AVIF (a parità di qualità circa metà di un JPEG: misurato il 25/11/2026):
  libraries/<libreria>/previews/<id>.avif  anteprima grande (lato lungo 2560 pixel, come le Smart Preview di
                                           Lightroom) per lo schermo intero
  libraries/<libreria>/thumbs/<id>.avif    miniatura (lato lungo 512 pixel) per la galleria
  libraries/<libreria>/previews.json       {"version": 2, "format": "avif", "size", "thumb", "photos": {id: firma},
                                            "edited": [id delle foto ritoccate in LightCraft], "failed"}
La firma cambia quando cambiano lo sviluppo o il file: le anteprime si rifanno; se la foto sparisce si cancellano.
"edited" serve al telefono per la matita e per «Originale» (l'anteprima della fotocamera, com'era allo scatto).
Prossimo passo (sviluppo dal telefono): accanto, in smart/<id>.dng, un DNG ridotto e ancora modificabile; le anteprime
qui sopra restano solo da guardare e si rifanno quando arriva lo sviluppo nuovo (la firma cambia).
Sola lettura su librerie e foto. Il lavoro gira in un thread del processo in background, a priorità minima, due foto
alla volta, prima le più recenti.
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

from lightcraft import (DATA_DIR, DEFAULT_CLI, NO_WINDOW, catalog_signature, is_cli, library_open_reason, log, own_cli, read_catalog,
                        read_prefs)

PREVIEWS_DIR = "previews"
THUMBS_DIR = "thumbs"
LIST_FILE = "previews.json"
LIST_VERSION = 2
SIZE = 2560  # lato lungo: nitida a schermo intero sul telefono, ~450 KB in AVIF
THUMB = 512  # miniatura per la galleria, ~25 KB
EXPORT_QUALITY = 97  # il JPEG intermedio di LightCraft, poi ricompresso in AVIF: quasi senza perdita
AVIF_QUALITY = 80  # misurato: come un JPEG qualità ~90 a metà peso
THUMB_QUALITY = 72
AVIF_SPEED = 6
FORMAT = 2  # cambia se cambia il modo di fare le anteprime: tutte si rifanno
WORKERS = 2
MAX_TRIES = 3
IDLE = 0x00000040  # IDLE_PRIORITY_CLASS: il PC resta libero per chi lo usa
STATE = os.path.join(DATA_DIR, "watch", "previews-state.json")  # per libreria: catalogo già fatto
STATUS = os.path.join(DATA_DIR, "watch", "previews-status.json")  # per la finestra dell'app
DEFAULTS = os.path.join(DATA_DIR, "watch", "develop-defaults.json")
IGNORED = ("version", "process")  # numeri di versione delle impostazioni: non cambiano l'aspetto
# nitidezza e riduzione del rumore: in un'anteprima a 2048 pixel non si vedono. Da sole non bastano per svilupparla
# (le foto arrivate da Lightroom le hanno quasi tutte); se c'è altro, l'anteprima le comprende
INVISIBLE = ("detail",)


class _Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def run_cli(args, timeout):
    """lightcraft-cli senza libreria, a priorità minima; il suo pid è segnato come nostro mentre gira, così il processo in
    background non lo scambia per LightCraft aperto."""
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         creationflags=NO_WINDOW | IDLE)
    own_cli(p.pid, True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
    finally:
        own_cli(p.pid, False)
    return _Result(p.returncode, out, err)


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
        durable(f)
    os.replace(tmp, path)


def durable(f):
    """Il contenuto del file arriva davvero sul disco (del NAS) prima che il file prenda il suo nome: senza, un
    blackout lasciava il nome giusto e un file vuoto (successo il 25/11/2026 a un'anteprima)."""
    f.flush()
    os.fsync(f.fileno())


MIN_BYTES = 100  # un'anteprima più piccola di così è rovinata (vuota o tagliata)


def damaged(folder, pids):
    """Le foto, tra pids, il cui file <id>.avif in folder manca o è vuoto: una lettura sola della cartella."""
    sizes = {}
    try:
        with os.scandir(folder) as it:
            for e in it:
                if e.name.endswith(".avif"):
                    try:
                        sizes[e.name[:-5]] = e.stat().st_size
                    except OSError:
                        pass
    except OSError:
        pass
    return {pid for pid in pids if sizes.get(str(pid), 0) < MIN_BYTES}


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
    d = {k: v for k, v in (dev or {}).items() if k not in IGNORED and k not in INVISIBLE and k != "profile"}
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
    r = run_cli([cli, "run", "--import", path, "develop.get"], timeout=300)
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
            "z": photo.get("file_size"), "s": SIZE, "t": THUMB, "q": AVIF_QUALITY, "f": FORMAT}
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def plan(lib, settings, cli):
    """({id: (foto, firma)} di tutte le foto da mostrare sul telefono (anche quelle nel cestino), {id ritoccate})."""
    cat = read_catalog(lib)
    everything = {int(p["id"]): (p, signature(p)) for p in cat["photos"].values()
                  if p.get("kind") in ("raw", "image") and (p.get("source") or {}).get("path")}
    return everything, edited_ids(lib, cat, settings, cli)


def edited_ids(lib, cat, settings, cli):
    """Le foto ritoccate in LightCraft: lo sviluppo è diverso da quello che LightCraft dà a una foto appena importata
    (il profilo della fotocamera dato da PresetCraftAppLicator non conta: riproduce l'aspetto della fotocamera)."""
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
    return set(want)


# ---------------------------------------------------------------- sviluppo di una foto

def render(cli, photo, profiles, dest, thumb_dest):
    """Sviluppa la foto con le sue impostazioni e scrive l'anteprima AVIF in dest e la miniatura in thumb_dest (in modo
    atomico). Torna (larghezza, altezza) dell'anteprima."""
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
                {"command": "app.export", "params": {"path": out, "longEdge": SIZE, "quality": EXPORT_QUALITY,
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
        r = run_cli([cli, "run", "--import", photo["source"]["path"], "--script", script], timeout=900)
        if r.returncode != 0 or not os.path.isfile(out):
            raise RuntimeError(f"lightcraft-cli ({r.returncode}): {(r.stdout + r.stderr)[-300:]}")
        return encode(out, dest, thumb_dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def encode(src, dest, thumb_dest):
    """Dal JPEG esportato da LightCraft: l'anteprima AVIF (stessa misura) e la miniatura AVIF, scritte in modo
    atomico. Torna (larghezza, altezza)."""
    from PIL import Image
    with Image.open(src) as im:
        im = im.convert("RGB")
        size = im.size
        for d in (dest, thumb_dest):
            os.makedirs(os.path.dirname(d), exist_ok=True)
        tmp = dest + ".tmp"
        with open(tmp, "wb") as f:
            im.save(f, "AVIF", quality=AVIF_QUALITY, speed=AVIF_SPEED)
            durable(f)
        small = im.copy()
        small.thumbnail((THUMB, THUMB), Image.LANCZOS)
        ttmp = thumb_dest + ".tmp"
        with open(ttmp, "wb") as f:
            small.save(f, "AVIF", quality=THUMB_QUALITY, speed=AVIF_SPEED)
            durable(f)
    os.replace(tmp, dest)
    os.replace(ttmp, thumb_dest)
    return size


_imported_ids = {}  # file del profilo -> nome che gli dà una sessione di lightcraft-cli


def _run(cli, photo, work, commands):
    script = os.path.join(work, "probe.jsonl")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        for c in commands:
            f.write(json.dumps(c) + "\n")
    r = run_cli([cli, "run", "--import", photo["source"]["path"], "--script", script], timeout=300)
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
# una libreria più piccola è cambiata mentre si lavora su una grande: si lascia la grande (riprende dopo)
_yield = threading.Event()
_broken = {}  # librerie che non si sono lette: lib → firma (non fanno fermare le altre finché non cambiano di nuovo)


def stop():
    _stop.set()


def running():
    return bool(_runner and _runner.is_alive())


def snap_signature(lib):
    return catalog_signature(lib) + [FORMAT]


def catalog_size(lib):
    """Dimensione di catalog.snap: per fare prima le librerie piccole (le modifiche lì non aspettano ore dietro le grandi)."""
    try:
        return os.path.getsize(os.path.join(lib, "catalog.snap"))
    except OSError:
        return 0


def _smaller_changed(lib, libs):
    """Una libreria più piccola di `lib` ha il catalogo cambiato (e LightCraft chiuso): va fatta prima."""
    size = catalog_size(lib)
    state = load_json(STATE)
    for other in libs:
        if other == lib or catalog_size(other) >= size:
            continue
        try:
            sig = snap_signature(other)
            if state.get(other) != sig and _broken.get(other) != sig and not library_open_reason(other):
                return True
        except OSError:
            continue
    return False


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
    todo.sort(key=lambda t: catalog_size(t[0]))  # prima le librerie piccole
    _stop.clear()
    _yield.clear()
    _runner = threading.Thread(target=_work, args=(settings, folder, cli, todo, libs), name="previews", daemon=True)
    _runner.start()
    return True


def _status(**values):
    try:
        save_json(STATUS, {"pid": os.getpid(), **values})
    except OSError:
        pass


def _work(settings, folder, cli, todo, libs=()):
    import watcher
    try:
        for lib, sig in todo:
            if _stop.is_set() or _yield.is_set():
                return
            name = os.path.basename(os.path.normpath(lib))
            try:
                done = _library(settings, watcher.library_dir(folder, lib), lib, name, cli, libs)
            except Exception as e:  # noqa: BLE001 — una libreria che non si legge non ferma le altre
                log(f"previews ERROR {name}: {e!r}")
                _broken[lib] = sig
                done = False
            if done:
                state = load_json(STATE)
                state[lib] = sig
                save_json(STATE, state)
    finally:
        _status(running=False)


def _library(settings, libdir, lib, name, cli, libs=()):
    """Allinea le anteprime di una libreria. True se è tutto fatto (nessun errore, non interrotto)."""
    started = time.time()
    _status(running=True, library=name, phase="plan", done=0, total=0, started=started)
    want, edited = plan(lib, settings, cli)
    list_path = os.path.join(libdir, LIST_FILE)
    current = load_json(list_path, {})
    pdir = os.path.join(libdir, PREVIEWS_DIR)
    tdir = os.path.join(libdir, THUMBS_DIR)
    same_kind = current.get("version") == LIST_VERSION and current.get("size") == SIZE and current.get("thumb") == THUMB
    if not same_kind:
        # anteprime di un altro tipo (le JPEG da 2048 delle versioni prima della 1.27): via tutte, si rifanno
        for d in (pdir, tdir):
            for f in (os.listdir(d) if os.path.isdir(d) else []):
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
    photos = {int(k): v for k, v in (current.get("photos") or {}).items()} if same_kind else {}
    # anteprime segnate come fatte ma il cui file manca o è vuoto (un blackout mentre si scrivevano): si rifanno
    broken = damaged(pdir, photos) | damaged(tdir, photos)
    if broken:
        log(f"previews: {name}: {len(broken)} previews missing or empty, developing them again")
        for pid in broken:
            photos.pop(pid, None)
    # foto non riuscite: [firma, tentativi]; si riprovano (fino a MAX_TRIES) quando il catalogo cambia di nuovo
    failed = {int(k): v for k, v in (current.get("failed") or {}).items() if isinstance(v, list)}
    # anteprime che non servono più: foto tolte dalla libreria (cancellate da CleanCraftNas)
    for pid in [pid for pid in photos if pid not in want]:
        for d in (pdir, tdir):
            try:
                os.remove(os.path.join(d, f"{pid}.avif"))
            except OSError:
                pass
        photos.pop(pid, None)
    for pid in [pid for pid in failed if pid not in want or failed[pid][0] != want[pid][1]]:
        failed.pop(pid)  # foto cambiata o non più da fare: si riparte da zero
    jobs = [(pid, p, s) for pid, (p, s) in want.items()
            if photos.get(pid) != s and failed.get(pid, [s, 0])[1] < MAX_TRIES]
    jobs.sort(key=lambda j: j[1].get("captured") or "", reverse=True)  # prima le foto più recenti
    lock = threading.Lock()
    state = {"done": 0, "saved": time.time(), "errors": 0, "checked": time.time()}

    def done_edited():
        return sorted(pid for pid in edited if pid in photos)

    def save():
        os.makedirs(libdir, exist_ok=True)
        save_json(list_path, {"version": LIST_VERSION, "format": "avif", "size": SIZE, "thumb": THUMB,
                              "photos": {str(k): v for k, v in sorted(photos.items())}, "edited": done_edited(),
                              "failed": {str(k): v for k, v in failed.items()}})

    if not jobs:
        if (not same_kind or current.get("photos") != {str(k): v for k, v in sorted(photos.items())}
                or current.get("edited") != done_edited() or not os.path.exists(list_path)):
            save()
        return True
    log(f"previews: {name}: {len(jobs)} to develop ({len(want)} photos, {len(edited)} edited)")
    _status(running=True, library=name, phase="render", done=0, total=len(jobs), started=started)
    profiles = {}
    prefs = read_prefs(lib)
    for p in (prefs.get("dcpProfiles") or []) + (prefs.get("lutProfiles") or []):
        if p.get("id"):
            profiles[p["id"]] = p

    def one(job):
        pid, photo, sig = job
        if _stop.is_set() or _yield.is_set():
            return
        try:
            render(cli, photo, profiles, os.path.join(pdir, f"{pid}.avif"), os.path.join(tdir, f"{pid}.avif"))
            ok = True
        except Exception as e:  # noqa: BLE001
            if _stop.is_set() or _yield.is_set():
                return  # interrotta (spegnimento, LightCraft aperto…): si rifà la prossima volta, non è un errore
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
            if time.time() - state["checked"] > 60:
                state["checked"] = time.time()
                if _smaller_changed(lib, libs):
                    log(f"previews: {name}: a smaller library changed, doing it first")
                    _yield.set()
            _status(running=True, library=name, phase="render", done=state["done"], total=len(jobs), started=started)

    with cf.ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(one, jobs))
    save()
    log(f"previews: {name}: {state['done'] - state['errors']} developed, {state['errors']} errors"
        + (" (stopped)" if _stop.is_set() else " (paused for a smaller library)" if _yield.is_set() else ""))
    return not (_stop.is_set() or _yield.is_set())
