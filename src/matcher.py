"""Quali foto della libreria ricevono quale profilo, e l'applicazione con lightcraft-cli."""

import json
import math
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import camera_styles as cs
import looks
from lightcraft import (DATA_DIR, DEFAULT_PROFILE, backup_library, imported_profiles, library_open_reason, log,
                        import_batches, read_catalog, recent_from, run_cli)

CACHE = os.path.join(DATA_DIR, "styles-cache.json")
# stati di una foto: apply = riceverà il profilo, ok = lo ha già, manual = profilo scelto a mano in LightCraft,
# nomatch = stile senza un profilo corrispondente, nostyle = stile non leggibile, missing = file non trovato
STATUSES = ("apply", "ok", "manual", "phone", "nomatch", "nostyle", "missing")  # phone = preset scelto con ViewerCraftStar
CHUNK = 120  # foto per ogni esecuzione di lightcraft-cli (per mostrare l'avanzamento)


def io_workers():
    """Thread per leggere i file: il doppio dei core logici, tra 4 e 16. Il lavoro aspetta soprattutto il disco o il
    NAS (non la CPU), quindi più thread sovrappongono le attese; oltre 16 un NAS domestico non risponde più in fretta."""
    return max(4, min(16, 2 * (os.cpu_count() or 2)))


def chunk_size(total, minimum, steps=20):
    """Foto per ogni esecuzione di lightcraft-cli: circa `steps` passi di avanzamento, mai meno di `minimum` foto
    (ogni esecuzione rilegge il catalogo: su una libreria grande costa più di mezzo secondo)."""
    return max(minimum, math.ceil(total / steps))


def camera_name(meta):
    """"RICOH IMAGING COMPANY, LTD. RICOH GR IV" → "RICOH GR IV": il modello ripete già la marca."""
    camera, make = (meta.get("camera") or "").strip(), (meta.get("make") or "").strip()
    if make and camera.lower().startswith(make.lower()) and len(camera) > len(make):
        camera = camera[len(make):].strip()
    return camera


def mapping_for(settings, model, style_key):
    """Scelta dell'utente per uno stile: None = automatica, "" = non cambiare, altrimenti il nome del profilo."""
    return (settings.get("mappings") or {}).get(cs.norm(model), {}).get(style_key)


def target_profile(settings, finder, model, style):
    """(nome del profilo, percorso del .dcp, del Look .xmp o della LUT misurata) da dare a uno scatto, oppure
    (None, None)."""
    profiles = finder.profiles_for(model)
    chosen = mapping_for(settings, model, style["key"])
    if chosen == "":
        return None, None
    name = chosen if chosen in profiles else cs.auto_profile(style["name"], profiles, finder.preferred(model))
    return (name, profiles[name]) if name else (None, None)


def load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def read_entry(path, hit):
    """Voce della cache per un file: [dimensione, data, modello, chiave, codice], riletta solo se il file è cambiato;
    None se il file non c'è. Non tocca la cache: si può chiamare da più thread."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if hit and hit[0] == st.st_size and hit[1] == st.st_mtime:
        return hit
    try:
        s = cs.read_style(path)
    except (OSError, ValueError, Exception):  # noqa: BLE001 — file danneggiato: stile non leggibile
        s = {"model": "", "key": None, "code": None}
    return [st.st_size, st.st_mtime, s["model"], s["key"], s["code"]]


def style_from(entry):
    if entry is None:
        return None
    model, key, code = entry[2:5]
    return {"model": model, "key": key, "code": code,
            "name": cs.PENTAX_TONES.get(code) if key and key.startswith("pentax:") else None}


def style_of(path, cache):
    """Stile dello scatto, letto dal file o dalla cache se il file non è cambiato; None se il file non c'è."""
    entry = read_entry(path, cache.get(path))
    if entry is not None:
        cache[path] = entry
    return style_from(entry)


def read_styles(paths, cache, progress=None):
    """Stili di molti file insieme, letti in parallelo (sul NAS ogni file costa un'attesa di rete): {percorso: stile}.

    I thread leggono soltanto; la cache si aggiorna qui, in un solo thread."""
    paths = list(dict.fromkeys(paths))
    styles, done = {}, 0
    with ThreadPoolExecutor(max_workers=io_workers()) as pool:
        futures = {pool.submit(read_entry, p, cache.get(p)): p for p in paths}
        for future in as_completed(futures):
            path = futures[future]
            entry = future.result()
            if entry is not None:
                cache[path] = entry
            styles[path] = style_from(entry)
            done += 1
            if progress and done % 20 == 0:
                progress(done, len(paths))
    return styles


