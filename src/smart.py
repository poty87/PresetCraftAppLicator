"""Smart preview di LightCraft per lo sviluppo dal telefono, per TUTTE le foto delle librerie del telefono.

La smart preview è una copia compatta della foto (~1,7 MB) che LightCraft tiene per la libreria: resta modificabile
anche senza l'originale. ViewerCraftStar la scarica quando apri «Modifica» e la sviluppa con lo stesso motore di
LightCraft, così il telefono vede esattamente ciò che vedrà il PC.

Per ogni libreria del telefono, nel processo in background (thread, LightCraft chiuso per i passi che toccano la
libreria):
  1. spostamento, una volta sola: le smart preview passano dalla cartella della libreria a quella di servizio,
     <cartella>/libraries/<libreria>/smart/<2 cifre>/<nome>.lcsp. PresetCraftAppLicator le copia (riprendendo da dove
     era rimasto, file per file in modo sicuro), poi dice a LightCraft di usare la cartella nuova
     (library.smartPreviewsLocation) e infine toglie le copie vecchie;
  2. pulizia: le smart preview di foto che non sono più nella libreria (cestino svuotato, CleanCraftNas) si cancellano,
     come le anteprime; LightCraft non lo fa da solo (provato il 25/11/2026). Il nome del file si calcola qui come in
     LightCraft (crates/engine/src/smart.rs: SipHash-1-3 a 128 bit della chiave di contenuto);
  3. creazione: le foto che non ce l'hanno la ricevono con library.smartPreviews {ids}, a gruppi (si può fermare tra
     un gruppo e l'altro).
Le foto nel cestino di LightCraft tengono la loro smart preview (si possono ripristinare) ma non se ne fanno di nuove.
"""
import datetime
import json
import os
import shutil
import struct
import threading
import time

from lightcraft import (BACKUPS_KEPT, DATA_DIR, DEFAULT_CLI, backup_library, backup_root, catalog_signature, is_cli,
                        library_open_reason, log, read_catalog, run_cli)

SMART_DIR = "smart"
LOOK_MARKER = "look-version"  # file di LightCraft nella cartella delle smart preview (smart.rs: LOOK_MARKER)
FORMAT = 1  # cambia se cambia il modo di lavorare: tutte le librerie si ricontrollano
BATCH = 50  # foto per giro di lightcraft-cli (~45 s): tra un giro e l'altro si può fermare
MAX_TRIES = 3
ORPHAN_MIN_AGE = 3600  # un file più giovane di così non si tocca (potrebbe essere di una foto appena importata)
ORPHAN_MAX_SHARE = 0.5  # se risultassero orfane più di metà dei file, il calcolo dei nomi non torna: non si cancella
STATE = os.path.join(DATA_DIR, "watch", "smart-state.json")
STATUS = os.path.join(DATA_DIR, "watch", "smart-status.json")

_runner = None
_stop = threading.Event()


# ---------------------------------------------------------------- nome del file (come LightCraft)

_M = 0xFFFFFFFFFFFFFFFF
_K0, _K1 = 0x4C69_6768_7443_7261, 0x6674_2043_6F6E_7465  # chiavi di lightcraft_preview::Hasher128


def _rotl(x, b):
    return ((x << b) | (x >> (64 - b))) & _M


def _sipround(v0, v1, v2, v3):
    v0 = (v0 + v1) & _M
    v1 = _rotl(v1, 13) ^ v0
    v0 = _rotl(v0, 32)
    v2 = (v2 + v3) & _M
    v3 = _rotl(v3, 16) ^ v2
    v0 = (v0 + v3) & _M
    v3 = _rotl(v3, 21) ^ v0
    v2 = (v2 + v1) & _M
    v1 = _rotl(v1, 17) ^ v2
    v2 = _rotl(v2, 32)
    return v0, v1, v2, v3


