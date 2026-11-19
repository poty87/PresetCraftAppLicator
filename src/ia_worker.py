"""Motore IA di PresetCraftAppLicator: impronte visive delle foto per la ricerca per soggetti di ViewerCraftStar.

Gira nell'ambiente Python del motore (PyTorch, transformers, onnx), non dentro l'app: PresetCraftAppLicator lo avvia
con  <motore>\\venv\\Scripts\\python.exe ia_worker.py --config <file.json>  a bassa priorità.

Solo lettura su librerie e foto: legge catalog.snap e le anteprime incorporate nei file. Scrive:
  - nella cartella del motore: miniature (cache/<libreria>/mini), stato (stato.json), registro (ia.log);
  - nella cartella del telefono sul NAS, per ogni libreria, in <libreria>/ia/:
      visivo.bin      impronte (id + vettori a 16 bit), vedi scrivi_impronte
      manifest.json   modello, numero di foto, data, file del telefono
      telefono/       la parte "testo" del modello, ridotta e compressa per il telefono (ONNX) + tokenizer.json

Configurazione (JSON): {"engine_dir", "phone_folder", "libraries": [cartelle, nell'ordine della coda], "model",
"phone_bits": 8 o 4, "gpu_check": bool, "window": {"from": "22:00", "to": "07:00"} o null}.
Il lavoro si ferma da solo, salvando i progressi, se: compare il file "pausa" nella cartella del motore (pausa chiesta
dall'utente), un altro programma usa la GPU ("gpu_check"), oppure un lavoro grande (prima analisi) esce dalla fascia
oraria ("window"). Lo stato finale lo dice: "paused", "gpu_busy", "waiting_window" o "idle".
Il processo principale ("supervisore") lancia il lavoro in un processo figlio e lo rilancia se la GPU si ferma (TDR di
Windows: il driver si riavvia e il processo perde la GPU), riprendendo da dove era arrivato.
"""
import argparse
import concurrent.futures as cf
import datetime
import gc
import hashlib
import io
import json
import os
import re
import struct
import subprocess
import sys
import time
import traceback
import urllib.request

FORMATO_VISIVO = 1
LATO = 448  # lato lungo delle miniature salvate (i modelli usano 224-384 px)
CODICE_GPU_PERSA = 75  # il figlio esce così quando la GPU si è fermata: il supervisore lo rilancia
TENTATIVI = 5
GRANDE = 1000  # foto da fare oltre le quali un lavoro è "grande" (rispetta la fascia oraria)
CONTROLLO_GPU = 120  # secondi tra un controllo e l'altro dell'uso della GPU da parte di altri programmi
GPU_OCCUPATA = 40  # % di uso medio della GPU, con il motore fermo, oltre la quale la GPU è di qualcun altro

# modelli: id Hugging Face, lotto (piccolo = ogni calcolo dura poco: niente TDR), testo minuscolo, lunghezza del testo
MODELLI = {
    "metaclip2-giant": {"hf": "facebook/metaclip-2-worldwide-giant-378", "lotto": 16},
    "metaclip2-huge": {"hf": "facebook/metaclip-2-worldwide-huge-378", "lotto": 32},
    "metaclip2-b16": {"hf": "facebook/metaclip-2-worldwide-b16", "lotto": 64},
}
PAROLE_URL = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/{lang}/{lang}_50k.txt"
# ricerche di controllo: il modello del telefono deve dare le stesse impronte dell'originale
CONTROLLO = ["gatti", "cane che corre sulla spiaggia", "auto d'epoca", "tramonto al mare", "bambini che giocano",
             "torta di compleanno", "neve in montagna", "persone che ballano", "chiesa", "fuochi d'artificio",
             "uomo con la barba", "mountain bike nel bosco", "Duomo di Milano", "fenicottero rosa"]


# ---------------------------------------------------------------- utilità

class Stop(Exception):
    """Il lavoro si ferma (progressi salvati): motivo = "paused", "gpu_busy" o "waiting_window"."""

    def __init__(self, motivo):
        super().__init__(motivo)
        self.motivo = motivo


def uso_gpu(campioni=4, pausa=0.5):
    """Uso medio (%) della GPU NVIDIA in questo momento, o None se non si sa."""
    vals = []
    for _ in range(campioni):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            vals.append(max(int(x) for x in out.split() if x.strip().isdigit()))
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        time.sleep(pausa)
    return sum(vals) / len(vals) if vals else None


def in_fascia(window, adesso=None):
    """True se l'ora è dentro la fascia oraria (anche a cavallo della mezzanotte); senza fascia sempre True."""
    if not window:
        return True
    adesso = adesso or datetime.datetime.now()
    try:
        da = datetime.time.fromisoformat(window["from"])
        a = datetime.time.fromisoformat(window["to"])
    except (KeyError, ValueError):
        return True
    t = adesso.time()
    return da <= t < a if da <= a else (t >= da or t < a)


