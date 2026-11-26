"""Motore IA facoltativo per la ricerca per soggetti di ViewerCraftStar ("gatti", "auto d'epoca", "tramonto al mare").

PresetCraftAppLicator non contiene l'IA: su richiesta (installazione guidata o impostazioni) installa in una cartella
scelta dall'utente un ambiente Python separato con PyTorch e il modello (MetaCLIP 2), poi avvia lì ia_worker.py, che
calcola le impronte delle foto e le scrive nella cartella del telefono sul NAS. Questo modulo usa solo la libreria
standard: l'app resta piccola.

Impostazioni (settings.json → "ai"): {"enabled": bool, "dir": cartella del motore, "model": id del modello,
"phone_bits": 8 o 4, "libraries": [librerie da analizzare, nell'ordine della coda], "paused": bool,
"gpu_check": bool (fermarsi se un altro programma usa la GPU), "window": {"enabled", "from", "to"} (fascia oraria per
le analisi grandi)}. Una libreria entra in "libraries" solo quando l'utente chiede di analizzarla: la prima analisi la
decide lui; dopo, le foto nuove si analizzano da sole a ogni aggiornamento della libreria.
Cartella del motore: uv\\ (strumento che crea l'ambiente), python\\ (Python 3.13), venv\\ (PyTorch e altri),
modelli\\<modello>\\ (modello scaricato, file normali; le installazioni 1.16.0 lo hanno nella cache di Hugging
Face in modelli\\hub), cache\\ (miniature), parole\\, motore.json (installazione completata), installazione.json
(avanzamento dell'installazione), stato.json (avanzamento del lavoro), ia.log, ia_worker.py, config.json.
"""

import ctypes
import datetime
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile

from lightcraft import DATA_DIR

ENGINE_VERSION = 1
DEFAULT_DIR = os.path.join(DATA_DIR, "IA")
UV_VERSION = "0.13.9"
UV_URL = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip"
PYTHON_VERSION = "3.13"
TORCH = ["torch==2.14.1", "torchvision==0.29.1"]
TORCH_INDEX = {"cuda": "https://download.pytorch.org/whl/cu126", "cpu": "https://download.pytorch.org/whl/cpu"}
PACKAGES = ["transformers==5.23.0", "tokenizers==0.23.3", "huggingface_hub==2.4.0", "safetensors==0.8.0",
            "onnx==1.23.2", "onnxscript==0.7.2", "onnxruntime==1.32.0", "numpy==2.5.4", "pillow==13.0.0",
            "rawpy==0.28.0", "sentencepiece", "protobuf"]
WORDS_URL = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/{lang}/{lang}_50k.txt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BELOW_NORMAL = 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS: il PC resta reattivo mentre il motore lavora

# modelli: id Hugging Face, download (GB), memoria della GPU consigliata (GB, 0 = va anche senza GPU)
MODELS = {
    "metaclip2-giant": {"hf": "facebook/metaclip-2-worldwide-giant-378", "download": 14.6, "vram": 16},
    "metaclip2-huge": {"hf": "facebook/metaclip-2-worldwide-huge-378", "download": 7.5, "vram": 8},
    "metaclip2-b16": {"hf": "facebook/metaclip-2-worldwide-b16", "download": 2.4, "vram": 0},
}
# modelli delle descrizioni (facoltative): id Hugging Face, download (GB), memoria video minima (GB)
DESCRIBERS = {
    # il primo che sta nella GPU è quello consigliato. Qwen3.5-9B: confrontato con Qwen3-VL-8B e Qwen3.8-27B (4 bit) su 30
    # foto il 26/11/2026: descrizioni più ricche (40 parole e 11 parole chiave contro 30 e 10), stessa velocità (1 s a
    # foto su una RTX 3090); il 27B, che in 24 GB entra solo a 4 bit, era 4 volte più lento e non migliore
    "qwen3.5-9b": {"hf": "Qwen/Qwen3.5-9B", "download": 19.3, "vram": 22},
    "qwen3-vl-8b": {"hf": "Qwen/Qwen3-VL-8B-Instruct", "download": 17.5, "vram": 20},
    "qwen3-vl-4b": {"hf": "Qwen/Qwen3-VL-4B-Instruct", "download": 8.9, "vram": 12},
}
ENV_GB = {"cuda": 6.5, "cpu": 1.5}  # ambiente Python installato
CACHE_GB = 1.0  # miniature: circa 40 KB a foto (1 GB ogni ~25.000 foto)
# velocità misurate (RTX 3090): foto al secondo per l'analisi; anteprime lette dal NAS circa 25 al secondo
SPEED = {"cuda": {"metaclip2-giant": 19, "metaclip2-huge": 40, "metaclip2-b16": 150},
         "cpu": {"metaclip2-giant": 0.7, "metaclip2-huge": 1.5, "metaclip2-b16": 6}}
