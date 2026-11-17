"""Lo stile scelto in fotocamera e il profilo "Camera Matching" di Adobe che lo riproduce.

Le fotocamere scrivono lo stile scelto (Image Control, Custom Image…) nelle note del produttore dentro il raw: i dati
del sensore non ne tengono conto, ed è per questo che un programma che sviluppa il raw mostra colori diversi da quelli
visti sul display della fotocamera. Qui si legge lo stile delle fotocamere supportate e si cerca, tra i profili DCP
installati da Lightroom o Camera Raw, quello che lo riproduce ("Camera Positive Film" per lo stile Positive Film).
Alcuni stili Adobe li fornisce come profili "Look" (.xmp con una tabella colore), per esempio Cinema Yellow e Cinema
Green della GR IV: si trovano anche quelli e looks.py li trasforma in LUT per LightCraft.

Fotocamere supportate: Ricoh (GR III, GR IIIx, GR IV…) e Pentax, che usano lo stesso formato di note.
"""

import json
import os
import struct
import sys

import looks

PROGRAM_DATA = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
PROGRAM_FILES = os.environ.get("PROGRAMFILES") or r"C:\Program Files"
APP_DATA = os.environ.get("APPDATA") or ""
LIGHTROOMS = [os.path.join(PROGRAM_FILES, "Adobe", app, "Resources") for app in ("Adobe Lightroom Classic", "Adobe Lightroom CC")]
# profili "Camera Matching" installati da Camera Raw / Lightroom (una cartella per fotocamera), in ordine di preferenza
ADOBE_CAMERA_DIRS = [os.path.join(PROGRAM_DATA, r"Adobe\CameraRaw\CameraProfiles\Camera")] +     [os.path.join(r, r"CameraProfiles\Camera") for r in LIGHTROOMS]
# profili "Look" di Adobe: Camera\<marca>\<modello>\*.xmp
ADOBE_LOOK_DIRS = [os.path.join(PROGRAM_DATA, r"Adobe\CameraRaw\Settings\Adobe\Profiles\Camera")] +     [os.path.join(r, r"Settings\Adobe\Profiles\Camera") for r in LIGHTROOMS]
USER_PROFILE_DIR = os.path.join(APP_DATA, r"Adobe\CameraRaw\CameraProfiles") if APP_DATA else ""
USER_SETTINGS_DIR = os.path.join(APP_DATA, r"Adobe\CameraRaw\Settings") if APP_DATA else ""


def _app_dir():
    if hasattr(sys, "_MEIPASS"):  # PyInstaller
        return sys._MEIPASS
    if getattr(sys, "frozen", False):  # cx_Freeze
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# profili "misurati" distribuiti con il programma: LUT .cube ricavate confrontando la resa di LightCraft con
# l'anteprima della fotocamera (tools/measure_styles.py), una cartella per fotocamera con profiles.json
MEASURED_DIR = os.path.join(_app_dir(), "assets", "profiles")

RAW_EXTS = (".dng", ".pef")

# Ricoh e Pentax, tag ImageTone (0x004f) delle note del produttore
PENTAX_TONES = {
    0: "Natural", 1: "Bright", 2: "Portrait", 3: "Landscape", 4: "Vibrant", 5: "Monochrome", 6: "Muted",
    7: "Reversal Film", 8: "Bleach Bypass", 9: "Radiant", 10: "Cross Processing", 11: "Flat",
    256: "Standard", 257: "Vivid", 258: "Monotone", 259: "Soft Monotone", 260: "Hard Monotone",
    261: "Hi-Contrast B&W", 262: "Positive Film", 263: "Bleach Bypass", 264: "Retro", 265: "HDR Tone",
    266: "Cross Processing", 267: "Negative Film",
    # GR IV: non ancora nelle tabelle pubbliche (ExifTool). 268 verificato sulle foto dell'autore (alte luci gialle);
    # 269 dedotto dall'ordine del menu della fotocamera, dove Cinema (Green) segue Cinema (Yellow).
    268: "Cinema (Yellow)", 269: "Cinema (Green)",
}

TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


def norm(text):
    """Solo lettere e cifre, minuscole: "RICOH GR IV" e "Ricoh GR-IV" diventano uguali."""
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


# ---------------------------------------------------------------- lettura TIFF (raw DNG/PEF e profili DCP)

