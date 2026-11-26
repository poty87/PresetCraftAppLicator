"""Quello che serve al telefono per sviluppare le foto come LightCraft (ViewerCraftStar, «Modifica»).

Nella cartella di servizio, accanto all'indice della libreria (libraries/<libreria>/):
  develop.json.gz   le impostazioni di sviluppo di ogni foto, il JSON di LightCraft (develop.get) senza conversioni:
                    {"version": 1, "base": {impostazioni più comuni},
                     "photos": {id: [firma, {"sezione.campo": valore diverso da base, ...}, [campi da togliere]?]}}
                    (le impostazioni di una foto = base, con i valori del secondo elemento al posto dei suoi e senza i
                    campi del terzo, se c'è; null è un valore vero di LightCraft, non vuol dire "togli");
                    "smart": {id: nome del file della smart preview} (in smart/<prime 2 cifre>/<nome>), dalla 1.29.1
                    La firma (develop_signature) è quella che il telefono rimanda con una modifica ("base"): se intanto
                    sul PC la foto è cambiata, PresetCraftAppLicator non sovrascrive (copia virtuale).
  profiles/         i file dei profili importati nella libreria (.dcp, .cube), copiati com'erano
  profiles.json     {"version": 1, "profiles": [{"id", "name", "kind": "dcp"|"lut", "group"|"cameraModel", "file"}]}
E nella cartella di servizio stessa:
  lightcraft.json   {"version": "0.21.1", "checked": ...}: la versione di LightCraft del PC. Il telefono sviluppa con
                    il motore con cui è stato compilato e avvisa se non coincide.
"""
import datetime
import gzip
import hashlib
import json
import os
import shutil
import subprocess
from collections import Counter

from lightcraft import NO_WINDOW, log, read_prefs

DEVELOP_FILE = "develop.json.gz"
PROFILES_DIR = "profiles"
PROFILES_FILE = "profiles.json"
LIGHTCRAFT_FILE = "lightcraft.json"


