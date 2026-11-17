"""Profili "Look" di Adobe (.xmp con una tabella colore 3D) trasformati in LUT .cube per LightCraft.

Alcuni profili Camera Matching non sono .dcp ma "Look": il profilo Camera Standard più una tabella colore 3D
(crs:RGBTable), per esempio "Camera Cinema Yellow" e "Camera Cinema Green" della Ricoh GR IV. LightCraft non legge
questi .xmp ma importa le LUT 3D (.cube), applicate al colore finale della foto. Qui la tabella viene decodificata e
riscritta come .cube: solo lo spostamento di colore del look, senza il suo cambio di luminosità (LightCraft ha già la
sua curva di tono e il look la applicherebbe una seconda volta: sulle foto di prova così il risultato è il più vicino
all'anteprima della fotocamera).
"""

import os
import re
import struct
import zlib

# alfabeto della codifica base85 usata da Adobe per le tabelle (dng_big_table)
ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?`'|()[]{}@%$#"
DECODE = {c: i for i, c in enumerate(ALPHABET)}
CUBE_SIZE = 33

# primari delle tabelle Adobe (dng_rgb_table): 0 sRGB, 1 Adobe RGB, 2 ProPhoto, 3 P3, 4 Rec.2020 → matrice RGB→XYZ (D65,
# ProPhoto adattato a D65 con Bradford)
PRIMARIES = {
    0: ((0.4124564, 0.3575761, 0.1804375), (0.2126729, 0.7151522, 0.0721750), (0.0193339, 0.1191920, 0.9503041)),
    1: ((0.5767309, 0.1855540, 0.1881852), (0.2973769, 0.6273491, 0.0752741), (0.0270343, 0.0706872, 0.9911085)),
    2: ((0.7529, 0.1446, 0.0530), (0.2765, 0.7192, 0.0043), (-0.0027, 0.0034, 1.0482)),
    3: ((0.4865709, 0.2656677, 0.1982173), (0.2289746, 0.6917385, 0.0792869), (0.0, 0.0451134, 1.0439444)),
    4: ((0.6369580, 0.1446169, 0.1688810), (0.2627002, 0.6779981, 0.0593017), (0.0, 0.0280727, 1.0609851)),
}
SRGB = PRIMARIES[0]
WHITE = tuple(sum(row) for row in SRGB)


# ---------------------------------------------------------------- lettura del file .xmp

def _unescape(text):
    return (text.replace("&quot;", '"').replace("&apos;", "'").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&#xA;", "\n").replace("&amp;", "&"))


def _attr(text, name):
    m = re.search(r'crs:%s="([^"]*)"' % name, text)
    return _unescape(m.group(1)) if m else None


def look_info(path, head_only=False):
    """{"camera", "name", "version"} di un profilo Look con tabella 3D, oppure None se il file non lo è."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(6000) if head_only else f.read()
    except OSError:
        return None
    if _attr(text, "PresetType") != "Look" or not _attr(text, "RGBTable"):
        return None
    camera = _attr(text, "CameraModelRestriction")
    if not camera:
        return None
    m = re.search(r"<crs:Name>\s*<rdf:Alt>\s*<rdf:li[^>]*>([^<]*)</rdf:li>", text)
    if not m and head_only:  # il nome può stare dopo la tabella: si legge il file intero
        return look_info(path)
    name = _unescape(m.group(1)).strip() if m else os.path.splitext(os.path.basename(path))[0]
    try:
        version = float(_attr(text, "Version") or 0)
    except ValueError:
        version = 0.0
    return {"camera": camera, "name": name, "version": version}


def _base85(text):
    chars = [DECODE[c] for c in text if c in DECODE]
    out = bytearray()
    for i in range(0, len(chars), 5):
        group = chars[i:i + 5]
        value = sum(v * 85 ** k for k, v in enumerate(group))
        out += struct.pack("<I", value & 0xFFFFFFFF)[:4 if len(group) == 5 else len(group) - 1]
    return bytes(out)


def read_table(path):
    """La tabella 3D del look: (campioni [r][g][b] → (r, g, b) 0..1, divisioni, primari, gamma)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    table_id = _attr(text, "RGBTable")
    m = re.search(r'crs:Table_%s="([^"]*)"' % re.escape(table_id or ""), text, re.S)
    if not m:
        raise ValueError("no RGB table")
    raw = _base85(_unescape(m.group(1)))
    data = zlib.decompress(raw[4:])  # 4 byte: lunghezza non compressa
    kind, version, dims, n = struct.unpack("<4I", data[:16])
    if (kind, version, dims) != (1, 1, 3) or not 2 <= n <= 64:
        raise ValueError(f"unsupported table ({kind}, {version}, {dims}, {n})")
    count = n ** 3 * 3
    deltas = struct.unpack("<%dH" % count, data[16:16 + count * 2])
    primaries, gamma = struct.unpack("<2I", data[16 + count * 2:24 + count * 2])
    nop = [(i * 0xFFFF + (n >> 1)) // (n - 1) for i in range(n)]
    samples = []
    k = 0
    for r in range(n):
        for g in range(n):
            for b in range(n):
                samples.append(tuple(((deltas[k + c] + base) & 0xFFFF) / 65535.0
                                     for c, base in enumerate((nop[r], nop[g], nop[b]))))
                k += 3
    return samples, n, primaries, gamma


# ---------------------------------------------------------------- colore

def _mat_mul(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def _mat_inv(m):
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return ((e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det), \
           ((f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det), \
           ((d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det)


def _mat_prod(a, b):
    return tuple(tuple(sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)) for r in range(3))


def _srgb_to_lin(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _lin_to_srgb(v):
    v = min(max(v, 0.0), 1.0)
    return v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


def _encode(gamma):
    """(lineare → codificato, codificato → lineare) per la gamma della tabella."""
    if gamma == 0:
        return (lambda v: v), (lambda v: v)
    if gamma == 1:
        return _lin_to_srgb, _srgb_to_lin
    exp = {2: 1.8, 3: 563 / 256, 4: 2.4}.get(gamma, 563 / 256)
    return (lambda v: max(v, 0.0) ** (1 / exp)), (lambda v: max(v, 0.0) ** exp)


def _f(t):
    return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29


def _finv(t):
    return t ** 3 if t > 6 / 29 else 3 * (6 / 29) ** 2 * (t - 4 / 29)


def _lab(lin):
    x, y, z = (c / w for c, w in zip(_mat_mul(SRGB, lin), WHITE))
    fx, fy, fz = _f(x), _f(y), _f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


SRGB_INV = _mat_inv(SRGB)


def _from_lab(lab):
    fy = (lab[0] + 16) / 116
    xyz = tuple(_finv(t) * w for t, w in zip((fy + lab[1] / 500, fy, fy - lab[2] / 200), WHITE))
    return _mat_mul(SRGB_INV, xyz)


def _trilinear(samples, n, p):
    idx, frac = [], []
    for v in p:
        v = min(max(v, 0.0), 1.0) * (n - 1)
        i = min(int(v), n - 2)
        idx.append(i)
        frac.append(v - i)
    out = [0.0, 0.0, 0.0]
    for dr in (0, 1):
        wr = frac[0] if dr else 1 - frac[0]
        for dg in (0, 1):
            wg = frac[1] if dg else 1 - frac[1]
            for db in (0, 1):
                w = wr * wg * (frac[2] if db else 1 - frac[2])
                if w:
                    s = samples[((idx[0] + dr) * n + idx[1] + dg) * n + idx[2] + db]
                    out[0] += w * s[0]
                    out[1] += w * s[1]
                    out[2] += w * s[2]
    return out


def to_cube(path, out_path, title=None, size=CUBE_SIZE):
    """Scrive la LUT .cube (ingresso e uscita sRGB) del look `path`; restituisce out_path."""
    samples, n, primaries, gamma = read_table(path)
    table_rgb = PRIMARIES.get(primaries, PRIMARIES[1])
    to_table = _mat_prod(_mat_inv(table_rgb), SRGB)   # sRGB lineare → primari della tabella
    from_table = _mat_inv(to_table)
    enc, dec = _encode(gamma)
    title = title or (look_info(path) or {}).get("name") or os.path.splitext(os.path.basename(path))[0]
    grid = [i / (size - 1) for i in range(size)]
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    for b in grid:  # nel formato .cube il rosso cambia più in fretta
        for g in grid:
            for r in grid:
                lin = (_srgb_to_lin(r), _srgb_to_lin(g), _srgb_to_lin(b))
                coded = [enc(max(v, 0.0)) for v in _mat_mul(to_table, lin)]
                looked = _mat_mul(from_table, [dec(v) for v in _trilinear(samples, n, coded)])
                # si tiene il colore del look e la luminosità di partenza
                lab_in, lab_out = _lab(lin), _lab(looked)
                out = _from_lab((lab_in[0], lab_out[1], lab_out[2]))
                lines.append("%.6f %.6f %.6f" % tuple(_lin_to_srgb(v) for v in out))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return out_path