class Tiff:
    """Lettura minima di un file in formato TIFF: si leggono solo i pezzi che servono, non il file intero."""

    def __init__(self, f):
        self.f = f
        head = self.read(0, 8)
        if head[:2] == b"II":
            self.e = "<"
        elif head[:2] == b"MM":
            self.e = ">"
        else:
            raise ValueError("not a TIFF-based file")
        self.first = struct.unpack(self.e + "I", head[4:8])[0]

    def read(self, offset, size):
        self.f.seek(offset)
        return self.f.read(size)

    def ifd(self, offset):
        """{tag: (tipo, quanti, 4 byte del valore o del suo indirizzo)}"""
        count = struct.unpack(self.e + "H", self.read(offset, 2))[0]
        if count > 2000:
            raise ValueError("damaged IFD")
        data = self.read(offset + 2, 12 * count)
        entries = {}
        for i in range(count):
            tag, typ, n, raw = struct.unpack(self.e + "HHI4s", data[12 * i:12 * i + 12])
            entries[tag] = (typ, n, raw)
        return entries

    def data(self, entry, limit=8_000_000):
        typ, n, raw = entry
        size = TYPE_SIZE.get(typ, 1) * n
        if size <= 4:
            return raw[:size]
        if size > limit:
            raise ValueError("value too large")
        return self.read(struct.unpack(self.e + "I", raw)[0], size)

    def text(self, entry):
        return self.data(entry).split(b"\0")[0].decode("latin-1").strip()

    def offset(self, entry):
        return struct.unpack(self.e + "I", entry[2])[0]


# ---------------------------------------------------------------- stile scelto in fotocamera

def pentax_tone(note):
    """Codice ImageTone dalle note del produttore Ricoh/Pentax (in qualunque delle loro forme), oppure None."""
    if note.startswith(b"Adobe\0MakN"):  # note copiate da Adobe DNG Converter: 20 byte di intestazione Adobe
        note = note[20:]
    for prefix in (b"RICOH\0", b"PENTAX \0", b"AOC\0"):
        if note.startswith(prefix):
            body = note[len(prefix):]
            break
    else:
        return None
    if body[:2] in (b"II", b"MM"):
        e, ifd = ("<" if body[:2] == b"II" else ">"), body[2:]
    elif body[:2] == b"  ":  # alcune Pentax vecchie: due spazi e ordine Motorola
        e, ifd = ">", body[2:]
    else:
        e, ifd = ">", body
    if len(ifd) < 2:
        return None
    count = min(struct.unpack(e + "H", ifd[:2])[0], 1000)
    for i in range(count):
        chunk = ifd[2 + 12 * i:14 + 12 * i]
        if len(chunk) < 12:
            break
        tag, typ, _n, raw = struct.unpack(e + "HHI4s", chunk)
        if tag == 0x004F and typ in (3, 8):
            return struct.unpack(e + "H", raw[:2])[0]
    return None


def camera_model(tiff, ifd0):
    """Il modello come lo scrive il raw (UniqueCameraModel dei DNG, altrimenti marca + modello)."""
    if 0xC614 in ifd0:
        model = tiff.text(ifd0[0xC614])
        if model:
            return model
    make = tiff.text(ifd0[0x010F]) if 0x010F in ifd0 else ""
    model = tiff.text(ifd0[0x0110]) if 0x0110 in ifd0 else ""
    return model if norm(model).startswith(norm(make)) else f"{make} {model}".strip()


def is_supported(path, make):
    """La fotocamera e il formato sono tra quelli di cui si sa leggere lo stile?"""
    return os.path.splitext(path or "")[1].lower() in RAW_EXTS and any(m in (make or "").lower()
                                                                     for m in ("ricoh", "pentax"))


def read_style(path):
    """Stile di uno scatto: {"model", "key", "code", "name"}; key/code/name sono None se lo stile non si trova.

    key identifica lo stile tra le fotocamere della stessa famiglia ("pentax:262"); name è None per i codici che
    non hanno ancora un nome noto (per esempio stili introdotti da fotocamere nuove).
    """
    with open(path, "rb") as f:
        tiff = Tiff(f)
        ifd0 = tiff.ifd(tiff.first)
        model = camera_model(tiff, ifd0)
        code = None
        if 0xC634 in ifd0:  # DNGPrivateData: le note del produttore dei DNG
            code = pentax_tone(tiff.data(ifd0[0xC634]))
        if code is None and 0x8769 in ifd0:  # altrimenti la MakerNote dei dati Exif (PEF)
            exif = tiff.ifd(tiff.offset(ifd0[0x8769]))
            if 0x927C in exif:
                code = pentax_tone(tiff.data(exif[0x927C]))
    if code is None:
        return {"model": model, "key": None, "code": None, "name": None}
    return {"model": model, "key": f"pentax:{code}", "code": code, "name": PENTAX_TONES.get(code)}


# ---------------------------------------------------------------- profili DCP

def dcp_info(path):
    """(fotocamera, nome del profilo) di un file .dcp."""
    with open(path, "rb") as f:
        tiff = Tiff(f)
        ifd = tiff.ifd(tiff.first)
        camera = tiff.text(ifd[0xC614]) if 0xC614 in ifd else ""
        name = tiff.text(ifd[0xC6F8]) if 0xC6F8 in ifd else ""
    return camera, name or os.path.splitext(os.path.basename(path))[0]


