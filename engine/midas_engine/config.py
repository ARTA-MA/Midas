r"""Path layout.

Binaries stay portable next to the executable; user data (settings,
history, logs, cookies.txt) lives in the per-user application-data folder
so it survives moving, updating, or reinstalling the app:

  Windows  %APPDATA%\Midas
  macOS    ~/Library/Application Support/Midas
  Linux    $XDG_DATA_HOME/midas  (or ~/.local/share/midas)

Set the MIDAS_DATA_DIR environment variable to override the location
(e.g. for a fully portable USB-stick setup). Data from the old portable
layout (<app>/data) is migrated automatically on first start.

  Midas/
    Midas.exe            (Flutter shell)
    engine/midas-engine.exe
    vendor/              (deno.exe, yt-dlp.exe, ffmpeg.exe, ffprobe.exe)
"""
import os
import shutil
import sys
from pathlib import Path
from typing import Optional


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller
        # engine exe lives in <root>/engine/, root is one level up
        return Path(sys.executable).resolve().parent.parent
    # dev mode: repo root/engine/midas_engine/config.py -> repo root
    return Path(__file__).resolve().parent.parent.parent


def _resolve_data_dir() -> Path:
    """Per-user application-data folder (overridable for portable mode)."""
    env = os.environ.get("MIDAS_DATA_DIR")
    if env:
        return Path(env)
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Midas"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Midas"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "midas"


BASE_DIR: Path = _base_dir()
VENDOR_DIR: Path = BASE_DIR / "vendor"
DATA_DIR: Path = _resolve_data_dir()
LOG_DIR: Path = DATA_DIR / "logs"
SETTINGS_FILE: Path = DATA_DIR / "settings.json"
HISTORY_DB: Path = DATA_DIR / "history.sqlite3"

for _d in (VENDOR_DIR, DATA_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _migrate_legacy_data() -> None:
    """One-time copy from the old portable <app>/data folder.

    Runs only while the new location holds no settings/history yet, and
    never blocks startup: failing to migrate just means starting fresh.
    """
    legacy = BASE_DIR / "data"
    try:
        if SETTINGS_FILE.exists() or HISTORY_DB.exists():
            return
        if not legacy.is_dir() or legacy.resolve() == DATA_DIR.resolve():
            return
        for name in ("settings.json", "history.sqlite3", "cookies.txt"):
            source = legacy / name
            if source.is_file():
                shutil.copy2(source, DATA_DIR / name)
    except Exception:
        pass


_migrate_legacy_data()

IS_WINDOWS = os.name == "nt"
EXE = ".exe" if IS_WINDOWS else ""

YTDLP_PATH: Path = VENDOR_DIR / f"yt-dlp{EXE}"
DENO_PATH: Path = VENDOR_DIR / f"deno{EXE}"
FFMPEG_PATH: Path = VENDOR_DIR / f"ffmpeg{EXE}"
FFPROBE_PATH: Path = VENDOR_DIR / f"ffprobe{EXE}"


def default_output_dir() -> Path:
    return Path.home() / "Downloads" / "Midas"


def resolve_tool(name: str) -> Optional[Path]:
    """Locate a tool: the portable vendor copy first, then the system PATH."""
    vendored = VENDOR_DIR / f"{name}{EXE}"
    if vendored.exists():
        return vendored
    found = shutil.which(name)
    return Path(found) if found else None