def profile_label(pid, dcps):
    if not pid or pid == DEFAULT_PROFILE:
        return "Color"
    if pid in dcps:
        return dcps[pid].get("name") or pid
    return pid.split(".", 1)[-1].replace("-", " ").replace(".", " › ").title() if pid.startswith("lc.") else pid


def build_plan(lib, scope, settings, finder, progress=None, ids=None, locked=None):
    """Foto delle fotocamere supportate e cosa succederà a ciascuna.

    scope: "all" (tutta la libreria), "last" (l'ultima importazione), "since:<ora>" (dall'importazione che inizia a
    quell'ora in poi), "recent" (come "Recently Added" di LightCraft: 30 giorni fino all'ultima importazione).
    ids: solo queste foto (l'importazione automatica le passa appena importate).
    Nel risultato "batches" sono le importazioni della libreria (per il menu della finestra)."""
    cat = read_catalog(lib)
    photos = [p for p in cat["photos"].values() if not p.get("deleted")]
    batches = import_batches(photos)
    if ids is not None:
        wanted = {int(i) for i in ids}
        photos = [p for p in photos if int(p["id"]) in wanted]
    elif scope == "recent":
        since = recent_from(photos)
        photos = [p for p in photos if (p.get("imported") or "") >= since]
    elif scope == "last" or str(scope).startswith("since:"):
        since = (batches[0]["start"] if batches else "") if scope == "last" else scope[6:]
        photos = [p for p in photos if (p.get("imported") or "")[:19] >= since]
    dcps = imported_profiles(lib)
    cache = load_cache()
    choices = (settings.get("choices") or {}).get(lib, {})
    items, unsupported = [], 0
    supported = [p for p in photos if cs.is_supported((p.get("source") or {}).get("path"), (p.get("meta") or {}).get("make"))]
    unsupported = len(photos) - len(supported)
    styles = read_styles([p["source"]["path"] for p in supported], cache, progress)
    for p in sorted(supported, key=lambda p: p.get("captured") or ""):
        meta = p.get("meta") or {}
        path = p["source"]["path"]
        current = ((p.get("develop") or {}).get("profile") or {}).get("id") or DEFAULT_PROFILE
        item = {"id": int(p["id"]), "name": p.get("file_name") or os.path.basename(path), "path": path,
                "camera": camera_name(meta), "imported": p.get("imported") or "", "captured": p.get("captured") or "",
                "size_px": (p.get("width"), p.get("height")), "current": current,
                "current_name": profile_label(current, dcps), "model": "", "style_key": None, "style_code": None,
                "style_name": None, "target": None, "target_path": None}
        style = styles[path]
        if style is None:
            item["status"] = "missing"
        else:
            item.update(model=style["model"] or item["camera"], style_key=style["key"], style_code=style["code"],
                        style_name=style["name"])
            if not style["key"]:
                item["status"] = "nostyle"
            else:
                item["target"], item["target_path"] = target_profile(settings, finder, item["model"], style)
                item["status"] = status_of(item, dcps, finder)
        if locked and item["id"] in locked:
            item["status"] = "phone"  # preset scelto sul telefono: il profilo automatico non lo sovrascrive
        item["action"] = "apply" if item["status"] == "apply" else "skip"
        saved = choices.get(str(item["id"]))
        if saved and item["status"] in ("apply", "manual"):
            item["action"] = saved
        items.append(item)
    save_cache(cache)
    return {"items": items, "unsupported": unsupported, "in_scope": len(photos), "batches": batches}


def status_of(item, dcps, finder):
    if not item["target"]:
        return "nomatch"
    current = dcps.get(item["current"])
    same_camera = current and cs.norm(current.get("cameraModel")) == cs.norm(item["model"])
    if same_camera and current.get("name") == item["target"]:
        return "ok"
    # il profilo predefinito, o un altro profilo fotocamera della stessa macchina (dato da noi), si può sostituire
    if item["current"] == DEFAULT_PROFILE or (same_camera and current.get("name") in finder.profiles_for(item["model"])):
        return "apply"
    return "manual"


def _safe_name(text):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .") or "profile"


