"""Conversione in DNG dei raw proprietari (CR2, NEF, ORF, ARW, RAF…) con Adobe DNG Converter.

Con l'opzione attiva (settings["dng"]["convert"]):
  - importazione automatica: un raw proprietario appena arrivato in una cartella osservata viene convertito in un DNG
    accanto a lui (stesso nome) e in LightCraft entra il DNG; l'originale resta nella cartella, segnato come già visto;
  - raw già nelle librerie (settings["dng"]["existing"]): in background, con LightCraft chiuso e dopo un backup, ogni
    raw proprietario viene convertito, il DNG importato al suo posto con stelle, bandierina, etichetta, testi, parole
    chiave, sviluppo, pila e album dell'originale, e l'originale va nel cestino di LightCraft (il file resta sul NAS).
Adobe DNG Converter è gratuito; PresetCraftAppLicator lo installa con winget (Windows chiede l'autorizzazione).
"""
import datetime
import glob
import json
import os
import shutil
import subprocess
import threading
import time

from lightcraft import (BACKUPS_KEPT, DATA_DIR, DEFAULT_CLI, NO_WINDOW, backup_library, backup_root, catalog_signature,
                        is_cli,
                        library_open_reason, log, read_catalog, run_cli)

# raw proprietari che LightCraft importa (vedi watcher.PHOTO_EXTS): il DNG e i formati non raw restano come sono
RAW_EXTS = {".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".sr2", ".srf", ".raf", ".orf", ".rw2", ".rwl", ".raw",
            ".pef", ".srw", ".iiq", ".mrw", ".x3f", ".kdc", ".mos", ".erf", ".3fr", ".fff", ".mef"}
WINGET_ID = "Adobe.DNGConverter"
TMP_DIR = ".pca-dng"  # cartella nascosta (il watcher non guarda nelle cartelle che cominciano con il punto)
STATE = os.path.join(DATA_DIR, "watch", "dng-state.json")
BATCH = 20  # raw per giro: una conversione ciascuno, poi un'importazione e un giro di comandi per tutti
STATUS = os.path.join(DATA_DIR, "watch", "dng-status.json")

_runner = None
_stop = threading.Event()


def config(settings):
    return settings.get("dng") or {}


def enabled(settings):
    return bool(config(settings).get("convert"))


def exe():
    """Il percorso di Adobe DNG Converter, oppure None se non è installato."""
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
        if base:
            p = os.path.join(base, "Adobe", "Adobe DNG Converter", "Adobe DNG Converter.exe")
            if os.path.isfile(p):
                return p
    return None


