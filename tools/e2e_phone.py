"""Prove da capo a fondo del giro telefono → NAS → PresetCraftAppLicator → LightCraft → telefono, sul sistema vero.

Scrive nella coda della libreria (<cartella di servizio>/libraries/<libreria>/changes) le stesse richieste che manda
ViewerCraftStar (LibraryEdits.send), aspetta che PresetCraftAppLicator (in esecuzione) le applichi e controlla il
catalogo di LightCraft e i file per il telefono (indice, develop.json, anteprime). Da usare SOLO su foto di prova.

uso: python e2e_phone.py <libreria> <id,id,…> [prove…]
"""
import gzip
import json
import os
import random
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
import lightcraft as lc  # noqa: E402
import phone_develop  # noqa: E402
import watcher  # noqa: E402

SETTINGS = json.load(open(os.path.join(lc.DATA_DIR, "settings.json"), encoding="utf-8"))
FOLDER, _LIBS = watcher.phone_settings(SETTINGS)
CLI = SETTINGS.get("cli") or lc.DEFAULT_CLI
TIMEOUT = 600
results = []


def svc(lib):
    return watcher.library_dir(FOLDER, lib)


def catalog(lib):
    return lc.read_catalog(lib)


def photos(lib):
    return catalog(lib)["photos"]


def index(lib):
    return json.load(gzip.open(os.path.join(svc(lib), "index.json.gz")))


def develop_json(lib):
    return json.load(gzip.open(os.path.join(svc(lib), "develop.json.gz")))


def send(lib, changes):
    """Una richiesta come quella del telefono: torna il nome del file."""
    now = int(time.time() * 1000)
    for c in changes:
        c.setdefault("at", now)
    body = {"version": 1, "device": "ViewerCraftStar", "created": now, "changes": changes}
    d = os.path.join(svc(lib), "changes")
    os.makedirs(d, exist_ok=True)
    name = f"{now}-{random.randint(1000, 9999)}.json"
    tmp = os.path.join(d, "." + name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f)
    os.replace(tmp, os.path.join(d, name))
    return name


def wait_applied(lib, name, timeout=TIMEOUT):
    """Aspetta che la richiesta finisca in changes/done e che l'indice per il telefono sia ripubblicato dopo."""
    d = os.path.join(svc(lib), "changes")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(os.path.join(d, "done", name)):
            moved = os.path.getmtime(os.path.join(d, "done", name))
            # l'indice si ripubblica subito dopo (stesso giro)
            while time.time() - t0 < timeout:
                if os.path.getmtime(os.path.join(svc(lib), "index.json.gz")) >= moved - 2:
                    return time.time() - t0
                time.sleep(3)
        time.sleep(3)
    raise TimeoutError(f"{name} non applicata in {timeout} s")