PREVIEW_SPEED = 25


# ---------------------------------------------------------------- impostazioni e hardware

def config(settings):
    return settings.get("ai") or {}


def engine_dir(settings):
    return config(settings).get("dir") or DEFAULT_DIR


def detect_gpu():
    """(nome, memoria in MB) della GPU NVIDIA più grande, o None. Senza GPU NVIDIA il motore usa la CPU."""
    exe = shutil.which("nvidia-smi") or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                                                      "nvidia-smi.exe")
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    best = None
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[1].isdigit():
            if best is None or int(parts[1]) > best[1]:
                best = (parts[0], int(parts[1]))
    return best


def recommended_model(gpu):
    vram = gpu[1] / 1024 if gpu else 0
    for name, info in MODELS.items():  # dal più grande al più piccolo
        if vram >= info["vram"] * 0.95:
            return name
    return "metaclip2-b16"


def recommended_describer(gpu):
    """Il modello delle descrizioni adatto alla GPU, o None (senza una GPU NVIDIA abbastanza grande sarebbe troppo lento)."""
    vram = gpu[1] / 1024 if gpu else 0
    for name, info in DESCRIBERS.items():
        if vram >= info["vram"] * 0.95:
            return name
    return None


def device(gpu):
    return "cuda" if gpu else "cpu"


def space_needed_gb(model, gpu, describer=None):
    return ENV_GB[device(gpu)] + MODELS[model]["download"] + CACHE_GB + \
        (DESCRIBERS[describer]["download"] if describer in DESCRIBERS else 0)


def free_space_gb(path):
    """Spazio libero sul disco della cartella (anche se la cartella non esiste ancora)."""
    p = os.path.abspath(path)
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    try:
        return shutil.disk_usage(p).free / 1024 ** 3
    except OSError:
        return 0.0


