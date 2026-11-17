"""Importazione automatica: osserva le cartelle scelte, importa in LightCraft le foto nuove e dà a ciascuna il
profilo dello stile con cui è stata scattata.

Gira in background (PresetCraftAppLicator.exe --watch), avviato all'accesso a Windows da un'attività pianificata
(vedi autostart.py), con un'icona nell'area di notifica (tray.py). Ogni regola è "cartella → libreria".

Un trasferimento dal telefono fa arrivare le foto una alla volta: l'importazione parte solo quando nella cartella non
arriva e non cambia più niente da `quiet` secondi (60 di predefinito), e allora importa tutte le foto arrivate in una
volta, con un solo backup. Mentre i file arrivano la cartella si controlla ogni pochi secondi, altrimenti ogni
`interval` minuti.

Per ogni regola un file di stato in DATA_DIR\\watch\\ ricorda i file già trattati (una foto tolta dalla libreria non
torna dentro da sola; le foto già presenti quando la regola è stata creata si possono ignorare) e le foto importate a
cui manca ancora il profilo (se LightCraft viene aperto a metà, il profilo arriva al giro dopo).
"""

import ctypes
import datetime
import hashlib
import json
import os
import threading
import time

import camera_styles as cs
import matcher
from lightcraft import (BACKUPS_KEPT, DATA_DIR, DEFAULT_CLI, backup_library, backup_root, is_cli, is_library,
                        library_open_reason, log, read_catalog, run_cli)

SETTINGS = os.path.join(DATA_DIR, "settings.json")
STATE_DIR = os.path.join(DATA_DIR, "watch")
STATUS = os.path.join(STATE_DIR, "status.json")  # stato del processo in background, per la finestra dell'app
MUTEX = "Local\\PresetCraftAppLicator-watch"  # stessi nomi in autostart.py (usati dall'installazione)
STOP_EVENT = "Local\\PresetCraftAppLicator-watch-stop"
DEFAULT_INTERVAL = 2  # minuti tra un controllo e l'altro quando non arriva niente
DEFAULT_QUIET = 60  # secondi senza novità nella cartella: il trasferimento è finito
FAST_POLL = 10  # secondi tra un controllo e l'altro mentre le foto arrivano
WAIT_POLL = 30  # secondi tra un controllo e l'altro mentre si aspetta la chiusura di LightCraft
IMPORT_CHUNK = 10  # minimo di foto per ogni esecuzione di lightcraft-cli (vedi matcher.chunk_size)
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

def drive_share(letter):
    """Percorso di rete di un'unità collegata ("Y:" → "\\\\MY-NAS\\Photos"), oppure None.

    Non si tiene in memoria: all'accesso a Windows l'unità può collegarsi dopo l'avvio del processo in background."""
    if not letter or not os.path.splitdrive(letter)[0]:
        return None
    buf = ctypes.create_unicode_buffer(1024)
    size = ctypes.c_ulong(len(buf))
    try:
        rc = ctypes.windll.mpr.WNetGetConnectionW(letter[:2].upper(), buf, ctypes.byref(size))
    except OSError:
        return None
    return buf.value.rstrip("\\") if rc == 0 and buf.value else None


def mapped_drives():
    """{lettera: percorso di rete} delle unità di rete collegate adesso."""
    found = {}
    for i in range(26):
        letter = f"{chr(65 + i)}:"
        if ctypes.windll.kernel32.GetDriveTypeW(letter + "\\") == 4:  # DRIVE_REMOTE
            share = drive_share(letter)
            if share:
                found[letter] = share
    return found


def key(path, drives=None):
    """Forma confrontabile di un percorso: un'unità di rete diventa il suo percorso di rete, così "Y:\\Catalogo"
    e "\\\\MY-NAS\\Photos\\Catalogo" risultano la stessa cartella."""
    p = os.path.normpath(path)
    drive = os.path.splitdrive(p)[0]
    if len(drive) == 2:
        share = drives.get(drive.upper()) if drives is not None else drive_share(drive)
        if share:
            p = share + p[2:]
    return os.path.normcase(p)


def prefer_drive(path):
    """Un percorso di rete di una cartella condivisa collegata a un'unità torna con la lettera dell'unità, come lo
    registra di solito la libreria ("\\\\MY-NAS\\Photos\\Catalogo" → "Y:\\Catalogo")."""
    p = os.path.normpath(path)
    if p.startswith("\\\\"):
        for letter, share in mapped_drives().items():
            if os.path.normcase(p) == os.path.normcase(share) or \
                    os.path.normcase(p).startswith(os.path.normcase(share) + "\\"):
                return letter + "\\" + p[len(share):].lstrip("\\")
    return p


