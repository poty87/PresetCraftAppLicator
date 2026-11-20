"""Libreria LightCraft: lettura del catalogo, comandi con lightcraft-cli, backup.

Le foto si leggono da catalog.snap; le modifiche alla libreria passano sempre da lightcraft-cli,
mai scrivendo i file della libreria a mano.
"""

import ctypes
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from ctypes import wintypes

from version import APP_NAME

LOCAL = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
DATA_DIR = os.path.join(LOCAL, APP_NAME)  # impostazioni, registro e cache dell'utente
DEFAULT_CLI = os.path.join(LOCAL, r"Programs\ArtCraft\LightCraft\lightcraft-cli.exe")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# file della libreria salvati prima di ogni modifica (prefs.json contiene l'elenco dei profili importati)
LIBRARY_FILES = ("catalog.snap", "catalog.log", "view.json", "prefs.json", "presets.json")
BACKUP_PREFIX = f"{APP_NAME} backup - "
STAMP = "%Y-%m-%d %H.%M.%S"
BACKUPS_KEPT = 5
DEFAULT_PROFILE = "lc.color"  # profilo che LightCraft dà alle foto appena importate
RECENT_DAYS = 30  # come "Recently Added" di LightCraft: i 30 giorni fino all'ultima importazione


def log(text):
    os.makedirs(DATA_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(DATA_DIR, "presetcraft.log"), "a", encoding="utf-8") as f:
        f.write(f"{stamp} {text}\n")


# lightcraft-cli avviati da PresetCraftAppLicator: non sono "LightCraft aperto". Quelli senza libreria (anteprime
# sviluppate) non toccano le librerie; quelli su una libreria (importazione, modifiche dal telefono, ricerca dei volti)
# nello stesso processo passano uno alla volta (run_cli), e per gli altri processi (la finestra dell'app) la libreria è
# "occupata da PresetCraftAppLicator". Tenuti in un file, perché tutti i processi li riconoscano: {pid: libreria o ""}
OWN_FILE = os.path.join(DATA_DIR, "watch", "own-cli.json")
OWN = {}  # quelli di questo processo
_own_lock = threading.Lock()


def _read_own_file():
    try:
        with open(OWN_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if isinstance(data, list):  # formato della 1.20
        return {int(x): "" for x in data}
    return {int(k): v or "" for k, v in data.items()} if isinstance(data, dict) else {}


def own_cli(pid, running, lib=""):
    """Segna (o toglie) un lightcraft-cli avviato da noi (lib: la libreria su cui lavora, "" se nessuna)."""
    with _own_lock:
        if running:
            OWN[pid] = lib or ""
        else:
            OWN.pop(pid, None)
        try:
            os.makedirs(os.path.dirname(OWN_FILE), exist_ok=True)
            # nel file restano anche quelli degli altri processi (ancora vivi: controllati da chi legge)
            data = {k: v for k, v in _read_own_file().items() if k not in OWN and k != pid}
            data.update(OWN)
            tmp = OWN_FILE + f".{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in data.items()}, f)
            os.replace(tmp, OWN_FILE)
        except OSError:
            pass


def own_pids():
    return set(OWN) | set(_read_own_file())


def _tasks():
    """{pid: nome in minuscolo} dei processi LightCraft in esecuzione."""
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    found = {}
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) < 2:
            continue
        name = parts[0].lower()
        if name in ("lightcraft.exe", "lightcraft-cli.exe"):
            try:
                found[int(parts[1])] = name
            except ValueError:
                pass
    return found


def lightcraft_running():
    """Nomi dei processi LightCraft in esecuzione (app o riga di comando), esclusi quelli avviati da noi."""
    own = own_pids()
    return sorted({name for pid, name in _tasks().items() if pid not in own})