def folder_size_gb(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1024 ** 3


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def installed_info(folder):
    """Il contenuto di motore.json se il motore è installato e completo in `folder`, altrimenti None."""
    info = load_json(os.path.join(folder, "motore.json"), None)
    if not info or info.get("version") != ENGINE_VERSION:
        return None
    if not os.path.isfile(venv_python(folder)):
        return None
    return info


def model_dir(folder, model):
    """Cartella del modello scaricato (dalla 1.16.1): file normali, niente cache di Hugging Face."""
    return os.path.join(folder, "modelli", model)


def remove_old_cache(folder):
    """Le installazioni 1.16.0 tenevano i modelli nella cache di Hugging Face (modelli\\hub); dalla 1.16.1 stanno in
    cartelle normali. Quando ogni modello della cache vecchia ha anche la sua cartella nuova completa, la cache è solo
    spazio doppio (un modello giant sono 14 GB) e si toglie. I file dei modelli nella cache sono in comune (blobs), quindi
    si toglie tutta o niente. Ritorna True se l'ha tolta."""
    hub = os.path.join(folder, "modelli", "hub")
    if not os.path.isdir(hub):
        return False
    per_hf = {v["hf"]: k for k, v in list(MODELS.items()) + list(DESCRIBERS.items())}
    for name in os.listdir(hub):
        if not name.startswith("models--"):
            continue
        key = per_hf.get(name[len("models--"):].replace("--", "/", 1))
        if not key or not os.path.isfile(os.path.join(model_dir(folder, key), "config.json")):
            return False  # un modello che c'è solo nella cache vecchia: la si lascia
    shutil.rmtree(hub, ignore_errors=True)
    shutil.rmtree(os.path.join(folder, "modelli", "xet"), ignore_errors=True)
    return not os.path.exists(hub)


def venv_python(folder):
    return os.path.join(folder, "venv", "Scripts", "python.exe")


def env(folder, offline=True):
    """Variabili d'ambiente del motore: modelli, cache e Python restano nella sua cartella."""
    e = dict(os.environ)
    e.update(HF_HOME=os.path.join(folder, "modelli"), UV_CACHE_DIR=os.path.join(folder, "uv-cache"),
             UV_PYTHON_INSTALL_DIR=os.path.join(folder, "python"), PYTHONIOENCODING="utf-8",
             HF_HUB_DISABLE_TELEMETRY="1", PYTHONNOUSERSITE="1")
    if offline:
        e["HF_HUB_OFFLINE"] = "1"
    for k in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        e.pop(k, None)
    return e


# ---------------------------------------------------------------- installazione

class InstallCancelled(Exception):
    pass


class Installer:
    """Installa il motore in `folder` (riprendibile: i passi già fatti si saltano). L'avanzamento va in
    installazione.json, che finestra e installazione guidata leggono; `cancel` (threading.Event) lo interrompe."""

    STEPS = ("uv", "python", "torch", "packages", "words", "model", "describer")

    def __init__(self, folder, model, gpu, cancel=None, describer=None):
        self.folder, self.model, self.gpu = folder, model, gpu
        self.describer = describer if describer in DESCRIBERS else None
        self.cancel = cancel or threading.Event()
        self.progress_path = os.path.join(folder, "installazione.json")
        self.log_path = os.path.join(folder, "installazione.log")

    def report(self, step, fraction=0.0, error=None, done=False, **extra):
        i = self.STEPS.index(step) if step in self.STEPS else len(self.STEPS)
        save_json(self.progress_path, {"pid": os.getpid(), "step": step, "index": i, "steps": len(self.STEPS),
                                       "fraction": fraction, "total": min(1.0, (i + fraction) / len(self.STEPS)),
                                       "model": self.model, "error": error or "", "done": done,
                                       "updated": datetime.datetime.now().isoformat(timespec="seconds"), **extra})

    def log(self, text):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")

    def run_cmd(self, cmd, step, offline=False, extra_env=None, tick=None):
        """Esegue un comando del motore; `tick` (se c'è) viene chiamata ogni ~2 secondi per l'avanzamento."""
        self.log("> " + " ".join(cmd))
        e = env(self.folder, offline)
        e.update(extra_env or {})
        with open(self.log_path, "a", encoding="utf-8") as log:
            p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=e, creationflags=NO_WINDOW)
            n = 0
            while p.poll() is None:
                if self.cancel.wait(1):
                    p.kill()
                    raise InstallCancelled()
                n += 1
                if tick and n % 2 == 0:
                    tick()
        if p.returncode != 0:
            raise RuntimeError(f"{step}: exit code {p.returncode} (see {self.log_path})")

    def download(self, url, dest, step):
        self.log(f"download {url}")
        tmp = dest + ".part"
        req = urllib.request.Request(url, headers={"User-Agent": "PresetCraftAppLicator"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                if self.cancel.is_set():
                    raise InstallCancelled()
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    self.report(step, got / total)
        os.replace(tmp, dest)

    def run(self):
        os.makedirs(self.folder, exist_ok=True)
        self.log(f"install model={self.model} gpu={self.gpu} folder={self.folder}")
        try:
            uv = os.path.join(self.folder, "uv", "uv.exe")
            self.report("uv")
            if not os.path.isfile(uv):
                z = os.path.join(self.folder, "uv.zip")
                self.download(UV_URL, z, "uv")
                with zipfile.ZipFile(z) as zf:
                    for name in zf.namelist():
                        if os.path.basename(name) in ("uv.exe", "uvx.exe"):
                            os.makedirs(os.path.dirname(uv), exist_ok=True)
                            with zf.open(name) as src, open(os.path.join(os.path.dirname(uv),
                                                                         os.path.basename(name)), "wb") as dst:
                                shutil.copyfileobj(src, dst)
                os.remove(z)
            py = venv_python(self.folder)
            self.report("python")
            if not os.path.isfile(py):
                self.run_cmd([uv, "venv", "--python", PYTHON_VERSION, "--managed-python",
                              os.path.join(self.folder, "venv")], "python")
            self.report("torch")
            self.run_cmd([uv, "pip", "install", "--python", py, *TORCH, "--index-url", TORCH_INDEX[device(self.gpu)]],
                         "torch")
            self.report("packages")
            self.run_cmd([uv, "pip", "install", "--python", py, *PACKAGES], "packages")
            self.report("words")
            for lang in ("it", "en"):
                dest = os.path.join(self.folder, "parole", f"{lang}_50k.txt")
                if not os.path.isfile(dest):
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    self.download(WORDS_URL.format(lang=lang), dest, "words")
            self.report("model")
            hf = MODELS[self.model]["hf"]
            target = model_dir(self.folder, self.model)
            expected = MODELS[self.model]["download"] * 1e9

            def tick():  # avanzamento del modello: quanto c'è già su disco rispetto alla dimensione attesa
                got = folder_size_gb(target) * 1024 ** 3
                self.report("model", min(0.99, got / expected), got_gb=round(got / 1e9, 1),
                            total_gb=MODELS[self.model]["download"])

            # in una cartella normale (la cache di Hugging Face su Windows copierebbe i file: spazio doppio) e senza
            # Xet, che scrive i file solo alla fine: così la dimensione cresce man mano e l'avanzamento si vede
            self.run_cmd([py, "-c", "from huggingface_hub import snapshot_download as s; "
                                    f"s('{hf}', local_dir=r'{target}', "
                                    "allow_patterns=['*.json', '*.safetensors', '*.model', '*.txt'])"],
                         "model", extra_env={"HF_HUB_DISABLE_XET": "1"}, tick=tick)
            shutil.rmtree(os.path.join(target, ".cache"), ignore_errors=True)  # file temporanei del download
            if self.describer:
                self.report("describer")
                dtarget = model_dir(self.folder, self.describer)
                dexpected = DESCRIBERS[self.describer]["download"] * 1e9

                def dtick():
                    got = folder_size_gb(dtarget) * 1024 ** 3
                    self.report("describer", min(0.99, got / dexpected), got_gb=round(got / 1e9, 1),
                                total_gb=DESCRIBERS[self.describer]["download"])

                dhf = DESCRIBERS[self.describer]["hf"]
                self.run_cmd([py, "-c", "from huggingface_hub import snapshot_download as s; "
                                        f"s('{dhf}', local_dir=r'{dtarget}', "
                                        "allow_patterns=['*.json', '*.safetensors', '*.txt', '*.jinja'])"],
                             "describer", extra_env={"HF_HUB_DISABLE_XET": "1"}, tick=dtick)
                shutil.rmtree(os.path.join(dtarget, ".cache"), ignore_errors=True)
            shutil.rmtree(os.path.join(self.folder, "uv-cache"), ignore_errors=True)  # solo spazio occupato
            if remove_old_cache(self.folder):
                self.log("removed the old model cache (modelli\\hub): the models are now in their own folders")
            prima = load_json(os.path.join(self.folder, "motore.json"), {})
            modelli = sorted(set(prima.get("models") or []) | {self.model})
            descrittori = sorted(set(prima.get("describers") or []) | ({self.describer} if self.describer else set()))
            save_json(os.path.join(self.folder, "motore.json"),
                      {"version": ENGINE_VERSION, "model": self.model, "models": modelli, "describers": descrittori,
                       "device": device(self.gpu),
                       "gpu": self.gpu[0] if self.gpu else "", "installed":
                           datetime.datetime.now().isoformat(timespec="seconds"),
                       "packages": TORCH + PACKAGES})
            self.report("describer" if self.describer else "model", 1.0, done=True)
            self.log("install done")
            return True
        except InstallCancelled:
            self.report("cancelled", error="cancelled")
            self.log("install cancelled")
            return False
        except Exception as e:  # noqa: BLE001
            self.report("error", error=str(e))
            self.log(f"install ERROR {e!r}")
            return False


def install_progress(folder):
    return load_json(os.path.join(folder, "installazione.json"), {})


def installing(folder):
    p = install_progress(folder)
    return bool(p) and not p.get("done") and not p.get("error") and pid_alive(p.get("pid"))


def start_install_process(app_exe, folder, model, describer=None):
    """Installazione in un processo a sé (l'app può chiudersi nel frattempo):
    <app> --install-ai <cartella> <modello> [<modello delle descrizioni>]."""
    extra = [describer] if describer else []
    args = [app_exe, "--install-ai", folder, model, *extra] if getattr(sys, "frozen", False) else \
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "presetcraft.py"),
         "--install-ai", folder, model, *extra]
    os.makedirs(folder, exist_ok=True)
    return subprocess.Popen(args, creationflags=NO_WINDOW | BELOW_NORMAL | 0x8, close_fds=True)  # 0x8 DETACHED