def rule_state_path(rule):
    # il nome non deve dipendere dalle unità di rete collegate in quel momento: si usa il percorso così com'è
    plain = [os.path.normcase(os.path.normpath(rule[k])) for k in ("folder", "library")]
    digest = hashlib.sha1("|".join(plain).encode("utf-8")).hexdigest()[:16]
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


def list_photos(folder, subfolders=False, sidecars=None):
    """Foto nella cartella: {percorso: (dimensione, data di modifica)}. Con `sidecars` (un dizionario) raccoglie lì
    anche i file .xmp, nello stesso giro."""
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
            if e.name.startswith("."):  # file temporanei dei trasferimenti (.SYNOTEMP…) e nascosti
                continue
            try:
                if e.is_dir():
                    if subfolders and e.name.lower() not in SKIP_DIRS:
                        stack.append(e.path)
                elif os.path.splitext(e.name)[1].lower() in PHOTO_EXTS:
                    st = e.stat()
                    found[e.path] = (st.st_size, st.st_mtime)
                elif sidecars is not None and e.name.lower().endswith(".xmp"):
                    st = e.stat()
                    sidecars[e.path] = (st.st_size, st.st_mtime)
            except OSError:
                continue
    return found


# ---------------------------------------------------------------- metadati da ViewerCraftStar (app Android)

XMP_TOOL = "ViewerCraftStar"
_NS = {"x": "adobe:ns:meta/", "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
       "dc": "http://purl.org/dc/elements/1.1/", "xmp": "http://ns.adobe.com/xap/1.0/",
       "lr": "http://ns.adobe.com/lightroom/1.0/", "lc": "http://ns.lightcraft.app/lc/1.0/",
       "vcs": "http://ns.poty87.github.io/viewercraftstar/1.0/"}
CATALOG_DIR = ".viewercraftstar"  # cartella nascosta, nella cartella osservata sul NAS, letta dall'app
LABELS = ("red", "yellow", "green", "blue", "purple")