class Controllo:
    """Controlli tra un passo e l'altro: pausa chiesta, fascia oraria (solo lavori grandi), GPU usata da altri."""

    def __init__(self, motore):
        self.motore = motore
        self.window = motore.cfg.get("window")
        self.gpu = bool(motore.cfg.get("gpu_check", True))
        self.ultimo_gpu = time.time()
        self.pausa_path = os.path.join(motore.dir, "pausa")

    def __call__(self, grande=False, gpu_in_uso=False):
        if os.path.exists(self.pausa_path):
            raise Stop("paused")
        if grande and not in_fascia(self.window):
            raise Stop("waiting_window")
        if self.gpu and time.time() - self.ultimo_gpu >= CONTROLLO_GPU:
            self.ultimo_gpu = time.time()
            if gpu_in_uso:
                time.sleep(2)  # il motore si ferma un attimo: quello che resta è di altri programmi
            u = uso_gpu()
            if u is not None and u >= GPU_OCCUPATA:
                raise Stop("gpu_busy")

    def subito_gpu(self):
        """Controllo della GPU prima di cominciare (il motore non la sta ancora usando)."""
        if self.gpu:
            u = uso_gpu()
            self.ultimo_gpu = time.time()
            if u is not None and u >= GPU_OCCUPATA:
                raise Stop("gpu_busy")