def siphash13_128(data):
    """SipHash-1-3 a 128 bit (come la crate siphasher) con le chiavi di LightCraft: (h1, h2)."""
    v0 = _K0 ^ 0x736F6D6570736575
    v1 = _K1 ^ 0x646F72616E646F6D ^ 0xEE
    v2 = _K0 ^ 0x6C7967656E657261
    v3 = _K1 ^ 0x7465646279746573
    n = len(data)
    end = n - n % 8
    for i in range(0, end, 8):
        m = struct.unpack_from("<Q", data, i)[0]
        v3 ^= m
        v0, v1, v2, v3 = _sipround(v0, v1, v2, v3)
        v0 ^= m
    b = (n & 0xFF) << 56
    for j, c in enumerate(data[end:]):
        b |= c << (8 * j)
    v3 ^= b
    v0, v1, v2, v3 = _sipround(v0, v1, v2, v3)
    v0 ^= b
    v2 ^= 0xEE
    for _ in range(3):
        v0, v1, v2, v3 = _sipround(v0, v1, v2, v3)
    h1 = v0 ^ v1 ^ v2 ^ v3
    v1 ^= 0xDD
    for _ in range(3):
        v0, v1, v2, v3 = _sipround(v0, v1, v2, v3)
    return h1, v0 ^ v1 ^ v2 ^ v3


def content_key(photo):
    """lightcraft_engine::media::content_key: l'impronta del contenuto, altrimenti percorso e dimensione."""
    if photo.get("content_hash"):
        return photo["content_hash"]
    return f"file:{(photo.get('source') or {}).get('path')}:{photo.get('file_size')}"


def file_name(photo):
    """Il nome del file della smart preview di una foto (le copie virtuali condividono quella dell'originale)."""
    s = content_key(photo).encode("utf-8")
    h1, h2 = siphash13_128(struct.pack("<Q", len(s)) + s)
    return f"{(h2 << 64) | h1:032x}.lcsp"


def rel_path(photo):
    """Percorso dentro la cartella delle smart preview: <prime 2 cifre>/<nome>."""
    name = file_name(photo)
    return os.path.join(name[:2], name)


# ---------------------------------------------------------------- utilità

def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def enabled(settings):
    """Smart preview per il telefono (settings["phone"]["smart"], di base sì)."""
    return bool((settings.get("phone") or {}).get("smart", True))


def cleanup_enabled(settings):
    """Pulizia delle smart preview orfane (settings["phone"]["smart_cleanup"], di base sì)."""
    return bool((settings.get("phone") or {}).get("smart_cleanup", True))


def target_dir(folder, lib):
    import watcher
    return os.path.join(watcher.library_dir(folder, lib), SMART_DIR)


def same_dir(a, b):
    return os.path.normcase(os.path.normpath(a or "")) == os.path.normcase(os.path.normpath(b or ""))