def read_shared(path):
    """Il contenuto di un file aperto con condivisione completa (lettura, scrittura e cancellazione): open() di Python
    non concede la cancellazione e fallisce (errore 32) se un altro programma ha il file aperto con quel diritto, per
    esempio un manico rimasto sul NAS da un invio del telefono interrotto."""
    if os.name != "nt":
        with open(path, "rb") as f:
            return f.read()
    import msvcrt
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    # GENERIC_READ, FILE_SHARE_READ | WRITE | DELETE, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL
    h = k32.CreateFileW(path, 0x80000000, 7, None, 3, 0x80, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        err = ctypes.get_last_error()
        raise OSError(err, ctypes.FormatError(err), path)
    with open(msvcrt.open_osfhandle(h, os.O_RDONLY), "rb") as f:
        return f.read()


def read_app_xmp(path):
    """I metadati di un .xmp scritto da ViewerCraftStar, pronti per i comandi di LightCraft; None se il file non è
    dell'app (gli .xmp di LightCraft o di altri programmi non si toccano) o non è XML valido. Se il file non si
    riesce ad aprire solleva OSError: chi chiama riprova più tardi.

    L'app scrive sempre tutti i campi, anche vuoti: qui si impostano tutti, così anche togliere una stella, un'etichetta
    o un titolo arriva in LightCraft (rileggere l'.xmp con photo.readMetadataFromFile non svuota etichetta, titolo e
    parole chiave)."""
    import xml.etree.ElementTree as ET
    data = read_shared(path)  # OSError: si riprova al prossimo giro
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    desc = root.find(".//rdf:Description", _NS)
    if desc is None:
        return None

    def text(tag):
        el = desc.find(tag, _NS)
        if el is None:
            return desc.get("{%s}%s" % (_NS[tag.split(":")[0]], tag.split(":")[1]))
        return (el.text or "").strip()

    def items(tag):
        return [(li.text or "").strip() for li in desc.findall(f"{tag}/*/rdf:li", _NS) if (li.text or "").strip()]

    if text("xmp:CreatorTool") != XMP_TOOL:
        return None
    try:
        rating = max(0, min(5, int(text("xmp:Rating") or 0)))
    except ValueError:
        rating = 0
    label = (text("xmp:Label") or "").lower()
    flag = (text("lc:flag") or "none").lower()
    keywords = items("lr:hierarchicalSubject") or items("dc:subject")
    alt = {t: (items(t) or [""])[0] for t in ("dc:title", "dc:description", "dc:rights", "dc:creator")}
    # preset scelto sul telefono: id del preset di LightCraft, "" = profilo della fotocamera, None = campo assente
    preset = text("vcs:preset")
    return {"rating": rating, "flag": flag if flag in ("pick", "reject", "none") else "none",
            "label": label if label in LABELS else "none", "title": alt["dc:title"], "caption": alt["dc:description"],
            "copyright": alt["dc:rights"], "creator": alt["dc:creator"], "keywords": keywords,
            "preset": preset.strip() if preset is not None else None, "preset_name": text("vcs:presetName") or "",
            "revision": (text("vcs:revision") or "").strip()}


def library_presets(lib):
    """I preset dell'utente nella libreria (presets.json): [{id, name, group, settings}]."""
    try:
        with open(os.path.join(lib, "presets.json"), encoding="utf-8-sig") as f:
            return [p for p in json.load(f).get("user") or [] if p.get("id")]
    except (OSError, ValueError):
        return []


def export_presets(rule, state):
    """Scrive nella cartella osservata (sul NAS) l'elenco dei preset della libreria, per ViewerCraftStar: lo rifà solo
    quando presets.json della libreria cambia."""
    lib = rule["library"]
    src = os.path.join(lib, "presets.json")
    target = os.path.join(rule["folder"], CATALOG_DIR, "presets.json")
    try:
        st = os.stat(src)
    except OSError:
        return
    sig = [st.st_size, st.st_mtime]
    if state.get("presets_sig") == sig and os.path.exists(target):
        return
    presets = []
    for p in library_presets(lib):
        settings = p.get("settings") or {}
        presets.append({"id": p["id"], "name": p.get("name") or p["id"], "group": p.get("group") or "",
                        "profile": (settings.get("profile") or {}).get("id"),
                        "profileOnly": set(settings) <= {"profile"}, "settings": settings})
    data = {"version": 1, "library": os.path.basename(lib),
            "updated": datetime.datetime.now().isoformat(timespec="seconds"), "presets": presets}
    try:
        save_json(target, data)
        state["presets_sig"] = sig
        log(f"watch: {len(presets)} presets published for {XMP_TOOL} in {os.path.dirname(target)}")
    except OSError as e:
        log(f"watch: presets not published ({e})")


PRESET_EXTS = (".xmp", ".lrtemplate", ".lcpreset", ".zip", ".lmp", ".mplumpack")
IMPORT_GROUP = "ViewerCraftStar"


def preset_uploads(rule):
    """File di preset caricati da ViewerCraftStar in .viewercraftstar/import, in attesa di essere importati."""
    folder = os.path.join(rule["folder"], CATALOG_DIR, "import")
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in sorted(names)
            if not n.startswith(".") and n.lower().endswith(PRESET_EXTS) and os.path.isfile(os.path.join(folder, n))]


def import_uploads(cli, rule, state, uploads, summary):
    """Importa i preset caricati dal telefono (preset.import) e li sposta in import/done; ripubblica l'elenco."""
    lib = rule["library"]
    reply = run_cli(cli, lib, [("preset.import", {"paths": uploads, "group": IMPORT_GROUP})], check=False)[0]
    if not reply.get("ok"):
        summary["failed"].append(f"preset.import: {reply.get('error')}")
        log(f"watch ERROR preset.import: {reply.get('error')}")
        return 0
    result = reply.get("result") or {}
    imported = result.get("imported") or []
    for f in result.get("failed") or []:
        summary["failed"].append(f"preset: {f}")
    done_dir = os.path.join(os.path.dirname(uploads[0]), "done")
    os.makedirs(done_dir, exist_ok=True)
    for path in uploads:
        try:
            os.replace(path, os.path.join(done_dir, os.path.basename(path)))
        except OSError:
            pass
    summary["presets_imported"] = summary.get("presets_imported", 0) + len(imported)
    log(f"watch: {len(imported)} presets imported from {IMPORT_GROUP}: " + ", ".join(i.get("name", "?") for i in imported))
    state.pop("presets_sig", None)  # l'elenco per il telefono si ripubblica subito
    export_presets(rule, state)
    return len(imported)


