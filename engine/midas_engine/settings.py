"""User settings: a pydantic model persisted as JSON (portable, human-editable)."""
import json
import os
import threading
from typing import Optional

from pydantic import BaseModel, Field

from . import config


class Settings(BaseModel):
    # Formats & quality
    video_format: str = "mp4"            # mp4 | mkv | webm
    audio_format: str = "mp3"            # mp3 | m4a | flac | opus
    audio_bitrate: int = 192             # kbps (ignored for flac)
    quality: str = "best"                # best | 2160 | 1440 | 1080 | 720 | audio

    # Output
    output_dir: str = Field(default_factory=lambda: str(config.default_output_dir()))
    per_platform_subfolders: bool = True
    filename_template: str = "%(title)s [%(id)s].%(ext)s"
    # UI-only state for the interactive filename builder (TASK: option-based
    # template builder). JSON blob owned by the Flutter app; the engine keeps
    # using filename_template, which the builder writes in lock-step.
    filename_components: str = ""

    # Queue behaviour
    max_concurrent: int = 3
    speed_limit_kbps: Optional[int] = None   # None = unlimited
    retries: int = 3

    # Embedding & extras
    embed_thumbnail: bool = True
    save_thumbnail_file: bool = True
    embed_chapters: bool = True
    embed_metadata: bool = True
    embed_subtitles: bool = False
    clipboard_watch: bool = True

    # Restricted content
    cookies_from_browser: str = ""       # "", "chrome", "edge", "firefox", ...

    # UI
    language: str = "en"                 # i18n scaffold; only "en" ships today
    show_logs: bool = False              # developer log panel on the Home screen


_lock = threading.Lock()
_cached: Optional[Settings] = None


def load() -> Settings:
    global _cached
    with _lock:
        if _cached is None:
            if config.SETTINGS_FILE.exists():
                try:
                    _cached = Settings.model_validate_json(
                        config.SETTINGS_FILE.read_text(encoding="utf-8"))
                except Exception:
                    _cached = Settings()  # corrupt file -> defaults, never crash
            else:
                _cached = Settings()
        return _cached


def save(new: Settings) -> Settings:
    global _cached
    with _lock:
        _cached = new
        # Atomic write: a crash mid-save must never corrupt settings.json.
        tmp = config.SETTINGS_FILE.with_name(config.SETTINGS_FILE.name + ".tmp")
        tmp.write_text(
            json.dumps(new.model_dump(), indent=2), encoding="utf-8")
        os.replace(tmp, config.SETTINGS_FILE)
        return new
