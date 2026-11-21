"""Breachcut patch updater.

Checks GitHub for a newer version.json and downloads the files it lists.
Your settings, processed list and thumbnails are never touched.
"""
import json
import os
import ssl
import sys
import urllib.request

REPO = "Fedr7/fed-clipper"
BRANCH = "main"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(APP_DIR, "version.txt")
KEEP = {"settings.json", "processed.json", "edits.json"}


def local_version():
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _vtuple(v):
    out = []
    for p in str(v).strip().lstrip("vV").split("."):
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out + [0] * (3 - len(out)))


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Breachcut", "Cache-Control": "no-cache"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def check():
    """Return the remote manifest dict if it is newer than this install, else None."""
    data = json.loads(_get(RAW + "version.json").decode("utf-8"))
    if _vtuple(data.get("version", "0")) > _vtuple(local_version()):
        return data
    return None


def apply(manifest, log=print):
    """Download every file in the manifest, then swap them in. Raises on failure (nothing replaced)."""
    files = manifest.get("files") or []
    staged = []
    for rel in files:
        rel = rel.replace("\\", "/").lstrip("/")
        if ".." in rel.split("/") or os.path.basename(rel) in KEEP:
            continue
        log(f"Downloading {rel}…")
        body = _get(RAW + "app/" + rel, timeout=60)
        dest = os.path.join(APP_DIR, *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".new"
        with open(tmp, "wb") as f:
            f.write(body)
        staged.append((tmp, dest))
    for tmp, dest in staged:
        os.replace(tmp, dest)
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(str(manifest["version"]))
    log(f"Updated to {manifest['version']}")


def restart():
    import subprocess
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        w = exe[:-10] + "pythonw.exe"
        if os.path.exists(w):
            exe = w
    main = os.path.join(APP_DIR, "breachcut.py")
    if not os.path.exists(main):
        main = os.path.join(APP_DIR, "fed_clipper.py")
    subprocess.Popen([exe, main], cwd=APP_DIR)
