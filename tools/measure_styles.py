"""Misura gli stili della fotocamera e ne ricava LUT 3D per LightCraft.

Per ogni raw: la resa di LightCraft con le impostazioni predefinite (lightcraft-cli render) e l'anteprima a piena
risoluzione salvata dalla fotocamera nel file, che ha già lo stile applicato. Le due immagini vengono dallo stesso raw,
quindi coincidono pixel per pixel: per ogni stile si stima la LUT "colore di LightCraft -> colore della fotocamera"
(un modello quadratico più una griglia 17x17x17 di correzioni con vincolo di regolarità, così anche i colori che
mancano nelle foto ricevono un valore sensato) e la si scrive come .cube 33x33x33.

Uso (Python con numpy e Pillow):
    python tools/measure_styles.py CARTELLA_O_FILE.DNG [...] --out assets/profiles

Il risultato: <out>/<modello>/<stile>.cube e <out>/<modello>/profiles.json (stile -> file, nome, preferenza).
La grana, la vignettatura e gli altri effetti locali di alcuni stili non sono colore: una LUT non li riproduce.
"""

import argparse
import glob
import io
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import camera_styles as cs  # noqa: E402

CLI = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\ArtCraft\LightCraft\lightcraft-cli.exe")
N = 17
# stili per cui il profilo di Adobe resta la scelta automatica: sulle foto di prova era più vicino alla fotocamera
ADOBE_FIRST = {"Monotone", "Soft Monotone", "Hard Monotone"}
SIZE = 1548  # lato lungo delle immagini confrontate


# ---------------------------------------------------------------- coppie di immagini

def preview(path):
    data = open(path, "rb").read()
    best, i = None, 0
    while True:
        s = data.find(b"\xff\xd8\xff", i)
        if s < 0:
            break
        try:
            im = Image.open(io.BytesIO(data[s:]))
            im.load()
            if best is None or im.size[0] * im.size[1] > best.size[0] * best.size[1]:
                best = im
        except Exception:  # noqa: BLE001 — non è un JPEG intero
            pass
        i = s + 3
    return best.convert("RGB") if best else None