def listing(folder):
    """{percorso relativo: (dimensione, data)} dei .lcsp in folder (anche nella disposizione vecchia, senza
    sottocartelle)."""
    out = {}
    if not os.path.isdir(folder):
        return out
    for d, _sub, files in os.walk(folder):
        for f in files:
            if f.endswith(".lcsp"):
                p = os.path.join(d, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out[os.path.relpath(p, folder)] = (st.st_size, st.st_mtime)
    return out


def copy_durable(src, dest):
    """Copia sicura: file temporaneo scritto sul disco, poi il nome giusto."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        shutil.copyfileobj(fi, fo, 4 << 20)
        fo.flush()
        os.fsync(fo.fileno())
    st = os.stat(src)
    os.utime(tmp, (st.st_atime, st.st_mtime))  # la data vera: la pulizia delle orfane guarda quanto sono vecchie
    os.replace(tmp, dest)


def cli_json(cli, lib, commands):
    """Risposte di lightcraft-cli (accetta errori: il chiamante guarda "ok")."""
    return run_cli(cli, lib, commands, check=False)


def location(cli, lib):
    """Dove la libreria tiene le smart preview: (percorso, personalizzato)."""
    r = cli_json(cli, lib, [("library.smartPreviewsLocation", {})])
    res = (r[0].get("result") or {}) if r and r[0].get("ok") else {}
    return res.get("path"), bool(res.get("custom"))


# ---------------------------------------------------------------- il lavoro in background

def stop():
    _stop.set()


def running():
    return bool(_runner and _runner.is_alive())


def signature(lib):
    return catalog_signature(lib) + [FORMAT]


def maybe_start(settings):
    """Avvia il lavoro se una libreria del telefono è cambiata dall'ultima volta. True se l'ha avviato."""
    global _runner
    import watcher
    if running() or not enabled(settings):
        return False
    folder, libs = watcher.phone_settings(settings)
    cli = settings.get("cli") or DEFAULT_CLI
    if not folder or not libs or not is_cli(cli) or watcher.old_layout_folders(folder):
        return False
    state = load_json(STATE)
    todo = []
    for lib in libs:
        try:
            sig = signature(lib)
        except OSError:
            continue
        st = state.get(lib) or {}
        if st.get("sig") == sig and same_dir(st.get("dir"), target_dir(folder, lib)) or library_open_reason(lib):
            continue
        todo.append((lib, sig))
    if not todo:
        return False
    _stop.clear()
    _runner = threading.Thread(target=_work, args=(settings, folder, cli, todo), name="smart", daemon=True)
    _runner.start()
    return True


def _status(**values):
    try:
        save_json(STATUS, {"pid": os.getpid(), **values})
    except OSError:
        pass


def _work(settings, folder, cli, todo):
    try:
        for lib, sig in todo:
            if _stop.is_set():
                return
            name = os.path.basename(os.path.normpath(lib))
            try:
                done = _library(settings, folder, cli, lib, name)
            except Exception as e:  # noqa: BLE001 — una libreria che non si legge non ferma le altre
                log(f"smart ERROR {name}: {e!r}")
                done = False
            if done:
                state = load_json(STATE)
                st = state.setdefault(lib, {})
                st.update(sig=signature(lib), dir=target_dir(folder, lib),
                          checked=datetime.datetime.now().isoformat(timespec="seconds"))
                save_json(STATE, state)
    finally:
        _status(running=False)


def _library(settings, folder, cli, lib, name):
    """Sposta, pulisce e completa le smart preview di una libreria. True se è tutto fatto."""
    started = time.time()
    dest = target_dir(folder, lib)
    if not relocate(settings, cli, lib, name, dest, started):
        return False
    cat = read_catalog(lib)
    photos = [p for p in cat["photos"].values() if p.get("kind") in ("raw", "image")]
    files = listing(dest)
    if cleanup_enabled(settings):
        remove_orphans(name, dest, photos, files)
        files = listing(dest)
    return build_missing(cli, lib, name, dest, photos, files, started)


def relocate(settings, cli, lib, name, dest, started):
    """Le smart preview passano nella cartella di servizio (una volta sola). True se ora stanno lì."""
    if library_open_reason(lib):
        return False
    old, _custom = location(cli, lib)
    if not old:
        log(f"smart ERROR {name}: location not readable")
        return False
    if same_dir(old, dest):
        return True
    os.makedirs(dest, exist_ok=True)
    src = listing(old)
    have = listing(dest)
    todo = [rel for rel, (size, _t) in src.items() if (have.get(_sharded(rel)) or (None,))[0] != size]
    if todo:
        log(f"smart: {name}: copying {len(todo)} of {len(src)} smart previews to the service folder")
    for i, rel in enumerate(todo, 1):
        if _stop.is_set():
            return False
        try:
            copy_durable(os.path.join(old, rel), os.path.join(dest, _sharded(rel)))
        except OSError as e:
            log(f"smart ERROR {name}: copying {rel}: {e!r}")
            return False
        if i % 100 == 0 or i == len(todo):
            _status(running=True, library=name, phase="move", done=i, total=len(todo), started=started)
    if _stop.is_set() or library_open_reason(lib):
        return False
    # le ultime arrivate mentre si copiava (LightCraft aperto nel frattempo): un giro veloce prima di cambiare
    src = listing(old)
    have = listing(dest)
    for rel, (size, _t) in src.items():
        if (have.get(_sharded(rel)) or (None,))[0] != size:
            copy_durable(os.path.join(old, rel), os.path.join(dest, _sharded(rel)))
    # il segno di LightCraft con la versione con cui sono state fatte (senza, le ricontrollerebbe tutte)
    marker = os.path.join(old, LOOK_MARKER)
    if os.path.isfile(marker) and not os.path.isfile(os.path.join(dest, LOOK_MARKER)):
        copy_durable(marker, os.path.join(dest, LOOK_MARKER))
    # cambia un'impostazione della libreria: prima un backup, come per ogni modifica
    backup_library(lib, backup_root(lib, settings), settings.get("backups_kept", BACKUPS_KEPT))
    r = cli_json(cli, lib, [("library.smartPreviewsLocation", {"path": dest.replace("\\", "/"), "existing": "leave"})])
    res = (r[0].get("result") or {}) if r and r[0].get("ok") else {}
    if not same_dir(res.get("path"), dest):
        log(f"smart ERROR {name}: LightCraft did not take the new folder ({(r[0] if r else {}).get('error')})")
        return False
    # copiate e verificate: le vecchie si tolgono
    have = listing(dest)
    removed = 0
    for rel, (size, _t) in src.items():
        if (have.get(_sharded(rel)) or (None,))[0] == size:
            try:
                os.remove(os.path.join(old, rel))
                removed += 1
            except OSError:
                pass
    # avanzi di LightCraft (il segno della versione, file temporanei), poi le cartelle rimaste vuote
    for d, _sub, files in sorted(os.walk(old), key=lambda x: -len(x[0])):
        for f in files:
            if f == LOOK_MARKER or f.endswith(".tmp"):
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
        try:
            os.rmdir(d)
        except OSError:
            pass
    log(f"smart: {name}: smart previews now in the service folder ({len(have)} files, {removed} old copies removed)")
    return True


def _sharded(rel):
    """Percorso nella disposizione con sottocartelle (quelle vecchie senza sottocartella ci finiscono dentro)."""
    name = os.path.basename(rel)
    return os.path.join(name[:2], name)


def remove_orphans(name, dest, photos, files):
    """Toglie le smart preview delle foto che non sono più nella libreria. Torna quante."""
    expected = {rel_path(p) for p in photos}
    now = time.time()
    orphans = [rel for rel, (_s, mtime) in files.items() if rel not in expected and now - mtime > ORPHAN_MIN_AGE]
    if not orphans:
        return 0
    matched = sum(1 for rel in files if rel in expected)
    if len(orphans) > ORPHAN_MAX_SHARE * len(files) or not matched:
        log(f"smart: {name}: {len(orphans)} of {len(files)} smart previews look unused: too many, nothing removed "
            "(file naming may have changed in LightCraft)")
        return 0
    removed = 0
    for rel in orphans:
        try:
            os.remove(os.path.join(dest, rel))
            removed += 1
        except OSError:
            pass
    log(f"smart: {name}: {removed} smart previews of photos no longer in the library removed")
    return removed


def build_missing(cli, lib, name, dest, photos, files, started):
    """Crea le smart preview che mancano (foto non nel cestino), a gruppi. True se non manca niente."""
    state = load_json(STATE)
    failed = {int(k): v for k, v in ((state.get(lib) or {}).get("failed") or {}).items()}
    seen, todo = set(), []
    for p in photos:
        rel = rel_path(p)
        if p.get("deleted") or rel in files or rel in seen or failed.get(int(p["id"]), 0) >= MAX_TRIES:
            continue
        seen.add(rel)  # le copie virtuali condividono la smart preview dell'originale: una sola
        todo.append(p)
    if not todo:
        return True
    todo.sort(key=lambda p: p.get("captured") or "", reverse=True)  # prima le più recenti
    log(f"smart: {name}: {len(todo)} smart previews to build")
    done = 0
    for i in range(0, len(todo), BATCH):
        if _stop.is_set() or library_open_reason(lib):
            break
        batch = todo[i:i + BATCH]
        _status(running=True, library=name, phase="build", done=done, total=len(todo), started=started)
        r = cli_json(cli, lib, [("library.smartPreviews", {"ids": [int(p["id"]) for p in batch]})])
        if not r or not r[0].get("ok"):
            log(f"smart ERROR {name}: {(r[0] if r else {}).get('error') or 'no reply'}")
            break
        bad = {int(x.get("id") if isinstance(x, dict) else x) for x in (r[0].get("result") or {}).get("failed") or []
               if (x.get("id") if isinstance(x, dict) else x) is not None}
        for pid in bad:
            failed[pid] = failed.get(pid, 0) + 1
        done += len(batch)
    st = load_json(STATE)
    st.setdefault(lib, {})["failed"] = {str(k): v for k, v in failed.items()}
    save_json(STATE, st)
    log(f"smart: {name}: {done} smart previews built" + (" (stopped)" if done < len(todo) else ""))
    return done >= len(todo)