def _same(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def library_open_reason(lib):
    """Chiave del testo che spiega perché la libreria non si può modificare adesso, oppure None."""
    tasks = _tasks()
    own = own_pids()
    if any(pid not in own for pid in tasks):
        return "st_open_app"
    # un nostro lightcraft-cli su questa libreria: di questo processo aspetta il suo turno (run_cli), di un altro
    # processo (il processo in background mentre la finestra è aperta) la rende occupata per un po'
    others = {pid for pid, l in _read_own_file().items() if l and _same(l, lib) and pid in tasks and pid not in OWN}
    if others:
        return "st_busy_pca"
    ours = any(l and _same(l, lib) for l in OWN.values())
    if os.path.exists(os.path.join(lib, "catalog.lock.owner")) and not ours:
        return "st_open_lock"
    return None


_signatures = {}  # catalogo -> (dimensione, data, impronta)


def catalog_signature(lib):
    """[dimensione, impronta del contenuto] di catalog.snap: cambia solo se cambia il catalogo. lightcraft-cli riscrive
    il file ogni volta che apre la libreria, anche solo per leggere: con la data i lavori in background si
    rimetterebbero in moto a vicenda. L'impronta si ricalcola solo se dimensione o data sono cambiate."""
    path = os.path.join(lib, "catalog.snap")
    st = os.stat(path)
    known = _signatures.get(path)
    if known and known[0] == st.st_size and known[1] == st.st_mtime_ns:
        return [st.st_size, known[2]]
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    _signatures[path] = (st.st_size, st.st_mtime_ns, h.hexdigest())
    return [st.st_size, h.hexdigest()]


def is_library(lib):
    return bool(lib) and os.path.isfile(os.path.join(lib, "catalog.snap"))


def is_cli(path):
    return bool(path) and os.path.isfile(path) and os.path.basename(path).lower() == "lightcraft-cli.exe"


def read_catalog(lib):
    with open(os.path.join(lib, "catalog.snap"), encoding="utf-8") as f:
        return json.load(f)["catalog"]


def read_prefs(lib):
    try:
        with open(os.path.join(lib, "prefs.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def imported_profiles(lib):
    """Profili già importati nella libreria: {id: {"name", "cameraModel"}}. Per le LUT il modello è il loro gruppo:
    PresetCraftAppLicator importa i profili Look di una fotocamera in un gruppo col nome della fotocamera."""
    prefs = read_prefs(lib)
    found = {p["id"]: p for p in prefs.get("dcpProfiles") or [] if p.get("id")}
    for p in prefs.get("lutProfiles") or []:
        if p.get("id"):
            found[p["id"]] = dict(p, cameraModel=p.get("group") or "")
    return found


def recent_from(photos):
    """Data da cui partono le foto "aggiunte di recente": 30 giorni prima dell'ultima importazione (come LightCraft)."""
    latest = max((p.get("imported") or "" for p in photos), default="")
    try:
        when = datetime.datetime.fromisoformat(latest[:19])
    except ValueError:
        return latest[:10]
    return (when - datetime.timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")


BATCH_GAP = datetime.timedelta(minutes=3)  # importazioni a pezzi più vicine di così sono un giro solo


def import_batches(photos):
    """Le importazioni della libreria, dalla più recente: [{"start", "end", "count"}]. LightCraft dà a tutte le foto
    di un'importazione la stessa ora ("imported", in UTC); i pezzi a meno di 3 minuti l'uno dall'altro (l'importazione
    automatica importa a gruppi) si uniscono in un giro solo."""
    times = sorted(p.get("imported") or "" for p in photos if p.get("imported"))
    batches = []
    for ts in times:
        try:
            when = datetime.datetime.fromisoformat(ts[:19])
        except ValueError:
            continue
        if batches and when - batches[-1]["_end"] < BATCH_GAP:
            batches[-1]["_end"] = when
            batches[-1]["end"] = ts[:19]
            batches[-1]["count"] += 1
        else:
            batches.append({"start": ts[:19], "end": ts[:19], "count": 1, "_end": when})
    for b in batches:
        del b["_end"]
    return batches[::-1]


def local_time(ts):
    """Un'ora di LightCraft (UTC, "2026-11-17T19:11:52") nell'ora del PC."""
    try:
        return datetime.datetime.fromisoformat(ts[:19]).replace(tzinfo=datetime.timezone.utc).astimezone()
    except ValueError:
        return None


def utc_text(local):
    """Un'ora del PC (datetime senza fuso) nel formato delle ore di LightCraft (UTC)."""
    return local.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def in_scope(imported, scope, batches):
    """La foto importata a quell'ora (UTC) rientra nella scelta "Foto da esaminare"? Vedi matcher.build_plan."""
    ts = (imported or "")[:19]
    if scope == "last":
        return bool(batches) and ts >= batches[0]["start"]
    if scope.startswith("since:"):
        return ts >= scope[6:]
    if scope.startswith("range:"):
        start, _sep, end = scope[6:].partition("|")
        return bool(ts) and (not start or ts >= start) and (not end or ts <= end)
    if scope.startswith("batches:"):
        return any(s <= ts <= e for s, _sep, e in (x.partition("/") for x in scope[8:].split(";") if x))
    return True


_cli_lock = threading.Lock()  # un solo lightcraft-cli alla volta su una libreria, da questo processo


def run_cli(cli, lib, commands, check=True):
    """Esegue comandi con lightcraft-cli sulla libreria; restituisce le risposte JSON.

    check=False restituisce le risposte anche se qualche comando è stato rifiutato. Nello stesso processo i comandi
    sulle librerie non si sovrappongono mai (importazione, modifiche dal telefono, ricerca dei volti).
    """
    with _cli_lock:
        return _run_cli(cli, lib, commands, check)


def _run_cli(cli, lib, commands, check):
    fd, script = tempfile.mkstemp(suffix=".jsonl", prefix="presetcraft-")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        for name, params in commands:
            f.write(json.dumps({"command": name, "params": params}) + "\n")
    try:
        p = subprocess.Popen([cli, "run", "--library", lib, "--script", script], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8", creationflags=NO_WINDOW)
        own_cli(p.pid, True, lib)
        try:
            out, err = p.communicate()
        finally:
            own_cli(p.pid, False)
        r = subprocess.CompletedProcess(p.args, p.returncode, out, err)
    finally:
        os.remove(script)
    replies = []
    for line in r.stdout.splitlines():
        try:
            replies.append(json.loads(line))
        except ValueError:
            pass
    if not replies or (check and (r.returncode != 0 or not all(x.get("ok") for x in replies))):
        # il motivo vero sta nella risposta del comando fallito; stderr dice solo "1 command(s) failed"
        errors = [f"{x.get('command')}: {x.get('error')}" for x in replies if not x.get("ok")]
        detail = "; ".join(errors) or r.stderr.strip()
        raise RuntimeError(f"lightcraft-cli ({r.returncode}): {detail}")
    return replies


# ---------------------------------------------------------------- backup

def backup_root(lib, settings):
    """Cartella dei backup di una libreria: accanto alla libreria o in quella scelta nelle Impostazioni."""
    base = settings.get("backup_dir") or os.path.dirname(lib)
    return os.path.join(base, BACKUP_PREFIX + os.path.basename(lib))


def backup_library(lib, root, kept=BACKUPS_KEPT):
    dest = os.path.join(root, datetime.datetime.now().strftime(STAMP))
    os.makedirs(dest)
    for name in LIBRARY_FILES:
        src = os.path.join(lib, name)
        if os.path.exists(src):
            shutil.copy2(src, dest)
    for old in [b["path"] for b in list_backups_in(root)][kept:]:
        shutil.rmtree(old, ignore_errors=True)
    return dest


def list_backups_in(root):
    """Backup trovati in una cartella, dal più recente: [{path, when, bytes}]."""
    found = []
    try:
        names = os.listdir(root)
    except OSError:
        return found
    for name in names:
        path = os.path.join(root, name)
        try:
            when = datetime.datetime.strptime(name, STAMP)
        except ValueError:
            continue
        if not os.path.isfile(os.path.join(path, "catalog.snap")):
            continue
        size = sum(os.path.getsize(os.path.join(path, f)) for f in os.listdir(path)
                   if os.path.isfile(os.path.join(path, f)))
        found.append({"path": path, "when": when, "bytes": size})
    return sorted(found, key=lambda b: b["when"], reverse=True)


def list_backups(lib, settings):
    """Tutti i backup della libreria: nella cartella scelta e accanto alla libreria."""
    roots = {backup_root(lib, settings), backup_root(lib, {})}
    return sorted((b for root in roots for b in list_backups_in(root)), key=lambda b: b["when"], reverse=True)


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


def recycle(path):
    """Sposta un file o una cartella nel Cestino di Windows (o di Synology, sul NAS)."""
    op = SHFILEOPSTRUCTW(wFunc=3, pFrom=path + "\0\0", fFlags=0x4 | 0x10 | 0x40 | 0x400)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted or os.path.exists(path):
        raise OSError(f"code {rc}")


def delete_backups(paths):
    """Sposta i backup nel Cestino; toglie le cartelle che restano vuote. Restituisce gli errori."""
    failed = []
    for path in paths:
        try:
            recycle(path)
            log(f"backup deleted {path}")
        except OSError as e:
            failed.append(f"{path}: {e}")
        root = os.path.dirname(path)
        try:
            if os.path.isdir(root) and not os.listdir(root):
                os.rmdir(root)
        except OSError:
            pass
    return failed