def install_main(folder, model, describer=None):
    """Punto d'ingresso di --install-ai (nessuna finestra)."""
    return 0 if Installer(folder, model, detect_gpu(), describer=describer).run() else 1


def descriptions_model(path):
    """Il modello che ha scritto il file delle descrizioni (None se non si legge)."""
    import gzip
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f).get("model")
    except (OSError, ValueError, EOFError):
        return None


def describer_ready(settings):
    """Il modello delle descrizioni scelto, se attivo e installato; altrimenti None."""
    cfg = config(settings)
    name = cfg.get("describer")
    info = installed_info(engine_dir(settings)) or {}
    if cfg.get("descriptions") and name in (info.get("describers") or []):
        return name
    return None


def remove_engine(folder):
    """Toglie la cartella del motore (programmi, modelli, miniature). Le foto e le librerie non c'entrano."""
    if not folder or not os.path.isdir(folder):
        return True
    stop_worker(folder)
    shutil.rmtree(folder, ignore_errors=True)
    return not os.path.exists(folder)


def nas_outputs(settings):
    """Le cartelle <libreria>/ia scritte dal motore nella cartella di servizio (anche nel posto delle versioni prima
    della 1.19, se non ancora spostate)."""
    import watcher
    folder, libs = watcher.phone_settings(settings)
    out = []
    for root in (watcher.libraries_root(folder), folder) if folder else ():
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            p = os.path.join(root, name, "ia")
            if os.path.isfile(os.path.join(p, "manifest.json")):
                out.append(p)
    return out