def pair(path, work, cli):
    """(colori di LightCraft, colori della fotocamera) come array Nx3 0..1, oppure None."""
    stem = os.path.splitext(os.path.basename(path))[0]
    lc_path = os.path.join(work, stem + "-lc.png")
    if not os.path.exists(lc_path):
        r = subprocess.run([cli, "render", path, "-o", lc_path, "--size", str(SIZE)], capture_output=True, text=True)
        if r.returncode:
            print("  render non riuscito:", stem, r.stderr[-200:])
            return None
    lc = Image.open(lc_path).convert("RGB")
    cam = preview(path)
    if cam is None:
        return None
    small = (lc.width // 4, lc.height // 4)
    ref = np.asarray(lc.resize(small, Image.BOX), float).mean(2)
    best = None
    for rot in (None, Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180):
        c = cam.transpose(rot) if rot is not None else cam
        if abs(c.width / c.height - lc.width / lc.height) > 0.02:
            continue
        # somiglianza della struttura (non della luminosità: gli stili in bianco e nero cambiano molto i toni)
        e = -np.corrcoef(np.asarray(c.resize(small, Image.BOX), float).mean(2).ravel(), ref.ravel())[0, 1]
        if best is None or e < best[0]:
            best = (e, c)
    if best is None or best[0] > -0.6:
        print("  anteprima non allineata:", stem)
        return None
    s = (lc.width // 2, lc.height // 2)  # media 2x2: meno rumore e grana
    a = np.asarray(lc.resize(s, Image.BOX), float) / 255
    b = np.asarray(best[1].resize(s, Image.BOX), float) / 255
    my, mx = int(s[1] * 0.04), int(s[0] * 0.04)  # i bordi risentono di vignettatura e correzioni ottiche
    return a[my:-my, mx:-mx].reshape(-1, 3), b[my:-my, mx:-mx].reshape(-1, 3)


def bin_samples(x, y, bins=48):
    q = np.minimum((x * bins).astype(int), bins - 1)
    key = (q[:, 0] * bins + q[:, 1]) * bins + q[:, 2]
    uniq, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    xs, ys = np.zeros((len(uniq), 3)), np.zeros((len(uniq), 3))
    np.add.at(xs, inv, x)
    np.add.at(ys, inv, y)
    return xs / cnt[:, None], ys / cnt[:, None], cnt


# ---------------------------------------------------------------- stima della LUT

def features(x):
    r, g, b = x[:, 0], x[:, 1], x[:, 2]
    return np.stack([r, g, b, r * r, g * g, b * b, r * g, r * b, g * b, np.ones_like(r)], 1)


def corners(x, n=N):
    p = np.clip(x, 0, 1) * (n - 1)
    i0 = np.minimum(np.floor(p).astype(int), n - 2)
    f = p - i0
    idx, w = [], []
    for dr in (0, 1):
        for dg in (0, 1):
            for db in (0, 1):
                idx.append(((i0[:, 0] + dr) * n + i0[:, 1] + dg) * n + i0[:, 2] + db)
                w.append((f[:, 0] if dr else 1 - f[:, 0]) * (f[:, 1] if dg else 1 - f[:, 1]) *
                         (f[:, 2] if db else 1 - f[:, 2]))
    return np.stack(idx, 1), np.stack(w, 1)


def laplacian(n=N):
    m = n ** 3
    L = np.zeros((m, m))
    for axis in range(3):
        for r in range(n):
            for g in range(n):
                for b in range(n):
                    c = [r, g, b]
                    if c[axis] in (0, n - 1):
                        continue
                    ids = []
                    for d in (-1, 0, 1):
                        q = list(c)
                        q[axis] += d
                        ids.append((q[0] * n + q[1]) * n + q[2])
                    for i, ci in zip(ids, (1.0, -2.0, 1.0)):
                        for j, cj in zip(ids, (1.0, -2.0, 1.0)):
                            L[i, j] += ci * cj
    return L


class Model:
    def __init__(self, coef, resid):
        self.coef, self.resid = coef, resid

    def __call__(self, x):
        idx, w = corners(x)
        return np.clip(features(x) @ self.coef + (self.resid[idx] * w[..., None]).sum(1), 0, 1)


def fit(xs, ys, cnt, lap, smooth=3.0, ridge=0.02, cap=400):
    w = np.minimum(cnt, cap).astype(float)
    F = features(xs)
    coef = np.linalg.lstsq(F * np.sqrt(w)[:, None], ys * np.sqrt(w)[:, None], rcond=None)[0]
    r = ys - F @ coef
    idx, cw = corners(xs)
    m = N ** 3
    A, B = np.zeros((m, m)), np.zeros((m, 3))
    for a in range(8):
        np.add.at(B, idx[:, a], (w * cw[:, a])[:, None] * r)
        for b in range(8):
            np.add.at(A, (idx[:, a], idx[:, b]), w * cw[:, a] * cw[:, b])
    scale = w.sum() / m
    A += smooth * scale * lap + ridge * scale * np.eye(m)
    return Model(coef, np.linalg.solve(A, B))


def write_cube(model, path, title, size=33):
    g = np.linspace(0, 1, size)
    b_, g_, r_ = np.meshgrid(g, g, g, indexing="ij")  # nel formato .cube il rosso cambia più in fretta
    out = model(np.stack([r_, g_, b_], -1).reshape(-1, 3))
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(f'TITLE "{title}"\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        for v in out:
            f.write("%.6f %.6f %.6f\n" % tuple(v))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", default="assets/profiles")
    ap.add_argument("--work", default=os.path.join(os.environ.get("TEMP", "."), "measure-styles"))
    ap.add_argument("--cli", default=CLI)
    args = ap.parse_args()
    files = []
    for p in args.paths:
        files += sorted(glob.glob(os.path.join(p, "*.DNG")) + glob.glob(os.path.join(p, "*.dng"))) if os.path.isdir(p) else [p]
    os.makedirs(args.work, exist_ok=True)
    groups = {}
    for path in sorted(set(files)):
        st = cs.read_style(path)
        if not st["key"] or not st["name"]:
            continue
        groups.setdefault((st["model"], st["key"], st["name"]), []).append(path)
    lap = laplacian()
    manifests = {}
    for (model, key, name), paths in sorted(groups.items(), key=lambda kv: kv[0][1]):
        data = [d for d in (pair(p, args.work, args.cli) for p in paths) if d]
        if not data:
            continue
        binned = [bin_samples(x, y) for x, y in data]
        xs, ys, cnt = (np.concatenate([b[i] for b in binned]) for i in range(3))
        model_fit = fit(xs, ys, cnt, lap)
        folder = os.path.join(args.out, model)
        os.makedirs(folder, exist_ok=True)
        file = name.replace("/", "-") + ".cube"
        title = f"Measured {name}"
        write_cube(model_fit, os.path.join(folder, file), title)
        manifests.setdefault(model, {})[key] = {"style": name, "file": file, "name": title, "photos": len(data),
                                                "prefer": name not in ADOBE_FIRST}
        print(f"{model} {name}: {len(data)} foto -> {file}")
    for model, entries in manifests.items():
        path = os.path.join(args.out, model, "profiles.json")
        old = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                old = json.load(f).get("styles", {})
        old.update(entries)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"camera": model, "renderer": "LightCraft default", "styles": dict(sorted(old.items()))}, f,
                      indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