def wait_for(pred, timeout=TIMEOUT, step=5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pred():
                return time.time() - t0
        except Exception:  # noqa: BLE001
            pass
        time.sleep(step)
    raise TimeoutError("condizione non raggiunta")


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def in_index(lib, pid):
    return next((p for p in index(lib)["photos"] if p["i"] == pid), None)


def stack_of(lib, pid):
    for s in (catalog(lib).get("stacks") or {}).values():
        members = [int(x) for x in s.get("photos") or []]
        if pid in members:
            return members
    return None


def preview_sig(lib, pid):
    pv = json.load(open(os.path.join(svc(lib), "previews.json"), encoding="utf-8"))
    return pv["photos"].get(str(pid))


# ---------------------------------------------------------------- prove

def t_metadata(lib, ids):
    a, b, c = ids[0], ids[1], ids[2]
    n = send(lib, [{"id": a, "rating": 3, "flag": "pick", "label": "red"},
                   {"id": b, "rating": 1, "flag": "reject", "label": "blue"},
                   {"id": c, "title": "Prova titolo", "caption": "Una didascalia di prova", "keywords": ["prova", "e2e", "città"]}])
    s = wait_applied(lib, n)
    ph = photos(lib)
    pa, pb, pc = ph[str(a)], ph[str(b)], ph[str(c)]
    check("stelle/bandierina/etichetta (A)", (pa["rating"], pa["flag"], pa.get("label")) == (3, "pick", "red"),
          f"{pa['rating']} {pa['flag']} {pa.get('label')} in {s:.0f}s")
    check("stelle/bandierina/etichetta (B)", (pb["rating"], pb["flag"], pb.get("label")) == (1, "reject", "blue"),
          f"{pb['rating']} {pb['flag']} {pb.get('label')}")
    m = pc.get("meta") or {}
    check("titolo/didascalia/parole chiave", m.get("title") == "Prova titolo" and m.get("caption") == "Una didascalia di prova"
          and sorted(m.get("keywords") or []) == ["città", "e2e", "prova"], json.dumps({k: m.get(k) for k in ("title", "caption", "keywords")}, ensure_ascii=False))
    ia = in_index(lib, a)
    check("indice del telefono aggiornato", ia and ia.get("r") == 3, str({k: ia.get(k) for k in ("r", "f", "l")} if ia else None))


def t_stacks(lib, ids):
    a, b, c = ids[0], ids[1], ids[2]
    n = send(lib, [{"id": x, "stack": a} for x in (a, b, c)])
    wait_applied(lib, n)
    st = stack_of(lib, a)
    check("crea pila (copertina A)", st is not None and st[0] == a and set(st) == {a, b, c}, str(st))
    n = send(lib, [{"id": b, "stack": b}])
    wait_applied(lib, n)
    st = stack_of(lib, a)
    check("rendi copertina B", st is not None and st[0] == b and set(st) == {a, b, c}, str(st))
    n = send(lib, [{"id": c, "stack": 0}])
    wait_applied(lib, n)
    check("togli C dalla pila", stack_of(lib, c) is None and set(stack_of(lib, a) or []) == {a, b}, str(stack_of(lib, a)))
    n = send(lib, [{"id": c, "stack": b}])
    wait_applied(lib, n)
    check("rimetti C nella pila", set(stack_of(lib, a) or []) == {a, b, c}, str(stack_of(lib, a)))


def t_trash(lib, ids):
    x = ids[-1]
    before = stack_of(lib, x)
    n = send(lib, [{"id": x, "trash": True}])
    wait_applied(lib, n)
    p = photos(lib)[str(x)]
    idx = index(lib)
    in_trash = x in [t["i"] if isinstance(t, dict) else t for t in idx.get("trash") or []]
    check("cestino", p.get("deleted") is True and in_trash, f"deleted={p.get('deleted')} index.trash={in_trash}")
    n = send(lib, [{"id": x, "trash": False}])
    wait_applied(lib, n)
    p = photos(lib)[str(x)]
    back = stack_of(lib, x)
    check("ripristino dal cestino", p.get("deleted") is False and (before is None or (back and set(back) == set(before))),
          f"deleted={p.get('deleted')} stack prima {before} dopo {back}")


def t_develop(lib, ids):
    x = ids[3]
    p = photos(lib)[str(x)]
    cur = p["develop"]
    base = phone_develop.develop_signature(cur)
    old_prev = preview_sig(lib, x)
    s = json.loads(json.dumps(cur))
    s["light"]["exposure"] = 0.75
    s["light"]["contrast"] = 20
    n = send(lib, [{"id": x, "develop": {"settings": s, "base": base}}])
    wait_applied(lib, n)
    p = photos(lib)[str(x)]
    check("sviluppo dal telefono applicato", abs(p["develop"]["light"]["exposure"] - 0.75) < 1e-3 and p["develop"]["light"]["contrast"] == 20
          and p["history"][-1]["label"] == "ViewerCraftStar", f"history {[h.get('label') for h in p['history']][-3:]}")
    dj = develop_json(lib)
    check("develop.json ha la firma nuova", dj["photos"][str(x)][0] == phone_develop.develop_signature(p["develop"]))
    t = wait_for(lambda: preview_sig(lib, x) not in (None, old_prev), timeout=900)
    check("anteprima rifatta dal PC", True, f"in {t:.0f}s")
    # stesso sviluppo rimandato con la firma vecchia (il telefono non aveva ancora le impostazioni nuove): niente copia
    copies_before = sum(1 for q in photos(lib).values() if q.get("copy_of") == x)
    s2 = json.loads(json.dumps(s))
    s2["light"]["exposure"] = 0.5
    n = send(lib, [{"id": x, "develop": {"settings": s2, "base": base}}])
    wait_applied(lib, n)
    p = photos(lib)[str(x)]
    copies_after = sum(1 for q in photos(lib).values() if q.get("copy_of") == x)
    check("seconda modifica di fila: nessun falso conflitto", copies_after == copies_before and abs(p["develop"]["light"]["exposure"] - 0.5) < 1e-3,
          f"copie {copies_before}->{copies_after}")
    # conflitto vero: il PC cambia la foto, il telefono manda con la firma di prima
    base_now = phone_develop.develop_signature(p["develop"])
    lc.run_cli(CLI, lib, [("library.select", {"ids": [x], "active": x}), ("develop.set", {"control": "light.highlights", "value": -30})])
    s3 = json.loads(json.dumps(p["develop"]))
    s3["light"]["exposure"] = -0.4
    n = send(lib, [{"id": x, "develop": {"settings": s3, "base": base_now}}])
    wait_applied(lib, n)
    ph = photos(lib)
    tel = [q for q in ph.values() if q.get("copy_of") == x and q.get("copy_name") == "Telefono"]
    px = ph[str(x)]
    check("conflitto vero: copia «Telefono»", len(tel) == 1 and abs(tel[0]["develop"]["light"]["exposure"] + 0.4) < 1e-3
          and px["develop"]["light"]["highlights"] == -30, f"copie Telefono {[q['id'] for q in tel]}")
    return [q["id"] for q in tel]


def t_profiles(lib, ids):
    x = ids[1]
    p = photos(lib)[str(x)]
    base = phone_develop.develop_signature(p["develop"])
    old_prev = preview_sig(lib, x)
    s = json.loads(json.dumps(p["develop"]))
    s["profile"]["id"] = "dcp:ricoh-gr-iv-ricoh-gr-iv-camera-monotone"
    n = send(lib, [{"id": x, "develop": {"settings": s, "base": base}}])
    wait_applied(lib, n)
    p = photos(lib)[str(x)]
    check("profilo DCP dal telefono", p["develop"]["profile"]["id"] == s["profile"]["id"])
    idx = in_index(lib, x)
    t = wait_for(lambda: preview_sig(lib, x) not in (None, old_prev), timeout=900)
    check("anteprima con il profilo nuovo", True, f"in {t:.0f}s")
    return idx


def t_masks(lib, ids):
    x = ids[4]
    p = photos(lib)[str(x)]
    base = phone_develop.develop_signature(p["develop"])
    s = json.loads(json.dumps(p["develop"]))
    s["masks"] = [
        {"id": 1, "name": "Maschera 1", "visible": True, "invert": False, "on_photo": True,
         "components": [{"op": "add", "invert": False, "shape": {"kind": "linear", "start": {"x": 0.5, "y": 0.0}, "end": {"x": 0.5, "y": 0.45}}}],
         "adjust": {"exposure": -1.2, "amount": 100.0}},
        {"id": 2, "name": "Maschera 2", "visible": True, "invert": False, "on_photo": True,
         "components": [{"op": "add", "invert": False, "shape": {"kind": "radial", "center": {"x": 0.5, "y": 0.6}, "rx": 0.2, "ry": 0.15,
                                                                   "angle": 30.0, "feather": 50.0, "invert": False}}],
         "adjust": {"exposure": 0.8, "saturation": 30.0, "amount": 100.0}}]
    n = send(lib, [{"id": x, "develop": {"settings": s, "base": base}}])
    wait_applied(lib, n)
    m = photos(lib)[str(x)]["develop"].get("masks") or []
    check("maschere lineare e radiale dal telefono", [k["components"][0]["shape"]["kind"] for k in m] == ["linear", "radial"]
          and abs(m[1]["adjust"]["exposure"] - 0.8) < 1e-3)
    base = phone_develop.develop_signature(photos(lib)[str(x)]["develop"])
    s["masks"] = s["masks"][1:]
    n = send(lib, [{"id": x, "develop": {"settings": s, "base": base}}])
    wait_applied(lib, n)
    m = photos(lib)[str(x)]["develop"].get("masks") or []
    check("maschera eliminata dal telefono", [k["name"] for k in m] == ["Maschera 2"])


def t_paste(lib, ids):
    targets = ids[0:3]
    ph = photos(lib)
    ch = []
    for x in targets:
        s = json.loads(json.dumps(ph[str(x)]["develop"]))
        s["color"]["vibrance"] = 25
        s["effects"]["clarity"] = 15
        ch.append({"id": x, "develop": {"settings": s, "base": phone_develop.develop_signature(ph[str(x)]["develop"])}})
    n = send(lib, ch)
    wait_applied(lib, n)
    ph = photos(lib)
    ok = all(ph[str(x)]["develop"]["color"]["vibrance"] == 25 and ph[str(x)]["develop"]["effects"]["clarity"] == 15 for x in targets)
    check("incolla su più foto", ok)


def t_copies(lib, ids):
    x = ids[2]
    token = str(uuid.uuid4())
    clean = photos(lib)[str(x)]["history"][0]["settings"]
    s = json.loads(json.dumps(clean))
    s["light"]["exposure"] = 0.6
    tmp = -(int(time.time()) % 2000000000) - 1  # negativo, come il telefono
    n = send(lib, [{"id": tmp, "copy": {"token": token, "parent": x, "name": "Copia e2e"}, "rating": 2,
                    "develop": {"settings": s, "base": "copy:" + token}}])
    wait_applied(lib, n)
    ph = photos(lib)
    c = [q for q in ph.values() if q.get("copy_of") == x and q.get("copy_name") == "Copia e2e"]
    check("copia virtuale dal telefono", len(c) == 1 and abs(c[0]["develop"]["light"]["exposure"] - 0.6) < 1e-3 and c[0].get("rating") == 2,
          f"{[(q['id'], q['develop']['light']['exposure'], q.get('rating')) for q in c]}")
    s["light"]["exposure"] = 0.9
    n = send(lib, [{"id": tmp, "copy": {"token": token, "parent": x, "name": "Copia e2e"}, "develop": {"settings": s, "base": "copy:" + token}}])
    wait_applied(lib, n)
    c = [q for q in photos(lib).values() if q.get("copy_of") == x and q.get("copy_name") == "Copia e2e"]
    dj = develop_json(lib)
    check("copia rimandata: nessun doppione, sviluppo aggiornato", len(c) == 1 and abs(c[0]["develop"]["light"]["exposure"] - 0.9) < 1e-3
          and dj.get("copies", {}).get(token) == c[0]["id"], f"{len(c)} copie, develop.json copies {dj.get('copies', {}).get(token)}")
    tok2 = str(uuid.uuid4())
    before = len(photos(lib))
    n = send(lib, [{"id": tmp - 1, "copy": {"token": tok2, "parent": x, "name": "Copia tolta"}, "trash": True}])
    wait_applied(lib, n)
    check("copia tolta prima della creazione: non nasce", len(photos(lib)) == before)
    return [q["id"] for q in c]


def t_discard(lib, ids):
    """«Togli le modifiche del telefono» dopo che sono già arrivate: il telefono rimanda lo sviluppo di casa."""
    x = ids[3]
    p = photos(lib)[str(x)]
    home = p["develop"]
    n = send(lib, [{"id": x, "develop": {"settings": home, "base": phone_develop.develop_signature(home)}}])
    wait_applied(lib, n)
    p2 = photos(lib)[str(x)]
    check("togli modifiche (sviluppo di casa rimandato): nessun cambio, nessuna copia",
          phone_develop.develop_signature(p2["develop"]) == phone_develop.develop_signature(home)
          and sum(1 for q in photos(lib).values() if q.get("copy_of") == x and q.get("copy_name") == "Telefono") <= 1)


TESTS = {"metadata": t_metadata, "stacks": t_stacks, "trash": t_trash, "develop": t_develop, "profiles": t_profiles,
         "masks": t_masks, "paste": t_paste, "copies": t_copies, "discard": t_discard}

if __name__ == "__main__":
    lib = sys.argv[1]
    ids = [int(x) for x in sys.argv[2].split(",")]
    names = sys.argv[3:] or list(TESTS)
    for nm in names:
        print(f"--- {nm}", flush=True)
        try:
            out = TESTS[nm](lib, ids)
            if out:
                print(f"    (creati: {out})", flush=True)
        except Exception as e:  # noqa: BLE001
            check(nm, False, repr(e))
    bad = [r for r in results if not r[1]]
    print(f"=== {len(results) - len(bad)} PASS, {len(bad)} FAIL")