def remove_nas_outputs(settings):
    for p in nas_outputs(settings):
        shutil.rmtree(p, ignore_errors=True)


# ---------------------------------------------------------------- coda delle librerie

def same(a, b):
    return os.path.normcase(os.path.normpath(a or "")) == os.path.normcase(os.path.normpath(b or ""))


def queue(settings):
    return list(config(settings).get("libraries") or [])


def in_queue(settings, lib):
    return any(same(x, lib) for x in queue(settings))


def add_to_queue(settings, lib):
    libs = queue(settings)
    if not any(same(x, lib) for x in libs):
        libs.append(lib)
    settings["ai"] = {**config(settings), "libraries": libs}


def remove_from_queue(settings, lib):
    settings["ai"] = {**config(settings), "libraries": [x for x in queue(settings) if not same(x, lib)]}


def move_in_queue(settings, lib, delta):
    libs = queue(settings)
    i = next((k for k, x in enumerate(libs) if same(x, lib)), None)
    if i is None or not 0 <= i + delta < len(libs):
        return
    libs[i], libs[i + delta] = libs[i + delta], libs[i]
    settings["ai"] = {**config(settings), "libraries": libs}


def set_paused(settings, paused):
    """Pausa (anche del lavoro in corso: il lavoratore la vede dal file "pausa" e si ferma salvando) o ripresa."""
    settings["ai"] = {**config(settings), "paused": bool(paused)}
    flag = os.path.join(engine_dir(settings), "pausa")
    try:
        if paused:
            os.makedirs(os.path.dirname(flag), exist_ok=True)
            open(flag, "w").close()
        elif os.path.exists(flag):
            os.remove(flag)
    except OSError:
        pass


def library_photo_count(lib):
    """Quante foto analizzerebbe il motore (senza eliminate, video e copie virtuali), o None."""
    try:
        with open(os.path.join(lib, "catalog.snap"), encoding="utf-8") as f:
            photos = json.load(f)["catalog"]["photos"].values()
    except (OSError, ValueError, KeyError):
        return None
    return sum(1 for p in photos if not p.get("deleted") and p.get("kind") in ("raw", "image")
               and not p.get("copy_of"))


