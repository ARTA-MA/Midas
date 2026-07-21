"""Portable dependency manager.

Downloads deno.exe, yt-dlp.exe and ffmpeg/ffprobe into <app>/vendor with
streamed progress events. No admin rights, no system-wide installs.
"""
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, Optional

import httpx

from .. import config
from ..events import bus
from . import logs

DENO_URL = ("https://github.com/denoland/deno/releases/latest/download/"
            "deno-x86_64-pc-windows-msvc.zip")
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
              "ffmpeg-master-latest-win64-gpl.zip")
YTDLP_LATEST_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"

DEPS = {
    "deno": {"path": config.DENO_PATH, "label": "Deno (JS runtime)"},
    "yt-dlp": {"path": config.YTDLP_PATH, "label": "yt-dlp (downloader)"},
    "ffmpeg": {"path": config.FFMPEG_PATH, "label": "FFmpeg (media toolkit)"},
}

_NO_WINDOW = 0x08000000 if config.IS_WINDOWS else 0

# Never hang forever on a stalled connection; `read` applies per chunk, so
# large archives still stream fine as long as data keeps flowing.
_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=120.0,
                                  pool=15.0)


def _version_of(path: Optional[Path], flag: str = "--version") -> Optional[str]:
    """Best-effort version probe.

    ffmpeg/ffprobe use a single-dash `-version` flag and print to stderr,
    and antivirus scans can stall the very first launch of a freshly
    downloaded exe - so a failed probe must NEVER mean 'not installed'.
    """
    if path is None or not Path(path).exists():
        return None
    try:
        proc = subprocess.run([str(path), flag], capture_output=True, text=True,
                              timeout=30, creationflags=_NO_WINDOW)
        out = (proc.stdout or "") + (proc.stderr or "")
        m = re.search(r"\d+[\w.\-]*", out)
        if m:
            return m.group(0)
        lines = out.strip().splitlines()
        return lines[0][:40] if lines else None
    except Exception:
        return None


def _ytdlp_latest() -> Optional[str]:
    try:
        r = httpx.get(YTDLP_LATEST_API, timeout=10,
                      headers={"Accept": "application/vnd.github+json"})
        return r.json().get("tag_name")
    except Exception:
        return None  # offline is fine; just no update hint


def status() -> Dict[str, dict]:
    result = {}
    for name, info in DEPS.items():
        # Installed = the binary exists (vendor folder first, system PATH as
        # a fallback). The version probe is informational only and must not
        # decide installed-ness.
        path = config.resolve_tool(name)
        flag = "-version" if name == "ffmpeg" else "--version"
        version = _version_of(path, flag)
        entry = {"label": info["label"], "installed": path is not None,
                 "version": version, "update_available": False}
        if name == "yt-dlp" and version:
            latest = _ytdlp_latest()
            if latest and latest != version:
                entry["update_available"] = True
                entry["latest"] = latest
        result[name] = entry
    probe = config.resolve_tool("ffprobe")
    result["ffprobe"] = {"label": "FFprobe", "installed": probe is not None,
                         "version": _version_of(probe, "-version"),
                         "update_available": False}
    return result


def _download(name: str, url: str, dest: Path) -> Path:
    """Stream a download straight to disk (no big RAM buffers)."""
    tmp = dest.parent / (dest.name + ".download")
    try:
        with httpx.stream("GET", url, follow_redirects=True,
                          timeout=_DOWNLOAD_TIMEOUT) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes(chunk_size=256 * 1024):
                    fh.write(chunk)
                    done += len(chunk)
                    bus.publish({"type": "deps.progress", "name": name,
                                 "downloaded": done, "total": total,
                                 "percent": round(done / total * 100, 1) if total else None})
    except BaseException:
        # Don't leave a partial .download file behind on failure.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return tmp


def _place(src: Path, dest: Path) -> None:
    """Atomically move a finished file into place.

    os.replace overwrites atomically on Windows; deleting dest first would
    open a window where the tool doesn't exist at all if we crash.
    """
    os.replace(src, dest)


def _extract_members(zip_path: Path, wanted: Dict[str, Path]) -> None:
    """Stream selected members out of a zip. wanted: {basename: destination}."""
    found = set()
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            base = member.rsplit("/", 1)[-1]
            dest = wanted.get(base)
            if dest is None or member.endswith("/"):
                continue
            tmp = dest.parent / (dest.name + ".new")
            with z.open(member) as src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            _place(tmp, dest)
            found.add(base)
    missing = set(wanted) - found
    if missing:
        raise RuntimeError("archive did not contain: " + ", ".join(sorted(missing)))


def install(name: str) -> dict:
    """Blocking; call from a worker thread. Publishes deps.progress/deps.state."""
    # ffprobe ships inside the ffmpeg archive; installing it from the UI's
    # FFprobe row must work instead of erroring with "unknown dependency".
    target = "ffmpeg" if name == "ffprobe" else name
    bus.publish({"type": "deps.state", "name": name, "state": "downloading"})
    logs.log(f"Installing {name}...", source="deps")
    config.VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    archive: Optional[Path] = None
    try:
        if target == "deno":
            archive = _download(name, DENO_URL, config.DENO_PATH)
            _extract_members(archive, {"deno.exe": config.DENO_PATH})
        elif target == "yt-dlp":
            tmp = _download(name, YTDLP_URL, config.YTDLP_PATH)
            _place(tmp, config.YTDLP_PATH)
        elif target == "ffmpeg":
            archive = _download(name, FFMPEG_URL, config.FFMPEG_PATH)
            _extract_members(archive, {"ffmpeg.exe": config.FFMPEG_PATH,
                                       "ffprobe.exe": config.FFPROBE_PATH})
        else:
            raise ValueError(f"Unknown dependency: {name}")
        bus.publish({"type": "deps.state", "name": name, "state": "installed"})
        logs.log(f"{name} installed successfully.", source="deps")
        return {"ok": True, "status": status()}
    except PermissionError:
        msg = (f"Could not replace {name}: the file is locked. Finish or cancel "
               "active downloads (or check your antivirus) and retry.")
        logs.log(msg, level="error", source="deps")
        bus.publish({"type": "deps.state", "name": name, "state": "error",
                     "message": msg})
        return {"ok": False, "error": msg}
    except Exception as exc:  # friendly errors, never tracebacks
        msg = f"Could not install {name}: {type(exc).__name__}: {str(exc)[:160]}"
        logs.log(msg, level="error", source="deps")
        bus.publish({"type": "deps.state", "name": name, "state": "error",
                     "message": msg})
        return {"ok": False, "error": msg}
    finally:
        if archive is not None:
            try:
                archive.unlink(missing_ok=True)
            except OSError:
                pass