def install():
    """Installa Adobe DNG Converter con winget (Windows chiede l'autorizzazione). (ok, messaggio)."""
    try:
        r = subprocess.run(["winget", "install", "--id", WINGET_ID, "-e", "--silent", "--accept-package-agreements",
                            "--accept-source-agreements"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW, timeout=1800)
    except (OSError, subprocess.SubprocessError) as e:
        return False, repr(e)
    ok = exe() is not None
    log(f"dng: winget install {WINGET_ID}: {'ok' if ok else 'failed'} ({r.returncode})")
    return ok, (r.stdout + r.stderr)[-400:]


def convertible(path):
    return os.path.splitext(path)[1].lower() in RAW_EXTS


def target_for(src):
    """Il DNG da scrivere accanto al raw: stesso nome, oppure "-dng" in più se un file con quel nome c'è già."""
    stem = os.path.splitext(src)[0]
    dest = stem + ".dng"
    n = 1
    while os.path.exists(dest):
        dest = f"{stem}-dng{'' if n == 1 else n}.dng"
        n += 1
    return dest


def convert(src):
    """Converte un raw in DNG accanto a lui (compressione senza perdita, anteprima a piena risoluzione per il
    telefono). Il DNG compare solo completo (scritto prima in una cartella nascosta). Restituisce il suo percorso."""
    tool = exe()
    if not tool:
        raise RuntimeError("Adobe DNG Converter not installed")
    folder = os.path.dirname(src)
    tmp = os.path.join(folder, TMP_DIR)
    os.makedirs(tmp, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]
    produced = os.path.join(tmp, stem + ".dng")
    try:
        if os.path.exists(produced):
            os.remove(produced)
        r = subprocess.run([tool, "-c", "-p2", "-d", tmp, src], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW, timeout=600)
        if not os.path.isfile(produced) or os.path.getsize(produced) == 0:
            raise RuntimeError(f"DNG not written ({r.returncode})")
        dest = target_for(src)
        os.replace(produced, dest)
        return dest
    finally:
        try:
            for leftover in glob.glob(os.path.join(tmp, "*")):
                os.remove(leftover)
            os.rmdir(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------- raw già nelle librerie

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


def library_raws(lib):
    """I raw proprietari della libreria (non nel cestino)."""
    return [p for p in read_catalog(lib)["photos"].values()
            if not p.get("deleted") and convertible(p.get("file_name") or "")
            and (p.get("source") or {}).get("path")]


def stop():
    _stop.set()


def running():
    return bool(_runner and _runner.is_alive())


def maybe_start(settings):
    """Avvia la conversione dei raw già nelle librerie (se chiesta e se ce ne sono). True se l'ha avviata."""
    global _runner
    if running() or not enabled(settings) or not config(settings).get("existing") or not exe():
        return False
    cli = settings.get("cli") or DEFAULT_CLI
    if not is_cli(cli):
        return False
    state = load_json(STATE)
    todo = []
    for lib in settings.get("libraries") or []:
        try:
            sig = catalog_signature(lib)
        except OSError:
            continue
        if (state.get(lib) or {}).get("snap") == sig or library_open_reason(lib):
            continue
        # niente raw da convertire (o solo quelli già falliti): si segna come fatta senza avviare il lavoro. Il lavoro
        # avviato ferma anteprime, smart preview e volti finché gira: prima bastava un catalogo cambiato (una stella
        # dal telefono) per interromperli a vuoto
        try:
            failed = (state.get(lib) or {}).get("failed") or {}
            pending = [p for p in library_raws(lib) if failed.get(str(p["id"]), 0) < 3]
        except (OSError, ValueError, KeyError):
            pending = [None]
        if not pending:
            st = state.setdefault(lib, {})
            st["snap"] = sig
            st["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
            save_json(STATE, state)
            continue
        todo.append(lib)
    if not todo:
        return False
    _stop.clear()
    _runner = threading.Thread(target=_work, args=(settings, cli, todo), name="dng", daemon=True)
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
                log(f"dng ERROR {os.path.basename(lib)}: {e!r}")
    finally:
        _status(running=False)


def replace_commands(old, new_id, cat, replaced=None):
    """I comandi che danno al DNG appena importato (new_id) quello che aveva il raw (old) e mettono il raw nel cestino.
    `replaced`: {raw: DNG} già sostituiti nello stesso giro (la pila letta prima può avere ancora i raw)."""
    replaced = replaced or {}
    oid = int(old["id"])
    meta = old.get("meta") or {}
    cmds = [("library.select", {"ids": [new_id], "active": new_id}),
            ("develop.merge", {"settings": old.get("develop") or {}, "label": "DNG"}),
            ("photo.rate", {"ids": [new_id], "rating": int(old.get("rating") or 0)}),
            ("photo.flag", {"ids": [new_id], "flag": old.get("flag") or "none"}),
            ("photo.label", {"ids": [new_id], "label": (old.get("label") or "none").lower()})]
    texts = {k: meta[k] for k in ("title", "caption", "creator", "copyright", "location", "city", "state", "country")
             if isinstance(meta.get(k), str) and meta[k]}
    if meta.get("keywords"):
        texts["keywords"] = list(meta["keywords"])
    if texts:
        cmds.append(("photo.setMeta", {"ids": [new_id], **texts}))
    for album in (cat.get("albums") or {}).values():
        if oid in [int(x) for x in album.get("photos") or []]:
            cmds.append(("album.addPhotos", {"id": int(album["id"]), "ids": [new_id]}))
    for st in (cat.get("stacks") or {}).values():
        members = [int(x) for x in st.get("photos") or []]
        if oid in members:  # il DNG entra nella stessa pila; se il raw era la copertina, lo diventa il DNG
            top = new_id if members[0] == oid else replaced.get(members[0], members[0])
            cmds.append(("stack.group", {"ids": [top, new_id, oid], "top": top,
                                         "collapsed": bool(st.get("collapsed", True))}))
            cmds.append(("stack.remove", {"ids": [oid]}))
            break
    cmds.append(("photo.delete", {"ids": [oid]}))
    return cmds


def _library(settings, cli, lib):
    import watcher
    name = os.path.basename(os.path.normpath(lib))
    state = load_json(STATE)
    st = state.setdefault(lib, {})
    failed = st.setdefault("failed", {})
    raws = [p for p in library_raws(lib) if failed.get(str(p["id"]), 0) < 3]
    total, done = len(raws), 0
    if raws:
        log(f"dng: {name}: converting {total} raw photos")
        today = datetime.date.today().isoformat()
        if st.get("backup") != today:  # un backup al giorno basta (un giro interrotto riprende senza rifarlo)
            backup_library(lib, backup_root(lib, settings), settings.get("backups_kept", BACKUPS_KEPT))
            st["backup"] = today
            save_json(STATE, state)
    rules = [r for r in (settings.get("watch") or {}).get("rules") or [] if watcher.key(r["library"]) == watcher.key(lib)]
    view = os.path.join(lib, "view.json")
    started = time.time()
    for start in range(0, total, BATCH):
        if _stop.is_set() or library_open_reason(lib):
            break
        _status(running=True, library=name, done=done, total=total, started=started)
        # 1) i DNG, accanto ai raw (un giro interrotto può averli già scritti: si riusano)
        made = []  # (raw, DNG)
        for p in raws[start:start + BATCH]:
            if _stop.is_set():
                break
            src = p["source"]["path"]
            try:
                if not os.path.isfile(src):
                    raise RuntimeError("file not found")
                twin = os.path.splitext(src)[0] + ".dng"
                made.append((p, twin if os.path.isfile(twin) else convert(src)))
            except Exception as e:  # noqa: BLE001
                if _stop.is_set():
                    break  # interrotta (spegnimento): si rifà la prossima volta, non è un errore
                failed[str(p["id"])] = failed.get(str(p["id"]), 0) + 1
                log(f"dng ERROR {name}: {p.get('file_name')}: {e!r}")
            done += 1
            _status(running=True, library=name, done=done, total=total, started=started)
        if not made:
            save_json(STATE, state)
            continue
        # i raw restano nella cartella osservata: segnati come già visti, così non rientrano in libreria
        for r in rules:
            rs = watcher.load_rule_state(r)
            if "handled" in rs:
                rs["handled"] = sorted(set(rs["handled"]) | {watcher.key(x) for q, d in made
                                                               for x in (q["source"]["path"], d)})
                watcher.save_rule_state(r, rs)
        try:
            with open(view, "rb") as f:
                saved_view = f.read()
        except OSError:
            saved_view = None
        try:
            # 2) un'importazione sola per i DNG non ancora in libreria
            def in_library(cat):
                return {os.path.normcase((q.get("source") or {}).get("path") or ""): int(q["id"])
                        for q in cat["photos"].values() if not q.get("deleted")}
            have = in_library(read_catalog(lib))
            new = [d for _p, d in made if os.path.normcase(d) not in have]
            if new:
                run_cli(cli, lib, [("library.import", {"paths": new, "mode": "add"})])
            cat = read_catalog(lib)
            have = in_library(cat)
            # 3) un giro solo per dare ai DNG quello che avevano i raw e mettere i raw nel cestino
            commands, replaced = [], {}
            for p, d in made:
                nid = have.get(os.path.normcase(d))
                if nid is None:
                    failed[str(p["id"])] = failed.get(str(p["id"]), 0) + 1
                    log(f"dng ERROR {name}: {os.path.basename(d)} not imported")
                    continue
                commands += replace_commands(p, nid, cat, replaced)
                replaced[int(p["id"])] = nid
            if commands:
                replies = run_cli(cli, lib, commands, check=False)
                for x in replies:
                    if not x.get("ok"):
                        log(f"dng: {name}: {x.get('command')}: {x.get('error')}")
            log(f"dng: {name}: {len(replaced)} raw photos replaced by DNG ({done} of {total})")
        except Exception as e:  # noqa: BLE001 — si riprova al giro dopo (i DNG già scritti si riusano)
            log(f"dng ERROR {name}: {e!r}")
            break
        finally:
            if saved_view is not None:
                with open(view, "wb") as f:
                    f.write(saved_view)
        save_json(STATE, state)
    if done == total:
        try:
            st["snap"] = catalog_signature(lib)
        except OSError:
            pass
        if total:
            log(f"dng: {name}: done ({total} photos, {len(failed)} not converted)")
    st["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_json(STATE, state)