def estimate_minutes(settings, photos, gpu=None):
    """Minuti stimati per la prima analisi di `photos` foto (anteprime dal NAS + analisi)."""
    info = installed_info(engine_dir(settings)) or {}
    model = config(settings).get("model") or info.get("model") or "metaclip2-giant"
    dev = info.get("device") or device(gpu)
    rate = SPEED[dev].get(model, 10)
    return photos / PREVIEW_SPEED / 60 + photos / rate / 60


def library_state(settings, lib):
    """Stato della ricerca IA per una libreria: {"state": "none" | "queued" | "running" | "paused" | "waiting" |
    "done" | "partial", "count", "photos", "done", "total", "phase"}."""
    folder = engine_dir(settings)
    st = worker_status(folder)
    name = os.path.basename(os.path.normpath(lib))
    x = (st.get("libraries") or {}).get(name) or {}
    out = {"count": x.get("count", 0), "photos": x.get("photos", 0), "described": x.get("described", 0),
           "phase": st.get("phase"), "done": 0, "total": 0}
    if not in_queue(settings, lib):
        out["state"] = "none"
    elif st["running"] and st.get("library") == name:
        out.update(state="running", done=st.get("done") or 0, total=st.get("total") or 0)
    elif config(settings).get("paused"):
        out["state"] = "paused"
    elif x and x.get("count", 0) >= x.get("photos", 1) and x.get("photos"):
        out["state"] = "done"
    elif st.get("phase") in ("gpu_busy", "waiting_window"):
        out["state"] = "waiting"
    elif x:
        out["state"] = "partial"
    else:
        out["state"] = "queued"
    return out


# ---------------------------------------------------------------- lavoro

def pid_alive(pid):
    if not pid:
        return False
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def worker_status(folder):
    s = load_json(os.path.join(folder, "stato.json"), {})
    s["running"] = pid_alive(s.get("supervisor")) or pid_alive(s.get("pid"))
    return s


def worker_source():
    """ia_worker.py distribuito con l'app (accanto all'eseguibile, o nei sorgenti)."""
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else \
        os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "ia_worker.py")


def stop_worker(folder):
    s = load_json(os.path.join(folder, "stato.json"), {})
    for pid in (s.get("supervisor"), s.get("pid")):
        if pid_alive(pid):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)


def in_window(window, now=None):
    """Come in_fascia del lavoratore: True dentro la fascia oraria (anche a cavallo della mezzanotte)."""
    now = now or datetime.datetime.now()
    try:
        start = datetime.time.fromisoformat(window["from"])
        end = datetime.time.fromisoformat(window["to"])
    except (KeyError, ValueError, TypeError):
        return True
    t = now.time()
    return start <= t < end if start <= end else (t >= start or t < end)


def pending_notification(folder):
    """L'ultimo giro con foto nuove (o descrizioni) non ancora notificato: {"id", "libraries", "described"} o None."""
    last = load_json(os.path.join(folder, "stato.json"), {}).get("last_run")
    seen = load_json(os.path.join(folder, "notificato.json"), {}).get("id")
    return last if last and last.get("id") != seen else None


def mark_notified(folder, last):
    save_json(os.path.join(folder, "notificato.json"), {"id": last.get("id")})


def library_signatures(libs):
    sig = {}
    for lib in libs:
        try:
            from lightcraft import catalog_signature
            sig[lib] = catalog_signature(lib)
        except OSError:
            pass
    return sig