class Motore:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dir = cfg["engine_dir"]
        self.stato_path = os.path.join(self.dir, "stato.json")

    def log(self, msg):
        line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
        print(line, flush=True)
        try:
            with open(os.path.join(self.dir, "ia.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def stato(self, **kw):
        """Aggiorna stato.json (letto dalla finestra di PresetCraftAppLicator)."""
        s = carica_json(self.stato_path, {})
        s.update(kw, pid=os.getpid(), updated=datetime.datetime.now().isoformat(timespec="seconds"))
        salva_json(self.stato_path, s)


def carica_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def salva_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def nome_cartella(lib):
    """Il nome della libreria come sottocartella (come library_folder_name di PresetCraftAppLicator)."""
    name = os.path.basename(os.path.normpath(lib))
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "library"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


def foto_libreria(lib):
    """Le foto della libreria (senza eliminate, video e copie virtuali): [{id, path, name}]."""
    with open(os.path.join(lib, "catalog.snap"), encoding="utf-8") as f:
        cat = json.load(f)["catalog"]
    out = []
    for p in cat["photos"].values():
        if p.get("deleted") or p.get("kind") not in ("raw", "image") or p.get("copy_of"):
            continue
        src = (p.get("source") or {}).get("path") or ""
        if src:
            out.append({"id": int(p["id"]), "path": src, "name": p.get("file_name") or os.path.basename(src)})
    return out


# ---------------------------------------------------------------- anteprime (stessa logica di ViewerCraftStar)

class Tiff:
    """Lettura a pezzi di un file TIFF/DNG (solo i tag che servono, senza leggere tutto il file)."""

    def __init__(self, f):
        self.f = f
        self.size = os.fstat(f.fileno()).st_size
        head = self.leggi(0, 8)
        self.o = "<" if head[:2] == b"II" else ">" if head[:2] == b"MM" else None
        self.primo = struct.unpack(self.o + "I", head[4:8])[0] if self.o else 0

    def leggi(self, at, n):
        self.f.seek(at)
        return self.f.read(n)

    def ifd(self, off):
        if off <= 0 or off >= self.size:
            return None, 0
        o = self.o
        n = struct.unpack(o + "H", self.leggi(off, 2))[0]
        if n == 0 or n > 1000:
            return None, 0
        raw = self.leggi(off + 2, n * 12 + 4)
        tags = {}
        for i in range(n):
            t, ty, c = struct.unpack(o + "HHI", raw[i * 12:i * 12 + 8])
            sz = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 10: 8, 11: 4, 12: 8, 13: 4}.get(ty)
            if not sz or c == 0 or sz * c > 1 << 20:
                continue
            data = raw[i * 12 + 8:i * 12 + 8 + sz * c] if sz * c <= 4 else \
                self.leggi(struct.unpack(o + "I", raw[i * 12 + 8:i * 12 + 12])[0], sz * c)
            if ty == 3:
                tags[t] = list(struct.unpack(o + "%dH" % c, data))
            elif ty in (4, 13):
                tags[t] = list(struct.unpack(o + "%dI" % c, data))
            elif ty in (5, 10):
                r = struct.unpack(o + "%d%s" % (2 * c, "I" if ty == 5 else "i"), data)
                tags[t] = [r[k] / r[k + 1] if r[k + 1] else 0.0 for k in range(0, len(r), 2)]
            else:
                tags[t] = data
        return tags, struct.unpack(o + "I", raw[n * 12:n * 12 + 4])[0]


XYZ50_TO_SRGB = [[3.1338561, -1.6168667, -0.4906146], [-0.9787684, 1.9161415, 0.0334540],
                 [0.0719453, -0.2289914, 1.4052427]]
XYZ65_TO_SRGB = [[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560],
                 [0.0556434, -0.2040259, 1.0572252]]


def tasselli(tf, t):
    from PIL import Image
    w, h, tw, th = t[0x100][0], t[0x101][0], t[0x142][0], t[0x143][0]
    img = Image.new("RGB", (w, h))
    across = (w + tw - 1) // tw
    for i, (at, n) in enumerate(zip(t[0x144], t[0x145])):
        img.paste(Image.open(io.BytesIO(tf.leggi(at, n))).convert("RGB"), ((i % across) * tw, (i // across) * th))
    return img


def sviluppa_fld(tf, ifd0, fl):
    """Sviluppa i Fast Load Data di Lightroom (LinearRaw a tasselli): polinomi, bianco, matrice colore, curva."""
    import numpy as np
    from PIL import Image
    if 0xC628 not in ifd0:
        return None
    img = tasselli(tf, fl)
    img.thumbnail((LATO * 2, LATO * 2))
    x = np.asarray(img).astype(np.float64) / 255.0
    ol = fl.get(0xC741) or b"\0\0\0\0"
    i = 4
    for _ in range(struct.unpack(">I", ol[:4])[0]):
        oid, _ver, _flags, size = struct.unpack(">IIII", ol[i:i + 16])
        body = ol[i + 16:i + 16 + size]
        if oid == 8:  # MapPolynomial
            plane, planes, deg = struct.unpack(">I", body[16:20])[0], struct.unpack(">I", body[20:24])[0], \
                struct.unpack(">I", body[32:36])[0]
            c = struct.unpack(">%dd" % (deg + 1), body[36:36 + 8 * (deg + 1)])
            for p in range(plane, min(3, plane + planes)):
                v = x[..., p].copy()
                x[..., p] = sum(c[k] * v ** k for k in range(deg + 1))
        i += 16 + size
    neutral = np.array(ifd0[0xC628])
    fmv = ifd0.get(0xC715) or ifd0.get(0xC714)
    if fmv and len(fmv) == 9:
        m = np.array(XYZ50_TO_SRGB) @ np.array(fmv).reshape(3, 3) @ np.diag(1 / neutral)
    else:
        cm = ifd0.get(0xC622) or ifd0.get(0xC621)
        if not cm or len(cm) != 9:
            return None
        m = np.array(XYZ65_TO_SRGB) @ np.linalg.inv(np.array(cm).reshape(3, 3))
        m = np.diag(1 / (m @ neutral)) @ m
    rgb = np.clip(x @ m.T * 2 ** (ifd0.get(0xC62A) or [0])[0], 0, None)
    rgb = np.clip(rgb / (1 + 0.15 * rgb) * 1.15, 0, 1)
    srgb = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * rgb ** (1 / 2.4) - 0.055)
    return Image.fromarray((srgb * 255 + 0.5).astype(np.uint8))


def anteprima_tiff(path):
    """L'anteprima sviluppata più grande di un DNG/CR2/TIFF (anche a tasselli o dai Fast Load Data), o None."""
    from PIL import Image
    with open(path, "rb") as f:
        tf = Tiff(f)
        if not tf.o:
            return None
        ifd0, nxt = tf.ifd(tf.primo)
        if not ifd0:
            return None
        orient = (ifd0.get(0x112) or [1])[0]
        tutte = [ifd0] + [x for x in (tf.ifd(s)[0] for s in ifd0.get(0x14A, [])) if x]
        guard = 0
        while nxt and guard < 8:
            x, nxt = tf.ifd(nxt)
            if not x:
                break
            tutte.append(x)
            guard += 1
        best, fld = None, None
        for t in tutte:
            comp = (t.get(0x103) or [0])[0]
            sub = (t.get(0xFE) or [0])[0]
            w, h = (t.get(0x100) or [0])[0], (t.get(0x101) or [0])[0]
            if (t.get(0x106) or [0])[0] == 34892:  # Fast Load Data: dati del sensore, da sviluppare
                if sub == 1 and 0x144 in t and (fld is None or w > fld[0x100][0]):
                    fld = t
                continue
            if comp == 34892 and sub == 1 and 0x144 in t:
                cand = ("tiles", w, h, t)
            elif 0x201 in t and 0x202 in t:
                cand = ("jpeg", w or 160, h or 120, (t[0x201][0], t[0x202][0]))
            elif (comp == 6 or (comp in (7, 34892) and sub == 1)) and len(t.get(0x111, [])) == 1:
                cand = ("jpeg", w, h, (t[0x111][0], t[0x117][0]))
            else:
                continue
            if best is None or cand[1] * cand[2] > best[1] * best[2]:
                best = cand
        img = None
        if fld is not None and (best is None or max(best[1], best[2]) < 400):
            img = sviluppa_fld(tf, ifd0, fld)
        if img is None and best is not None:
            if best[0] == "jpeg":
                at, n = best[3]
                img = Image.open(io.BytesIO(tf.leggi(at, n)))
                img.draft("RGB", (LATO, LATO))
                img = img.convert("RGB")
            else:
                img = tasselli(tf, best[3])
        if img is None:
            return None
    rot = {3: 180, 6: 270, 8: 90}.get(orient)
    return img.rotate(rot, expand=True) if rot else img


def anteprima(path):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = None
    ext = os.path.splitext(path)[1].lower()
    img = None
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
        img = Image.open(path)
        img.draft("RGB", (LATO, LATO))
        img = ImageOps.exif_transpose(img).convert("RGB")
    else:
        try:
            img = anteprima_tiff(path)
        except Exception:  # noqa: BLE001 — si prova con gli altri metodi
            img = None
        if img is None and ext in (".tif", ".tiff", ".psd"):
            img = Image.open(path).convert("RGB")
        if img is None:
            import rawpy
            with rawpy.imread(path) as r:
                th = r.extract_thumb()
                img = Image.open(io.BytesIO(th.data)) if th.format == rawpy.ThumbFormat.JPEG else \
                    Image.fromarray(th.data)
                img = img.convert("RGB")
    img.thumbnail((LATO, LATO))
    return img


def prepara_miniature(motore, cart, foto, nome_lib, controllo):
    """Le miniature che mancano (lette dal NAS in parallelo). Ritorna le foto che hanno la miniatura."""
    os.makedirs(cart, exist_ok=True)
    errori_path = os.path.join(cart, "errori.json")
    errori = carica_json(errori_path, {})
    da_fare = [p for p in foto if not os.path.exists(os.path.join(cart, f"{p['id']}.jpg"))
               and errori.get(str(p["id"])) != p["path"]]  # i file che non si leggono non si riprovano a ogni giro

    def una(p):
        try:
            img = anteprima(p["path"])
            tmp = os.path.join(cart, f"{p['id']}.tmp.jpg")
            img.save(tmp, quality=88)
            os.replace(tmp, os.path.join(cart, f"{p['id']}.jpg"))
            return None
        except Exception as e:  # noqa: BLE001
            return f"{type(e).__name__}: {e}"

    if da_fare:
        grande = len(da_fare) > GRANDE
        controllo(grande=grande)
        motore.log(f"{nome_lib}: {len(da_fare)} miniature da leggere")
        t0 = time.time()
        # a blocchi, così pausa e fascia oraria si rispettano anche durante una lettura lunga
        blocco = 400
        try:
            with cf.ThreadPoolExecutor(min(32, (os.cpu_count() or 4) * 2)) as ex:
                for b in range(0, len(da_fare), blocco):
                    parte = da_fare[b:b + blocco]
                    for i, (p, err) in enumerate(zip(parte, ex.map(una, parte)), b + 1):
                        if err:
                            errori[str(p["id"])] = p["path"]
                            motore.log(f"{nome_lib}: {p['name']} non leggibile ({err})")
                        if i % 200 == 0 or i == len(da_fare):
                            motore.stato(phase="previews", library=nome_lib, done=i, total=len(da_fare))
                    controllo(grande=grande)
        finally:
            salva_json(errori_path, errori)
        dt = time.time() - t0
        motore.log(f"{nome_lib}: miniature lette in {dt:.0f}s ({len(da_fare) / max(dt, 1e-6):.1f}/s)")
    return [p for p in foto if os.path.exists(os.path.join(cart, f"{p['id']}.jpg"))]


# ---------------------------------------------------------------- impronte

def carica_impronte(path):
    """(ids, vettori) da visivo.bin, o (None, None) se manca o è di un altro formato."""
    import numpy as np
    try:
        with open(path, "rb") as f:
            head = f.read(20)
            magic, ver, dim, n, tipo = struct.unpack("<4sIIII", head)
            if magic != b"VCSV" or ver != FORMATO_VISIVO or tipo != 1:
                return None, None
            ids = np.frombuffer(f.read(4 * n), dtype="<i4").copy()
            v = np.frombuffer(f.read(2 * n * dim), dtype="<f2").reshape(n, dim).copy()
            return ids, v
    except (OSError, ValueError, struct.error):
        return None, None


def scrivi_impronte(path, ids, v):
    """visivo.bin: "VCSV", formato, dimensione, quante, tipo (1 = float16); poi gli id (int32) e i vettori, tutto
    little-endian. Scritto in un file temporaneo e poi rinominato: chi legge trova sempre un file intero."""
    import numpy as np
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(struct.pack("<4sIIII", b"VCSV", FORMATO_VISIVO, v.shape[1], len(ids), 1))
        f.write(np.asarray(ids, dtype="<i4").tobytes())
        f.write(np.asarray(v, dtype="<f2").tobytes())
    os.replace(tmp, path)


class Miniature:
    """Dataset delle miniature per il DataLoader: le prepara per il modello su più processi."""

    def __init__(self, cart, ids, preparatore):
        self.cart, self.ids, self.prep = cart, ids, preparatore

    def __len__(self):
        return len(self.ids)

    def __getitem__(self, i):
        from PIL import Image
        img = Image.open(os.path.join(self.cart, f"{self.ids[i]}.jpg")).convert("RGB")
        return self.prep(images=img, return_tensors="pt")["pixel_values"][0]


class ModelloVisivo:
    def __init__(self, nome):
        import torch
        from transformers import AutoModel, AutoProcessor
        self.torch = torch
        self.nome = nome
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if self.dev == "cuda" else torch.float32
        self.dtype = dtype
        self.m = AutoModel.from_pretrained(MODELLI[nome]["hf"], dtype=dtype).to(self.dev).eval()
        self.proc = AutoProcessor.from_pretrained(MODELLI[nome]["hf"])

    def impronte(self, x):
        t = self.torch
        out = self.m.get_image_features(pixel_values=x.to(self.dev).to(self.dtype))
        e = (out if hasattr(out, "float") else out.pooler_output).float()
        return t.nn.functional.normalize(e, dim=-1).cpu().numpy()


def aggiorna_impronte(motore, modello, cart, foto, dest, nome_lib, controllo):
    """Calcola le impronte delle foto nuove, toglie quelle delle foto non più in libreria, salva visivo.bin.
    I progressi si salvano ogni ~100 lotti: se la GPU si ferma si riparte da lì."""
    import numpy as np
    import torch
    from torch.utils.data import DataLoader
    ids, v = carica_impronte(dest)
    presenti = {p["id"] for p in foto}
    if ids is not None:
        tieni = np.isin(ids, list(presenti))
        if not tieni.all():
            ids, v = ids[tieni], v[tieni]
            scrivi_impronte(dest, ids, v)
            motore.log(f"{nome_lib}: tolte le impronte di {int((~tieni).sum())} foto non più in libreria")
    fatte = set(ids.tolist()) if ids is not None else set()
    nuove = [p["id"] for p in foto if p["id"] not in fatte]
    if not nuove:
        return 0
    grande = len(nuove) > GRANDE
    controllo(grande=grande)
    motore.log(f"{nome_lib}: {len(nuove)} foto da analizzare con {modello.nome} su {modello.dev}")
    lotto = MODELLI[modello.nome]["lotto"] if modello.dev == "cuda" else 8
    dl = DataLoader(Miniature(cart, nuove, modello.proc.image_processor), batch_size=lotto,
                    num_workers=min(12, os.cpu_count() or 4), prefetch_factor=4, persistent_workers=False)
    nuovi_ids, nuovi_v = [], []

    def salva():
        nonlocal ids, v, nuovi_ids, nuovi_v
        if not nuovi_ids:
            return
        add_ids = np.array(nuovi_ids, dtype=np.int32)
        add_v = np.concatenate(nuovi_v).astype(np.float16)
        ids = add_ids if ids is None else np.concatenate([ids, add_ids])
        v = add_v if v is None else np.concatenate([v, add_v])
        scrivi_impronte(dest, ids, v)
        nuovi_ids, nuovi_v = [], []

    t0 = time.time()
    fatte_ora = 0
    with torch.no_grad():
        for k, x in enumerate(dl):
            nuovi_v.append(modello.impronte(x))
            nuovi_ids.extend(nuove[k * lotto:k * lotto + len(x)])
            fatte_ora += len(x)
            if (k + 1) % 100 == 0:
                salva()
            if (k + 1) % 10 == 0 or fatte_ora == len(nuove):
                motore.stato(phase="embeddings", library=nome_lib, done=fatte_ora, total=len(nuove))
            if (k + 1) % 10 == 0:
                try:
                    controllo(grande=grande, gpu_in_uso=modello.dev == "cuda")
                except Stop:
                    salva()
                    raise
    salva()
    dt = time.time() - t0
    motore.log(f"{nome_lib}: {fatte_ora} impronte in {dt:.0f}s ({fatte_ora / max(dt, 1e-6):.1f} foto/s)")
    return fatte_ora


# ---------------------------------------------------------------- modello del telefono (solo la parte testo)

def parole(motore, lang):
    path = os.path.join(motore.dir, "parole", f"{lang}_50k.txt")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        req = urllib.request.Request(PAROLE_URL.format(lang=lang), headers={"User-Agent": "PresetCraftAppLicator"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    with open(path, encoding="utf-8") as f:
        out = [riga.split()[0] for riga in f if riga.strip()]
    return out + [p.capitalize() for p in out]


def pezzi_da_tenere(motore, tok):
    """Token da tenere: speciali, singoli caratteri e quelli usati dalle 50.000 parole più comuni (it + en)."""
    vocab = tok.get_vocab()
    keep = {t for t in vocab if len(t.replace("▁", "")) <= 1}
    keep.update(str(t) for t in tok.added_tokens_decoder.values())
    keep.update(tok.all_special_tokens)
    for lang in ("it", "en"):
        ws = parole(motore, lang)
        for i in range(0, len(ws), 2000):
            for ids in tok(ws[i:i + 2000], add_special_tokens=False)["input_ids"]:
                keep.update(tok.convert_ids_to_tokens(ids))
            for ids in tok([" ".join(ws[i:i + 2000])], add_special_tokens=False)["input_ids"]:
                keep.update(tok.convert_ids_to_tokens(ids))
    for q in CONTROLLO:
        keep.update(tok.convert_ids_to_tokens(tok(q, add_special_tokens=False)["input_ids"]))
    return sorted({vocab[t] for t in keep if t in vocab})


def tokenizzatore_ridotto(tok, tenuti):
    """Il tokenizzatore (Unigram o BPE) rifatto col vocabolario ridotto: id nuovi 0..N-1 nell'ordine dei vecchi."""
    from tokenizers import Tokenizer
    js = json.loads(tok.backend_tokenizer.to_str())
    nuovo = {v: n for n, v in enumerate(tenuti)}
    vocab = js["model"]["vocab"]
    if js["model"]["type"] == "Unigram":
        js["model"]["vocab"] = [v for i, v in enumerate(vocab) if i in nuovo]
        if js["model"].get("unk_id") is not None:
            js["model"]["unk_id"] = nuovo[js["model"]["unk_id"]]
    else:
        tok_tenuti = {t for t, i in vocab.items() if i in nuovo}
        js["model"]["vocab"] = {t: nuovo[i] for t, i in vocab.items() if i in nuovo}
        merges = []
        for m in js["model"]["merges"]:
            a, b = m.split(" ", 1) if isinstance(m, str) else m
            if a in tok_tenuti and b in tok_tenuti and a + b in tok_tenuti:
                merges.append(m)
        js["model"]["merges"] = merges
    for t in js.get("added_tokens") or []:
        t["id"] = nuovo[t["id"]]

    def rimappa(x):
        if isinstance(x, dict):
            for k, val in x.items():
                if k == "ids" and isinstance(val, list):
                    x[k] = [nuovo[i] for i in val]
                else:
                    rimappa(val)
        elif isinstance(x, list):
            for val in x:
                rimappa(val)

    rimappa(js.get("post_processor"))
    return Tokenizer.from_str(json.dumps(js)), js


def prepara_telefono(motore, nome_modello, bits, cartella):
    """La parte testo del modello per il telefono: vocabolario it+en, pesi a `bits` bit, tabella del vocabolario a
    8 bit, in ONNX; più tokenizer.json e i parametri per usarli. Ritorna il dizionario per manifest.json."""
    import numpy as np
    import onnx
    import torch
    from onnxruntime.quantization.matmul_nbits_quantizer import MatMulNBitsQuantizer
    from transformers import AutoModel, AutoTokenizer
    hf = MODELLI[nome_modello]["hf"]
    motore.stato(phase="phone_model", library="", done=0, total=4)
    motore.log(f"telefono: preparo il modello testo di {nome_modello} ({bits} bit)")
    tok = AutoTokenizer.from_pretrained(hf)
    model = AutoModel.from_pretrained(hf, dtype=torch.float32).eval()
    model.vision_model = None
    gc.collect()
    max_len = getattr(model.config.text_config, "max_position_embeddings", 77)
    tenuti = pezzi_da_tenere(motore, tok)
    nuovo_tok, js = tokenizzatore_ridotto(tok, tenuti)
    motore.stato(done=1)
    pad_old = tok.pad_token_id if tok.pad_token_id is not None else 0
    pad_new = tenuti.index(pad_old)
    eos_new = tenuti.index(tok.eos_token_id)

    def ids(enc, testi, pad):
        out = np.full((len(testi), max_len), pad, dtype=np.int64)
        for i, t in enumerate(testi):
            x = enc(t)[:max_len]
            out[i, :len(x)] = x
        return out

    ids_old = ids(lambda t: tok(t)["input_ids"], CONTROLLO, pad_old)
    ids_new = ids(lambda t: nuovo_tok.encode(t).ids, CONTROLLO, pad_new)
    with torch.no_grad():
        out = model.get_text_features(input_ids=torch.tensor(ids_old))
        rif = (out if hasattr(out, "float") else out.pooler_output)
        rif = torch.nn.functional.normalize(rif, dim=-1).numpy()

    tm = model.text_model
    inner = tm.text_model if hasattr(tm, "text_model") else tm
    w = inner.embeddings.token_embedding.weight.data[torch.tensor(tenuti)].clone()
    scala = w.abs().amax(dim=1, keepdim=True).clamp(min=1e-8) / 127
    proiezione = getattr(model, "text_projection", None)

    class Vocabolario(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer("q", torch.round(w / scala).to(torch.int8))
            self.register_buffer("scala", scala)

        def forward(self, x):
            return self.q[x].float() * self.scala[x]

    class Testo(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.tm = tm
            inner.embeddings.token_embedding = Vocabolario()
            for mod in tm.modules():
                if hasattr(mod, "eos_token_id"):
                    mod.eos_token_id = eos_new
            self.proiezione = proiezione

        def forward(self, input_ids):
            e = self.tm(input_ids=input_ids).pooler_output
            if self.proiezione is not None:
                e = self.proiezione(e)
            return e / e.norm(dim=-1, keepdim=True)

    testo = Testo().eval()
    del model
    gc.collect()
    lavoro = os.path.join(motore.dir, "lavoro-telefono")
    if os.path.exists(lavoro):
        import shutil
        shutil.rmtree(lavoro)
    os.makedirs(os.path.join(lavoro, "grezzo"))
    os.makedirs(os.path.join(lavoro, "finale"))
    grezzo = os.path.join(lavoro, "grezzo", "grezzo.onnx")  # l'esportazione può spargere i pesi in molti file
    torch.onnx.export(testo, (torch.tensor(ids_new[:1]),), grezzo, input_names=["input_ids"],
                      output_names=["impronta"], dynamic_axes={"input_ids": {0: "n"}, "impronta": {0: "n"}},
                      opset_version=17, dynamo=False)
    del testo
    gc.collect()
    motore.stato(done=2)
    m = onnx.load(grezzo)
    qz = MatMulNBitsQuantizer(m, block_size=32, is_symmetric=True, bits=bits)
    qz.process()
    finale = os.path.join(lavoro, "finale", "testo.onnx")
    qz.model.save_model_to_file(finale, use_external_data_format=True)
    del m, qz
    gc.collect()
    motore.stato(done=3)

    # controllo: il modello compresso deve dare quasi le stesse impronte dell'originale
    import onnxruntime as ort
    s = ort.InferenceSession(finale, providers=["CPUExecutionProvider"])
    out = s.run(None, {"input_ids": ids_new})[0]
    somiglianza = float(np.sum(rif * out, 1).mean())
    del s
    motore.log(f"telefono: somiglianza media con l'originale {somiglianza:.4f}")
    if somiglianza < 0.97:
        raise RuntimeError(f"modello del telefono troppo diverso dall'originale ({somiglianza:.3f})")

    # nella cartella del telefono: prima i file nuovi con nomi temporanei, poi al loro posto
    os.makedirs(cartella, exist_ok=True)
    dati = [x for x in os.listdir(os.path.dirname(finale)) if x != "testo.onnx"]
    if len(dati) != 1:
        raise RuntimeError(f"file dei pesi inattesi: {dati}")
    with open(os.path.join(cartella, "tokenizer.json.tmp"), "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False)
    os.replace(os.path.join(cartella, "tokenizer.json.tmp"), os.path.join(cartella, "tokenizer.json"))
    # il file ONNX nomina il suo file dei pesi: lo si tiene col nome che ha
    import shutil
    for nome in (dati[0], "testo.onnx"):
        shutil.copyfile(os.path.join(lavoro, "finale", nome), os.path.join(cartella, nome + ".tmp"))
        os.replace(os.path.join(cartella, nome + ".tmp"), os.path.join(cartella, nome))
    shutil.rmtree(lavoro, ignore_errors=True)
    files = {n: {"size": os.path.getsize(os.path.join(cartella, n)), "sha256": sha256(os.path.join(cartella, n))}
             for n in ("testo.onnx", dati[0], "tokenizer.json")}
    motore.stato(done=4)
    return {"model": nome_modello, "bits": bits, "files": files, "onnx": "testo.onnx", "tokenizer": "tokenizer.json",
            "input": "input_ids", "output": "impronta", "max_len": max_len, "pad_id": pad_new, "eos_id": eos_new,
            "lowercase": False, "vocab": len(tenuti), "similarity": round(somiglianza, 4)}


# ---------------------------------------------------------------- lavoro

def lavoro(cfg):
    global GRANDE
    GRANDE = int(cfg.get("big") or GRANDE)  # "big": solo per le prove
    motore = Motore(cfg)
    nome_modello = cfg["model"]
    bits = int(cfg.get("phone_bits") or 8)
    phone_folder = cfg["phone_folder"]
    modello = None
    totale_nuove = 0
    controllo = Controllo(motore)
    nuove_per_lib = {}
    for lib in cfg["libraries"]:
        nome_lib = os.path.basename(os.path.normpath(lib))
        try:
            foto = foto_libreria(lib)
        except (OSError, ValueError, KeyError) as e:
            motore.log(f"{nome_lib}: catalogo non leggibile adesso ({e!r}), riprovo al prossimo giro")
            continue
        cart = os.path.join(motore.dir, "cache", nome_cartella(lib) + "-" + hashlib.sha1(
            os.path.normcase(os.path.normpath(lib)).encode()).hexdigest()[:8], "mini")
        foto = prepara_miniature(motore, cart, foto, nome_lib, controllo)
        ia = os.path.join(phone_folder, nome_cartella(lib), "ia")
        manifest_path = os.path.join(ia, "manifest.json")
        manifest = carica_json(manifest_path, {})
        visivo = os.path.join(ia, "visivo.bin")
        if manifest and manifest.get("model") != nome_modello:  # modello cambiato: le impronte vecchie non valgono più
            for p in (visivo, manifest_path):
                if os.path.exists(p):
                    os.remove(p)
            manifest = {}
        ids_fatti, _ = carica_impronte(visivo)
        mancano = len({p["id"] for p in foto} - set(ids_fatti.tolist() if ids_fatti is not None else []))
        tolte = 0 if ids_fatti is None else len(set(ids_fatti.tolist()) - {p["id"] for p in foto})
        if mancano or tolte:
            if modello is None:
                controllo(grande=mancano > GRANDE)
                controllo.subito_gpu()
                motore.stato(phase="loading", library=nome_lib, done=0, total=0)
                modello = ModelloVisivo(nome_modello)
            fatte = aggiorna_impronte(motore, modello, cart, foto, visivo, nome_lib, controllo)
            totale_nuove += fatte
            if fatte:
                nuove_per_lib[nome_lib] = fatte
        ids_fatti, v = carica_impronte(visivo)
        tel = os.path.join(ia, "telefono")
        tel_info = manifest.get("phone") or {}
        valido = (tel_info.get("model") == nome_modello and tel_info.get("bits") == bits and all(
            os.path.exists(os.path.join(tel, n)) for n in (tel_info.get("files") or {"x": 0})))
        if not valido and ids_fatti is not None:
            if modello is not None:  # libera la GPU e la memoria per la preparazione (sulla CPU)
                del modello
                modello = None
                gc.collect()
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001
                    pass
            tel_info = prepara_telefono(motore, nome_modello, bits, tel)
        if ids_fatti is None:
            continue
        nuovo_manifest = {
            "version": 1, "library": nome_lib, "model": nome_modello, "hf": MODELLI[nome_modello]["hf"],
            "license": "CC-BY-NC-4.0" if nome_modello.startswith("metaclip2") else "",
            "dim": int(v.shape[1]), "count": int(len(ids_fatti)), "visual": "visivo.bin",
            "visual_size": os.path.getsize(visivo), "phone": tel_info,
            "updated": manifest.get("updated") if manifest.get("count") == len(ids_fatti) and not totale_nuove
            else datetime.datetime.now().isoformat(timespec="seconds")}
        if nuovo_manifest != manifest:
            salva_json(manifest_path, nuovo_manifest)
        libs = carica_json(motore.stato_path, {}).get("libraries") or {}
        libs[nome_lib] = {"count": int(len(ids_fatti)), "photos": len(foto), "model": nome_modello,
                          "updated": nuovo_manifest["updated"], "path": lib}
        motore.stato(libraries=libs)
    if totale_nuove:
        motore.log(f"fatto: {totale_nuove} foto nuove")
        # per la notifica di Windows (la mostra l'icona di PresetCraftAppLicator, una volta sola)
        motore.stato(last_run={"id": datetime.datetime.now().isoformat(timespec="seconds"),
                               "libraries": nuove_per_lib})
    motore.stato(phase="idle", library="", done=0, total=0, error="")


def gpu_persa(e):
    testo = repr(e)
    return any(x in testo for x in ("illegal memory access", "CUDA error", "AcceleratorError", "device-side assert",
                                    "unspecified launch failure"))


def figlio(cfg_path):
    cfg = carica_json(cfg_path)
    motore = Motore(cfg)
    try:
        lavoro(cfg)
        return 0
    except Stop as s:
        motore.log({"paused": "in pausa (richiesta)", "gpu_busy": "fermo: la GPU è usata da un altro programma",
                    "waiting_window": "fermo: fuori dalla fascia oraria"}[s.motivo] + ", progressi salvati")
        motore.stato(phase=s.motivo, library="", done=0, total=0)
        return 0
    except Exception as e:  # noqa: BLE001
        if gpu_persa(e):
            motore.log(f"la GPU si è fermata ({type(e).__name__}): riprendo da dove ero arrivato")
            return CODICE_GPU_PERSA
        motore.log("ERRORE " + "".join(traceback.format_exception(e)).strip())
        motore.stato(phase="error", error=f"{type(e).__name__}: {e}")
        return 1


def supervisore(cfg_path):
    """Lancia il lavoro in un processo figlio e lo rilancia (al massimo TENTATIVI volte) se la GPU si ferma."""
    cfg = carica_json(cfg_path)
    motore = Motore(cfg)
    lock = os.path.join(motore.dir, "ia.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        vecchio = carica_json(motore.stato_path, {}).get("pid")
        if vecchio and pid_vivo(vecchio) and vecchio != os.getpid():
            return 0  # c'è già un motore al lavoro
        os.remove(lock)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        motore.stato(phase="starting", error="", supervisor=os.getpid())
        for tentativo in range(1, TENTATIVI + 1):
            r = subprocess.run([sys.executable, os.path.abspath(__file__), "--job", cfg_path],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != CODICE_GPU_PERSA:
                return r.returncode
            time.sleep(10)  # il driver si sta riavviando
        motore.stato(phase="error", error="GPU")
        return 1
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def pid_vivo(pid):
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--job")
    a = ap.parse_args()
    if a.job:
        return figlio(a.job)
    return supervisore(a.config)


if __name__ == "__main__":
    sys.exit(main())