def develop_signature(dev):
    """Firma delle impostazioni di sviluppo di una foto (cambia con qualsiasi modifica)."""
    return hashlib.sha1(json.dumps(dev or {}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def flatten(d, prefix=""):
    """{"a": {"b": 1}} → {"a.b": 1}; liste e valori semplici restano interi (le liste si confrontano intere)."""
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def unflatten(flat):
    out = {}
    for key, v in flat.items():
        cur = out
        parts = key.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = v
    return out


# copie virtuali create su richiesta del telefono: {token: id} (nella cartella della libreria, accanto a changes/)
COPIES_FILE = "copies.json"


def build_develop(photos):
    """Il contenuto di develop.json.gz: base comune + differenze per foto."""
    devs = {int(p["id"]): p.get("develop") or {} for p in photos}
    flats = {pid: flatten(d) for pid, d in devs.items()}
    counts = {}
    for f in flats.values():
        for k, v in f.items():
            counts.setdefault(k, Counter())[json.dumps(v, sort_keys=True)] += 1
    base = {k: json.loads(c.most_common(1)[0][0]) for k, c in counts.items()}
    out = {}
    for pid, f in flats.items():
        diff = {k: v for k, v in f.items() if k not in base or base[k] != v}
        drop = sorted(k for k in base if k not in f)  # campi che questa foto non ha
        out[str(pid)] = [develop_signature(devs[pid]), diff] + ([drop] if drop else [])
    # com'era ogni foto all'importazione (prima voce della cronologia di LightCraft: il profilo scelto da
    # PresetCraftAppLicator, nessun ritocco), solo dove è diversa da adesso: da lì parte una copia virtuale fatta sul
    # telefono («senza nulla applicato»)
    first = {}
    for p in photos:
        h = p.get("history") or []
        h0 = h[0].get("settings") if h and isinstance(h[0], dict) else None
        pid = int(p["id"])
        if not isinstance(h0, dict) or h0 == devs[pid]:
            continue
        f = flatten(h0)
        diff = {k: v for k, v in f.items() if k not in base or base[k] != v}
        drop = sorted(k for k in base if k not in f)
        first[str(pid)] = [diff] + ([drop] if drop else [])
    import smart
    names = {str(int(p["id"])): smart.file_name(p) for p in photos}
    return {"version": 1, "base": unflatten(base), "photos": out, "smart": names, "first": first}


def rebuild(data, pid):
    """Le impostazioni di una foto da develop.json.gz (come fa il telefono)."""
    entry = data["photos"][str(pid)]
    flat = flatten(data["base"])
    flat.update(entry[1])
    for k in entry[2] if len(entry) > 2 else []:
        flat.pop(k, None)
    return unflatten(flat)


def write_gz_json(path, data):
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    with open(tmp, "rb+") as f:
        os.fsync(f.fileno())
    os.replace(tmp, path)


def publish_develop(target_dir, cat):
    """Scrive develop.json.gz per il telefono. Torna il numero di foto."""
    photos = [p for p in cat["photos"].values() if p.get("kind") in ("raw", "image")]
    data = build_develop(photos)
    # copie virtuali chieste dal telefono: token → id della copia (il telefono riconosce così la sua copia in attesa)
    try:
        with open(os.path.join(target_dir, COPIES_FILE), encoding="utf-8") as f:
            copies = json.load(f)
    except (OSError, ValueError):
        copies = {}
    data["copies"] = {t: i for t, i in copies.items() if str(i) in cat["photos"]}
    os.makedirs(target_dir, exist_ok=True)
    write_gz_json(os.path.join(target_dir, DEVELOP_FILE), data)
    return len(photos)


def publish_profiles(lib, target_dir):
    """Copia i profili importati nella libreria in profiles/ e scrive profiles.json. Torna il numero di profili."""
    prefs = read_prefs(lib)
    pdir = os.path.join(target_dir, PROFILES_DIR)
    entries = []
    wanted = set()
    for kind, key in (("dcp", "dcpProfiles"), ("lut", "lutProfiles")):
        for p in prefs.get(key) or []:
            src = p.get("file")
            if not p.get("id") or not src or not os.path.isfile(src):
                continue
            name = os.path.basename(src)
            dest = os.path.join(pdir, name)
            wanted.add(name)
            try:
                st = os.stat(src)
                if not os.path.isfile(dest) or os.path.getsize(dest) != st.st_size:
                    os.makedirs(pdir, exist_ok=True)
                    tmp = dest + ".tmp"
                    shutil.copyfile(src, tmp)
                    os.replace(tmp, dest)
            except OSError as e:
                log(f"watch: profile {name} not copied for the phone ({e!r})")
                continue
            entry = {"id": p["id"], "name": p.get("name") or name, "kind": kind, "file": name}
            for extra in ("group", "cameraModel"):
                if p.get(extra):
                    entry[extra] = p[extra]
            entries.append(entry)
    # profili tolti dalla libreria: via anche dal telefono
    if os.path.isdir(pdir):
        for f in os.listdir(pdir):
            if f not in wanted:
                try:
                    os.remove(os.path.join(pdir, f))
                except OSError:
                    pass
    path = os.path.join(target_dir, PROFILES_FILE)
    data = {"version": 1, "profiles": entries}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return len(entries)


def cli_version(cli):
    try:
        r = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        words = (r.stdout or "").split()
        return words[-1] if r.returncode == 0 and words else None
    except (OSError, subprocess.SubprocessError):
        return None


def publish_lightcraft_version(folder, cli):
    """Scrive lightcraft.json nella cartella di servizio se la versione è cambiata. Torna la versione."""
    version = cli_version(cli)
    if not version:
        return None
    path = os.path.join(folder, LIGHTCRAFT_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            if json.load(f).get("version") == version:
                return version
    except (OSError, ValueError):
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": version, "checked": datetime.datetime.now().isoformat(timespec="seconds")}, f)
    os.replace(tmp, path)
    log(f"watch: LightCraft {version} published for the phone")
    return version