def publish_status(rule, state, names):
    """Scrive per ViewerCraftStar quali foto della cartella sono in libreria e quale versione dei metadati del
    telefono è stata applicata ({nome senza estensione: {id, rev}}): l'app considera "al sicuro a casa" (e quindi
    cancellabili dal telefono) solo le foto confermate qui. Si riscrive solo quando qualcosa cambia."""
    applied = state.get("applied_rev") or {}
    data = {"version": 1, "library": os.path.basename(rule["library"]),
            "photos": {stem: {"id": pid, "rev": applied.get(stem, "")} for stem, pid in sorted(names.items())}}
    digest = hashlib.sha1(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()
    target = os.path.join(rule["folder"], CATALOG_DIR, "status.json")
    if state.get("status_digest") == digest and os.path.exists(target):
        return
    data["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        save_json(target, data)
        state["status_digest"] = digest
    except OSError as e:
        log(f"watch: status for {XMP_TOOL} not written ({e})")


def phone_presets(lib, settings):
    """{id della foto: id del preset} scelti con ViewerCraftStar per le foto di una libreria: quelle foto non ricevono
    il profilo automatico (né dall'importazione automatica né dalla finestra dell'app)."""
    found = {}
    for rule in (settings.get("watch") or {}).get("rules") or []:
        if key(rule.get("library", "")) == key(lib):
            for pid, preset in (load_rule_state(rule).get("phone_presets") or {}).items():
                if preset:
                    found[int(pid)] = preset
    return found


def apply_phone_presets(cli, lib, settings, finder, todo, phone, summary):
    """Porta in LightCraft i preset scelti sul telefono: {id della foto: id del preset ("" = profilo della fotocamera)}.

    Per ogni foto: sviluppo azzerato, profilo dello stile di scatto, poi il preset. Così cambiare preset sul telefono non
    lascia in giro le regolazioni del preset di prima. Restituisce le foto riuscite."""
    known = {p["id"] for p in library_presets(lib)}
    plan = {i["id"]: i for i in matcher.build_plan(lib, "all", settings, finder, ids=list(todo))["items"]}
    with_target = [i for i in plan.values() if i["target"]]
    profile_ids, failed = matcher.import_profiles(lib, cli, with_target) if with_target else ({}, [])
    summary["failed"] += failed
    cmds, spans = [("library.source", {"kind": "all"})], []
    for pid, preset in todo.items():
        if preset and preset not in known:
            summary["failed"].append(f"#{pid}: preset {preset} not in the library")
            continue
        start = len(cmds)
        cmds.append(("develop.reset", {"ids": [pid]}))
        item = plan.get(pid)
        prof = profile_ids.get((cs.norm(item["model"]), item["target"])) if item and item["target"] else None
        if prof:
            cmds += [("library.select", {"ids": [pid], "active": pid}), ("develop.profile", {"id": prof, "amount": 100})]
        if preset:
            cmds.append(("preset.apply", {"id": preset, "ids": [pid]}))
        spans.append((pid, preset, start, len(cmds)))
    if not spans:
        return 0
    replies = run_cli(cli, lib, cmds, check=False)
    done = 0
    for pid, preset, a, b in spans:
        mine = replies[a:b] if len(replies) == len(cmds) else []
        if mine and all(r.get("ok") for r in mine):
            phone[str(pid)] = preset
            done += 1
        else:
            errors = [f"{r.get('command')}: {r.get('error')}" for r in mine if not r.get("ok")] or ["no reply"]
            summary["failed"].append(f"#{pid}: {'; '.join(errors)}")
    summary["presets"] = summary.get("presets", 0) + done
    log(f"watch: presets from {XMP_TOOL} applied to {done} of {len(spans)} photos")
    return done


def metadata_commands(pid, meta):
    """Comandi di lightcraft-cli che portano una foto esattamente ai metadati dell'.xmp."""
    ids = [pid]
    return [("photo.rate", {"ids": ids, "rating": meta["rating"]}),
            ("photo.flag", {"ids": ids, "flag": meta["flag"]}),
            ("photo.label", {"ids": ids, "label": meta["label"]}),
            ("photo.setMeta", {"ids": ids, "title": meta["title"], "caption": meta["caption"],
                               "creator": meta["creator"], "copyright": meta["copyright"],
                               "keywords": meta["keywords"]})]


class LibraryIndex:
    """Le foto della libreria (anche quelle in "Recently Deleted"), per sapere se un file c'è già.

    Si confronta il percorso (con le unità di rete ricondotte al loro percorso di rete) e, se il percorso è scritto in
    un altro modo (per esempio con l'indirizzo IP del NAS), il nome del file insieme alla dimensione."""

    def __init__(self, lib):
        self.drives = mapped_drives()
        self.paths, self.names = {}, {}
        for p in read_catalog(lib)["photos"].values():
            path = (p.get("source") or {}).get("path")
            if not path:
                continue
            pid = int(p["id"])
            self.paths[key(path, self.drives)] = pid
            name = (p.get("file_name") or os.path.basename(path)).lower()
            if p.get("file_size"):
                self.names[(name, int(p["file_size"]))] = pid

    @classmethod
    def of(cls, lib):
        """L'indice della libreria, rifatto solo se catalog.snap è cambiato (su una libreria grande leggerlo costa più
        di un secondo, e mentre arrivano foto la cartella si controlla ogni 10 secondi) o se sono cambiate le unità
        di rete collegate."""
        try:
            st = os.stat(os.path.join(lib, "catalog.snap"))
            signature = (st.st_size, st.st_mtime_ns)
        except OSError:
            signature = None
        cached = _indexes.get(key(lib))
        if cached and signature and cached[0] == signature and cached[1].drives == mapped_drives():
            return cached[1]
        index = cls(lib)
        if signature:
            _indexes[key(lib)] = (signature, index)
        return index

    def key(self, path):
        return key(path, self.drives)

    def find(self, path, size):
        """Id della foto della libreria che corrisponde al file, oppure None."""
        pid = self.paths.get(self.key(path))
        if pid is None and size:
            pid = self.names.get((os.path.basename(path).lower(), int(size)))
        return pid


_indexes = {}  # libreria -> (firma di catalog.snap, LibraryIndex)


def new_files(folder, lib, subfolders=False):
    """Foto della cartella che la libreria non ha (per chiedere cosa farne quando si crea una regola)."""
    index = LibraryIndex(lib)
    return [p for p, (size, _m) in list_photos(folder, subfolders).items() if index.find(p, size) is None]


def create_rule_state(rule, import_existing):
    """Stato iniziale di una regola nuova: i file già presenti si ignorano, a meno che si vogliano importare."""
    files = list_photos(rule["folder"], rule.get("subfolders"))
    index = LibraryIndex(rule["library"])
    handled = sorted(index.key(p) for p, (size, _m) in files.items()
                     if index.find(p, size) is not None or not import_existing)
    save_rule_state(rule, {"handled": handled, "pending": {}, "tries": {}, "profile_ids": [],
                           "created": datetime.datetime.now().isoformat(timespec="seconds")})


# ---------------------------------------------------------------- stato visibile (icona e finestra)

class Status:
    """Cosa sta facendo il processo in background, letto dall'icona nell'area di notifica (da un altro thread)."""

    def __init__(self, on_change=None):
        self.lock = threading.Lock()
        self.on_change = on_change
        self.data = {"phase": "idle"}

    def set(self, **values):
        with self.lock:
            if values.get("phase") and values["phase"] != self.data.get("phase"):
                keep = {k: self.data[k] for k in ("last", "next_check") if k in self.data}
                self.data = keep
            self.data.update(values)
            data = dict(self.data)
        if self.on_change:
            self.on_change(data)

    def get(self):
        with self.lock:
            return dict(self.data)


def eta(started, done, total):
    """Secondi che mancano, stimati dal tempo per foto finora; None all'inizio."""
    if not done or done >= total:
        return None
    return (time.time() - started) / done * (total - done)


# ---------------------------------------------------------------- un giro su una regola

def cycle(rule, settings, finder, status=None, now=None):
    """Controlla una cartella; a trasferimento finito importa le foto e dà loro il profilo.

    Restituisce quanti secondi aspettare prima del prossimo controllo (None = l'intervallo normale)."""
    status = status or Status()
    folder, lib = rule["folder"], rule["library"]
    lib_name = os.path.basename(lib) or lib
    if not os.path.isdir(folder) or not is_library(lib):
        return None  # NAS spento, unità non ancora collegata, libreria spostata: si riprova al prossimo giro
    now = now or time.time()
    w = settings.get("watch") or {}
    quiet = max(10, int(w.get("quiet") or DEFAULT_QUIET))
    state = load_rule_state(rule)
    sidecars = {}
    files = list_photos(folder, rule.get("subfolders"), sidecars)
    index = LibraryIndex.of(lib)
    key = index.key  # noqa: F811 — qui le chiavi usano le unità di rete lette una volta per questo giro
    if "handled" not in state:
        # stato perso o regola scritta a mano: per prudenza si ignora quello che c'è già
        state.update(handled=sorted(key(p) for p in files), pending={}, tries={}, profile_ids=[])
        save_rule_state(rule, state)
        return None
    handled = set(state["handled"])
    profile_ids = set(state.get("profile_ids") or [])
    export_presets(rule, state)
    phone = state.setdefault("phone_presets", {})  # id della foto -> preset scelto sul telefono ("" = nessuno)

    # file arrivati nella libreria per un'altra strada (trascinati, o importati da LightCraft stesso)
    in_library = {}
    for path, (size, _m) in files.items():
        pid = index.find(path, size)
        if pid is not None:
            in_library[key(path)] = pid
    for k, pid in in_library.items():
        if k not in handled:
            handled.add(k)
            profile_ids.add(pid)
    # resoconto per il telefono: foto in libreria (nome senza estensione -> id)
    names = {os.path.splitext(os.path.basename(path))[0].lower(): in_library[key(path)]
             for path in files if key(path) in in_library}
    publish_status(rule, state, names)
    # file nuovi: finché qualcosa arriva o cambia, il trasferimento non è finito
    pending = {key(p): [size, mtime] for p, (size, mtime) in files.items()
               if key(p) not in in_library and key(p) not in handled}
    by_key = {key(p): p for p in files}
    if pending != state.get("pending"):
        state["changed"] = now
    state["pending"] = pending
    still = now - state.get("changed", now)
    ready = [by_key[k] for k, (size, _m) in pending.items() if size > 0] if still >= quiet else []

    # metadati da ViewerCraftStar: gli .xmp nuovi o cambiati accanto a foto già in libreria
    seen_xmp = state.setdefault("xmp", {})
    photo_of = {}  # (cartella, nome senza estensione) -> id della foto in libreria
    for path in files:
        pid = in_library.get(key(path))
        if pid is not None:
            photo_of[(os.path.normcase(os.path.dirname(path)),
                      os.path.normcase(os.path.splitext(os.path.basename(path))[0]))] = pid
    xmp_todo = {}  # .xmp -> (id della foto, metadati, firma del file)
    for path, (size, mtime) in sidecars.items():
        k, sig = key(path), [size, mtime]
        if seen_xmp.get(k) == sig:
            continue
        try:
            meta = read_app_xmp(path)
        except OSError as e:  # non si apre adesso (bloccato, rete): non lo si segna come visto, si riprova
            log(f"watch: {path} not readable yet ({e}), retrying later")
            continue
        if meta is None:  # .xmp di LightCraft o di altri programmi: non si tocca
            seen_xmp[k] = sig
            continue
        pid = photo_of.get((os.path.normcase(os.path.dirname(path)),
                            os.path.normcase(os.path.splitext(os.path.basename(path))[0])))
        if pid is not None:  # altrimenti il DNG non è ancora in libreria: si riprova al prossimo giro
            xmp_todo[path] = (pid, meta, sig)
    preset_todo = {pid: meta["preset"] for _p, (pid, meta, _s) in xmp_todo.items()
                   if meta["preset"] is not None and phone.get(str(pid), "") != meta["preset"]}
    uploads = preset_uploads(rule)

    def save():
        state["handled"] = sorted(handled)
        state["profile_ids"] = sorted(profile_ids)
        save_rule_state(rule, state)

    if pending and still < quiet:
        status.set(phase="incoming", count=len(pending), library=lib_name, quiet_left=int(quiet - still))
        save()
        return FAST_POLL
    if not ready and not profile_ids and not xmp_todo and not uploads:
        state.pop("waiting", None)
        save()
        return None

    reason = library_open_reason(lib)
    if reason:
        count = len(ready) or len(profile_ids) or len(xmp_todo) or len(uploads)
        if not state.get("waiting"):
            log(f"watch: {count} new photos for {lib}, waiting until LightCraft is closed")
        status.set(phase="waiting", count=count, library=lib_name, first_wait=not state.get("waiting"))
        state["waiting"] = True
        save()
        return WAIT_POLL
    state.pop("waiting", None)
    cli = settings.get("cli") or DEFAULT_CLI
    if not is_cli(cli):
        log(f"watch: lightcraft-cli not found ({cli})")
        status.set(phase="error", error=f"lightcraft-cli: {cli}")
        save()
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
               "metadata": 0, "presets": 0, "failed": [], "library": lib_name}
    tries = state.setdefault("tries", {})
    try:
        started = time.time()
        size = matcher.chunk_size(len(ready), IMPORT_CHUNK)
        for start in range(0, len(ready), size):
            if start and library_open_reason(lib):
                log("watch: LightCraft was opened, the rest is imported later")
                break  # le foto rimaste restano in attesa: il prossimo giro le riprende
            chunk = ready[start:start + size]
            status.set(phase="importing", done=start, total=len(ready), library=lib_name,
                       eta=eta(started, start, len(ready)))
            reply = run_cli(cli, lib, [("library.import", {"paths": chunk, "mode": "add", "onDeleted": "skip"})],
                            check=False)[0]
            if reply.get("ok"):
                result = reply.get("result") or {}
                new = [int(i) for k in ("imported", "restored") for i in result.get(k) or []]
                profile_ids.update(new)
                summary["imported"] += len(new)
                summary["failed"] += [": ".join(map(str, x)) if isinstance(x, list) else str(x)
                                      for x in result.get("failed") or []]
                for path in chunk:
                    handled.add(key(path))
                    tries.pop(key(path), None)
                    pending.pop(key(path), None)
            else:
                error = f"library.import: {reply.get('error')}"
                summary["failed"].append(error)
                log(f"watch ERROR {error}")
                for path in chunk:
                    k = key(path)
                    tries[k] = tries.get(k, 0) + 1
                    if tries[k] >= MAX_TRIES:  # non si riprova all'infinito: resta da importare a mano
                        handled.add(k)
                        tries.pop(k)
                        pending.pop(k, None)
                        log(f"watch: giving up on {path}")
        if ready:
            log(f"watch: imported {summary['imported']} of {len(ready)}" +
                (f", not imported: {summary['failed']}" if summary["failed"] else ""))
        # preset caricati dal telefono: prima di tutto, così sono già in libreria se una foto li sceglie
        if uploads and not library_open_reason(lib):
            import_uploads(cli, rule, state, uploads, summary)
        # le foto con un preset scelto sul telefono non ricevono il profilo automatico
        profile_ids -= {int(p) for p, preset in phone.items() if preset}
        if profile_ids and not library_open_reason(lib):
            plan = matcher.build_plan(lib, "all", settings, finder, ids=profile_ids)
            todo = [i for i in plan["items"] if i["status"] == "apply" and i["action"] == "apply"]
            if todo:
                p_started = time.time()

                def progress(done, total):
                    status.set(phase="profiles", done=done, total=total, library=lib_name,
                               eta=eta(p_started, done, total))
                status.set(phase="profiles", done=0, total=len(todo), library=lib_name, eta=None)
                result = matcher.apply_profiles(lib, cli, todo, root, kept, progress=progress, backup=backup,
                                                chunk=matcher.chunk_size(len(todo), 20))
                summary["profiles"] = result["done"]
                summary["failed"] += result["failed"]
            profile_ids.clear()  # chi non è "apply" (profilo scelto a mano, stile senza profilo) resta com'è
        if xmp_todo and not library_open_reason(lib):
            status.set(phase="metadata", count=len(xmp_todo), library=lib_name)
            done = apply_app_metadata(cli, lib, xmp_todo, seen_xmp, key, summary)
            log(f"watch: metadata from {XMP_TOOL} applied to {done} of {len(xmp_todo)} photos")
            if preset_todo:
                apply_phone_presets(cli, lib, settings, finder, preset_todo, phone, summary)
                # un preset non riuscito: il suo .xmp si riprova al prossimo giro
                for path, (pid, _meta, _sig) in xmp_todo.items():
                    if pid in preset_todo and phone.get(str(pid), "") != preset_todo[pid]:
                        seen_xmp.pop(key(path), None)
            # versione dei metadati del telefono applicata davvero (metadati e preset riusciti)
            applied = state.setdefault("applied_rev", {})
            for path, (_pid, meta, sig) in xmp_todo.items():
                if seen_xmp.get(key(path)) == sig and meta.get("revision"):
                    applied[os.path.splitext(os.path.basename(path))[0].lower()] = meta["revision"]
            publish_status(rule, state, names)
    finally:
        if saved_view is not None:  # LightCraft si riapre sulla vista di prima
            with open(view, "wb") as f:
                f.write(saved_view)
        state["last"] = summary
        save()
    if summary["imported"] or summary["profiles"] or summary["metadata"]:
        status.set(phase="done", last=summary)
    elif summary["failed"]:
        status.set(phase="error", error=summary["failed"][0])
    return FAST_POLL if pending else None


# ---------------------------------------------------------------- processo in background

def apply_app_metadata(cli, lib, todo, seen_xmp, key, summary, batch=50):
    """Porta in LightCraft i metadati degli .xmp di ViewerCraftStar; restituisce quante foto sono state aggiornate."""
    done = 0
    items = list(todo.items())
    for start in range(0, len(items), batch):
        block = items[start:start + batch]
        cmds = [c for _path, (pid, meta, _sig) in block for c in metadata_commands(pid, meta)]
        replies = run_cli(cli, lib, cmds, check=False)
        per = len(cmds) // len(block)
        for i, (path, (pid, _meta, sig)) in enumerate(block):
            mine = replies[i * per:(i + 1) * per] if len(replies) == len(cmds) else []
            if mine and all(r.get("ok") for r in mine):
                seen_xmp[key(path)] = sig
                done += 1
            else:
                errors = [f"{r.get('command')}: {r.get('error')}" for r in mine if not r.get("ok")] or ["no reply"]
                summary["failed"].append(f"{os.path.basename(path)}: {'; '.join(errors)}")
    summary["metadata"] += done
    return done


def watch_settings(settings):
    w = settings.get("watch") or {}
    rules = [r for r in w.get("rules") or [] if r.get("folder") and r.get("library")]
    return bool(w.get("enabled")) and bool(rules), rules, max(1, min(60, int(w.get("interval") or DEFAULT_INTERVAL)))


_mutex = None


def single_instance():
    """True se questo è l'unico processo in background. Il mutex è posseduto finché il processo vive: l'installazione
    lo aspetta per sapere quando il processo si è chiuso."""
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, True, MUTEX)
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