def _files(folder, ext, limit=5000):
    found = []
    for base, _dirs, files in os.walk(folder):
        found += [os.path.join(base, n) for n in files if n.lower().endswith(ext)]
        if len(found) >= limit:
            break
    return found


def _model_folders(root, key, depth):
    """Sottocartelle (a `depth` livelli: modello, oppure marca/modello) col nome simile al modello."""
    level = [root]
    for _ in range(depth):
        nxt = []
        for folder in level:
            try:
                nxt += [e.path for e in os.scandir(folder) if e.is_dir()]
            except OSError:
                pass
        level = nxt
    return [f for f in level if (k := norm(os.path.basename(f))) and (k in key or key in k)]


def is_look(path):
    return path.lower().endswith(".xmp")


def is_lut(path):
    """Profili che in LightCraft diventano LUT: Look di Adobe (.xmp, da convertire) e LUT misurate (.cube)."""
    return path.lower().endswith((".xmp", ".cube"))


def measured_for(model):
    """{nome del profilo: (percorso del .cube, stile, preferito)} dei profili misurati per la fotocamera."""
    key, found = norm(model), {}
    for folder in _model_folders(MEASURED_DIR, key, 1):
        try:
            with open(os.path.join(folder, "profiles.json"), encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            continue
        if norm(manifest.get("camera")) != key:
            continue
        for entry in (manifest.get("styles") or {}).values():
            path = os.path.join(folder, entry.get("file") or "")
            if entry.get("name") and os.path.isfile(path):
                found[entry["name"]] = (path, entry.get("style") or "", bool(entry.get("prefer")))
    return found


class ProfileFinder:
    """Trova i profili fatti per una fotocamera (DCP e Look di Adobe), nelle cartelle di Adobe e in quella scelta
    dall'utente."""

    def __init__(self, extra_dir=None):
        self.extra_dir = extra_dir
        self.cache = {}
        self.preferred_cache = {}

    def candidates(self, model):
        key = norm(model)
        dcps, xmps = [], []
        # nelle cartelle di Adobe c'è una sottocartella per fotocamera ("Ricoh GR IV"): si guardano solo quelle
        # col nome simile al modello, poi il modello scritto nel profilo decide
        for root in ADOBE_CAMERA_DIRS:
            for folder in _model_folders(root, key, 1):
                dcps += _files(folder, ".dcp")
        for root in ADOBE_LOOK_DIRS:
            for folder in _model_folders(root, key, 2):
                xmps += _files(folder, ".xmp")
        for folder in (USER_PROFILE_DIR, self.extra_dir):
            if folder and os.path.isdir(folder):
                dcps += _files(folder, ".dcp")
        for folder in (USER_SETTINGS_DIR, self.extra_dir):
            if folder and os.path.isdir(folder):
                xmps += _files(folder, ".xmp", limit=3000)
        return dcps, xmps

    def profiles_for(self, model):
        """{nome del profilo: percorso del .dcp o del .xmp} per la fotocamera. Tra i .dcp vince il primo trovato,
        tra i Look la versione più recente; un .dcp vince su un Look con lo stesso nome."""
        key = norm(model)
        if key not in self.cache:
            dcps, xmps = self.candidates(model)
            found, versions = {}, {}
            for path in xmps:
                info = looks.look_info(path, head_only=True)
                if info and norm(info["camera"]) == key and info["version"] >= versions.get(info["name"], -1):
                    found[info["name"]], versions[info["name"]] = path, info["version"]
            dcp_found = {}
            for path in dcps:
                try:
                    camera, name = dcp_info(path)
                except (OSError, ValueError, struct.error):
                    continue
                if norm(camera) == key and name not in dcp_found:
                    dcp_found[name] = path
            found.update(dcp_found)
            preferred = {}
            for name, (path, style, prefer) in measured_for(model).items():
                found.setdefault(name, path)
                if prefer:
                    preferred[style] = name
            self.cache[key] = dict(sorted(found.items(), key=lambda kv: kv[0].lower()))
            self.preferred_cache[key] = preferred
        return self.cache[key]

    def preferred(self, model):
        """{stile: profilo misurato da usare al posto di quello di Adobe} (sulle foto di prova era più fedele)."""
        self.profiles_for(model)
        return self.preferred_cache.get(norm(model), {})


def auto_profile(style_name, profiles, preferred=None):
    """Il profilo per lo stile: quello misurato se è il preferito, altrimenti quello di Adobe col nome dello stile
    ("Camera Positive Film" per Positive Film), altrimenti quello misurato; None se non c'è."""
    want = norm(style_name)
    if not want:
        return None
    best = (preferred or {}).get(style_name)
    if best in profiles:
        return best
    for prefixes in (("camera", ""), ("measured",)):
        for name in profiles:
            if norm(name) in [p + want for p in prefixes]:
                return name
    return None
