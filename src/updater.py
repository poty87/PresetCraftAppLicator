"""Controllo e download degli aggiornamenti dalle release GitHub del progetto.

Chiede a GitHub solo qual è l'ultima release pubblicata: non invia dati sull'utente o sulle foto.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request

from version import APP_NAME, REPO, VERSION, parse_version

API = f"https://api.github.com/repos/{REPO}/releases/latest"
SETUP_ASSET = re.compile(rf"^{APP_NAME}-Setup-[\d.]+\.exe$", re.I)
MSI_ASSET = re.compile(rf"^{APP_NAME}-[\d.]+\.msi$", re.I)
HEADERS = {"User-Agent": f"{APP_NAME}/{VERSION}", "Accept": "application/vnd.github+json"}


def latest_release(timeout=10):
    """Ultima release: {version, tag, notes, url, asset_name, asset_url, size, sha256}; None se non ha un installer."""
    req = urllib.request.Request(API, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    assets = data.get("assets") or []
    asset = next((a for a in assets if SETUP_ASSET.match(a.get("name", ""))), None) or \
        next((a for a in assets if MSI_ASSET.match(a.get("name", ""))), None)
    if asset is None:
        return None
    digest = asset.get("digest") or ""
    return {
        "version": ".".join(str(n) for n in parse_version(data.get("tag_name", "0"))),
        "tag": data.get("tag_name", ""),
        "notes": (data.get("body") or "").strip(),
        "url": data.get("html_url", ""),
        "asset_name": asset["name"],
        "asset_url": asset["browser_download_url"],
        "size": asset.get("size") or 0,
        "sha256": digest.split(":", 1)[1].lower() if digest.lower().startswith("sha256:") else None,
    }


def is_newer(version):
    return parse_version(version) > parse_version(VERSION)


def download(release, progress=None):
    """Scarica l'installer nella cartella temporanea e ne controlla dimensione e SHA-256 (se GitHub lo fornisce)."""
    if not release["asset_url"].startswith("https://github.com/" + REPO + "/releases/download/"):
        raise ValueError("unexpected download address")
    dest = os.path.join(tempfile.gettempdir(), release["asset_name"])
    sha = hashlib.sha256()
    done = 0
    req = urllib.request.Request(release["asset_url"], headers={"User-Agent": HEADERS["User-Agent"]})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        while chunk := r.read(256 * 1024):
            f.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, release["size"])
    if release["size"] and done != release["size"]:
        raise OSError(f"incomplete download ({done} of {release['size']} bytes)")
    if release["sha256"] and sha.hexdigest() != release["sha256"]:
        os.remove(dest)
        raise OSError("the downloaded file does not match its SHA-256")
    return dest


def launch_installer(path):
    """Avvia l'installazione guidata in modalità aggiornamento (o l'MSI, se la release ha solo quello)."""
    if path.lower().endswith(".exe"):
        subprocess.Popen([path, "--update"], close_fds=True)
    else:
        subprocess.Popen(["msiexec.exe", "/i", path], close_fds=True)