def import_profiles(lib, cli, items):
    """Importa nella libreria i profili che servono: {(modello, nome del profilo): id in LightCraft}, errori.

    I .dcp si importano così come sono (LightCraft restituisce l'id già esistente se c'è). I Look diventano LUT .cube
    e le LUT misurate si copiano, in un gruppo col nome della fotocamera; quelle già importate si riusano, perché
    LightCraft importerebbe una copia."""
    needed = {(i["model"], i["target"]): i["target_path"] for i in items}
    ids, failed = {}, []
    dcp_paths = sorted({path for path in needed.values() if not cs.is_lut(path)})
    if dcp_paths:
        result = run_cli(cli, lib, [("profile.import", {"paths": dcp_paths})])[0]["result"]
        ids.update({(cs.norm(x.get("cameraModel")), x.get("name")): x["id"]
                    for x in result.get("imported", []) if x.get("cameraModel")})
        failed += [f"{a}: {b}" for a, b in result.get("failed", [])]
    existing = {(cs.norm(p.get("cameraModel")), p.get("name")): pid
                for pid, p in imported_profiles(lib).items() if pid.startswith("lut:")}
    new = {}
    for (model, name), path in needed.items():
        if not cs.is_lut(path):
            continue
        key = (cs.norm(model), name)
        if key in existing:
            ids[key] = existing[key]
        else:
            new.setdefault(model, []).append((name, path))
    if new:
        tmp = os.path.join(DATA_DIR, "looks", time.strftime("%Y%m%d-%H%M%S"))
        try:
            for model, entries in new.items():
                folder = os.path.join(tmp, _safe_name(model))  # il nome della cartella diventa il gruppo in LightCraft
                for name, path in entries:
                    try:
                        out = os.path.join(folder, _safe_name(name) + ".cube")
                        if cs.is_look(path):
                            looks.to_cube(path, out, title=name)
                        else:  # LUT misurata: il titolo dentro il file dà il nome in LightCraft
                            os.makedirs(folder, exist_ok=True)
                            shutil.copyfile(path, out)
                    except (OSError, ValueError, Exception) as e:  # noqa: BLE001 — tabella non leggibile
                        failed.append(f"{name}: {e}")
                if os.path.isdir(folder):
                    result = run_cli(cli, lib, [("profile.import", {"paths": [folder]})])[0]["result"]
                    for x in result.get("imported", []):
                        ids[(cs.norm(model), x.get("name"))] = x["id"]
                    failed += [f"{a}: {b}" for a, b in result.get("failed", [])]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return ids, failed


def apply_profiles(lib, cli, items, root, kept, progress=None, backup=None, chunk=None):
    """Importa nella libreria i profili che servono e li assegna alle foto; restituisce il riepilogo.

    backup: il backup già fatto da chi chiama (l'importazione automatica), per non farne un secondo."""
    reason = library_open_reason(lib)
    if reason:
        raise RuntimeError(reason)
    backup = backup or backup_library(lib, root, kept)
    log(f"--- profiles for {len(items)} photos in {lib} (backup: {backup})")
    ids, failed = import_profiles(lib, cli, items)
    todo = []
    for item in items:
        pid = ids.get((cs.norm(item["model"]), item["target"]))
        if pid:
            todo.append((item, pid))
        else:
            failed.append(f"{item['name']}: {item['target']} not imported")
    view = os.path.join(lib, "view.json")
    try:
        with open(view, "rb") as f:
            saved_view = f.read()
    except OSError:
        saved_view = None
    try:
        size = chunk or CHUNK
        for start in range(0, len(todo), size):
            block = todo[start:start + size]
            # develop.profile agisce sulla foto attiva: ogni foto viene selezionata e poi riceve il suo profilo
            cmds = [("library.source", {"kind": "all"})]
            for item, pid in block:
                cmds += [("library.select", {"ids": [item["id"]], "active": item["id"]}),
                         ("develop.profile", {"id": pid, "amount": 100})]
            replies = run_cli(cli, lib, cmds, check=False)
            failed += [f"{x.get('command')}: {x.get('error')}" for x in replies if not x.get("ok")]
            if progress:
                progress(min(start + size, len(todo)), len(todo))
    finally:
        if saved_view is not None:  # all'apertura LightCraft ritrova la vista com'era
            with open(view, "wb") as f:
                f.write(saved_view)
    # controllo finale sul catalogo salvato
    photos = read_catalog(lib)["photos"]
    done, wrong = [], []
    for item, pid in todo:
        p = photos.get(str(item["id"])) or {}
        got = ((p.get("develop") or {}).get("profile") or {}).get("id")
        (done if got == pid else wrong).append(item)
    for item in wrong:
        failed.append(f"{item['name']}: profile not set")
    for item in done:
        log(f"{item['path']}: {item['style_key']} -> {item['target']}")
    log(f"profiles set: {len(done)}, errors: {len(failed)}")
    return {"done": len(done), "failed": failed, "backup": backup, "done_ids": [i["id"] for i in done]}