class Watcher:
    """Il ciclo dei controlli. Si ferma con stop() (icona) o con l'evento dell'installazione."""

    def __init__(self, status):
        k32 = ctypes.windll.kernel32
        k32.CreateEventW.restype = ctypes.c_void_p
        self.status = status
        # [0] = richiesta dell'installazione (con nome), [1] = sveglia interna ("Controlla adesso", "Chiudi")
        self.events = (ctypes.c_void_p * 2)(k32.CreateEventW(None, True, False, STOP_EVENT),
                                             k32.CreateEventW(None, False, False, None))
        self.quit = False

    def wake(self):
        ctypes.windll.kernel32.SetEvent(ctypes.c_void_p(self.events[1]))

    def stop(self):
        self.quit = True
        self.wake()

    def wait(self, seconds):
        """Aspetta; True se è arrivata la richiesta di chiudersi."""
        r = ctypes.windll.kernel32.WaitForMultipleObjects(2, self.events, False, int(seconds * 1000))
        return r == 0 or self.quit

    def run(self, once=False):
        log("watch: started")
        try:
            while True:
                if self.wait(0):
                    log("watch: stopped" + ("" if self.quit else " (update or uninstall)"))
                    return 0
                settings = load_json(SETTINGS)
                enabled, rules, interval = watch_settings(settings)
                if not enabled:
                    log("watch: stopped (automatic import is off)")
                    return 0
                finder = cs.ProfileFinder(settings.get("profiles_dir"))
                pause = interval * 60
                if self.status.get().get("phase") in ("incoming", "waiting", "importing", "profiles", "error"):
                    self.status.set(phase="idle")
                for rule in rules:
                    try:
                        wanted = cycle(rule, settings, finder, self.status)
                    except Exception as e:  # noqa: BLE001 — un errore non deve fermare il processo in background
                        log(f"watch ERROR {rule.get('folder')}: {e!r}")
                        self.status.set(phase="error", error=str(e))
                        wanted = None
                    if wanted:
                        pause = min(pause, wanted)
                next_check = time.time() + pause
                self.status.set(next_check=next_check)
                save_json(STATUS, {"pid": os.getpid(), "checked": datetime.datetime.now().isoformat(timespec="seconds"),
                                   "interval": interval, **{k: v for k, v in self.status.get().items()
                                                            if k in ("phase", "count", "done", "total", "last")}})
                if once:
                    return 0
                if self.wait(pause):
                    log("watch: stopped" + ("" if self.quit else " (update or uninstall)"))
                    return 0
        finally:
            try:
                os.remove(STATUS)
            except OSError:
                pass


def run(once=False, tray=True):
    if not single_instance():
        return 0
    if tray and not once:
        try:
            import tray as tray_ui
        except Exception as e:  # noqa: BLE001 — senza icona l'importazione funziona lo stesso
            log(f"watch: no tray icon ({e!r})")
        else:
            return tray_ui.run()
    return Watcher(Status()).run(once)


def status():
    """Stato per la finestra: ultimo controllo del processo in background (vuoto se non è in esecuzione)."""
    return load_json(STATUS)