def maybe_start(settings, log=None):
    """Avvia il lavoro se il motore è attivo e installato, ci sono librerie per il telefono e qualcosa è cambiato
    (libreria modificata, file mancanti sul NAS, ultimo giro non finito). Ritorna True se l'ha avviato."""
    import watcher
    cfg = config(settings)
    folder = engine_dir(settings)
    phone_folder, phone_libs = watcher.phone_settings(settings)
    # solo le librerie che l'utente ha chiesto di analizzare (in ordine di coda) e che il telefono consulta
    libs = [lib for lib in queue(settings) if any(same(lib, x) for x in phone_libs)]
    if not cfg.get("enabled") or cfg.get("paused") or not libs or not phone_folder or not installed_info(folder):
        return False
    status = worker_status(folder)
    if status["running"] or installing(folder):
        return False
    if remove_old_cache(folder) and log:  # motore fermo: la cache vecchia dei modelli, se doppia, si può togliere
        log("AI engine: removed the old model cache (models already in their own folders)")
    window = cfg.get("window") or {}
    window = {"from": window.get("from") or "22:00", "to": window.get("to") or "07:00"} if window.get("enabled") \
        else None
    info = installed_info(folder)
    model = cfg.get("model") or info.get("model")
    if model not in (info.get("models") or [info.get("model")]):
        return False  # modello cambiato: si aspetta che l'installazione lo abbia scaricato
    state_path = os.path.join(folder, "avvii.json")
    last = load_json(state_path, {})
    sig = library_signatures(libs)
    if watcher.old_layout_folders(phone_folder):
        return False  # prima si spostano le cartelle delle librerie nel posto nuovo (lo fa il giro del watcher)
    missing = any(not os.path.isfile(os.path.join(watcher.library_dir(phone_folder, lib), "ia", "manifest.json"))
                  for lib in libs)
    describer = describer_ready(settings)
    if describer and not missing:  # descrizioni attivate ma non ancora fatte (o non complete) per qualche libreria
        for lib in libs:
            m = load_json(os.path.join(watcher.library_dir(phone_folder, lib), "ia", "manifest.json"), {})
            d = m.get("descriptions") or {}
            made_by = d.get("by")
            if d and not made_by:  # manifest scritto prima della 1.32.1 (poteva nominare il modello scelto): si legge il file
                made_by = descriptions_model(os.path.join(watcher.library_dir(phone_folder, lib), "ia", "descrizioni.json.gz")) or made_by
            # (le foto saltate perché fermano la GPU non contano: il motore le riprova da sé, al massimo una volta al giorno)
            if d.get("count", 0) < m.get("count", 0) or (d.get("old") or 0) > (d.get("skipped") or 0) or \
                    (d and made_by != DESCRIBERS[describer]["hf"]):
                missing = True  # (anche: descrizioni scritte da un altro modello, o rifatte solo in parte)
                break
    if not missing:  # modello del telefono ancora dentro la libreria (fino alla 1.30.1): va nella cartella condivisa
        for lib in libs:
            m = load_json(os.path.join(watcher.library_dir(phone_folder, lib), "ia", "manifest.json"), {})
            if m.get("phone") and not m["phone"].get("shared"):
                missing = True
                break
    phase = status.get("phase")
    unfinished = phase not in (None, "idle", "paused")
    if phase == "waiting_window" and window and not in_window(window):
        return False  # si riprende quando comincia la fascia oraria
    retry_wait = {"error": 1800, "gpu_busy": 600}.get(phase, 0)
    since = (datetime.datetime.now() - datetime.datetime.fromisoformat(last["when"])).total_seconds() \
        if last.get("when") else 1e9
    if last.get("sig") == sig and last.get("model") == model and last.get("describer") == describer and not missing \
            and not unfinished:
        return False
    if since < retry_wait:
        return False
    src = worker_source()
    dst = os.path.join(folder, "ia_worker.py")
    shutil.copyfile(src, dst)  # il codice del lavoratore segue la versione dell'app
    conf = os.path.join(folder, "config.json")
    save_json(conf, {"engine_dir": folder, "phone_folder": watcher.libraries_root(phone_folder), "libraries": libs,
                     "model": model,
                     "phone_bits": int(cfg.get("phone_bits") or 8), "gpu_check": bool(cfg.get("gpu_check", True)),
                     "window": window, "descriptions": describer})
    subprocess.Popen([venv_python(folder), dst, "--config", conf], cwd=folder, env=env(folder),
                     creationflags=NO_WINDOW | BELOW_NORMAL, close_fds=True)
    save_json(state_path, {"sig": sig, "model": model, "describer": describer,
                           "when": datetime.datetime.now().isoformat(timespec="seconds")})
    if log:
        log(f"ai: started ({model}, {len(libs)} libraries)")
    return True
