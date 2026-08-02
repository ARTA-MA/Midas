"""Download queue manager.

Each job runs vendor/yt-dlp.exe as a subprocess with a machine-readable
progress template. Running the vendored binary (instead of importing yt_dlp)
is deliberate: it makes "update yt-dlp" a one-click file replacement and the
engine never needs re-packaging when yt-dlp changes.

The vendor dir is prepended to PATH for every job, so yt-dlp automatically
finds deno.exe (its external JS runtime for YouTube challenges) and ffmpeg.
Cancel = terminate the process; yt-dlp's .part files make retry resume
where it stopped (--continue is yt-dlp's default).
"""
import json
import math
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .. import config
from ..events import bus
from ..settings import Settings, load as load_settings
from . import chapters, history, logs, spotify
from .covers import embed_cover_audio

_NO_WINDOW = 0x08000000 if config.IS_WINDOWS else 0

PROGRESS_TMPL = (
    "download:MIDAS|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
    "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|"
    "%(info.playlist_index)s|%(info.playlist_count)s|%(info.title)s")

_DPAPI_MESSAGE = (
    "Couldn't read your browser's cookies (Windows blocked the decryption). "
    "Close the browser fully and retry, pick Firefox under Settings > "
    "Cookies from browser, or put an exported cookies.txt file in Midas's "
    "data folder.")

FRIENDLY_ERRORS = [
    ("Failed to decrypt with DPAPI", _DPAPI_MESSAGE),
    ("not a bot", "YouTube wants a sign-in check for this link. Pick your "
     "browser under Settings > Cookies from browser, then retry."),
    ("Requested format is not available",
     "This post only offers an unusual format. Try updating yt-dlp in "
     "Settings > Dependencies, then retry."),
    ("Unsupported URL", "This link isn't supported."),
    ("Video unavailable", "This video is unavailable."),
    ("Private video", "This video is private."),
    ("This post is private", "This post is private."),
    ("Sign in to confirm your age", "Age-restricted content. Enable "
     "cookies-from-browser in Settings and try again."),
    ("Sign in to confirm", "The site asked for a sign-in. Enable "
     "cookies-from-browser in Settings and try again."),
    ("HTTP Error 429", "The site is rate-limiting us. Try again in a bit."),
    ("network", "Network problem. Check your connection and retry."),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hms(sec: int) -> str:
    """Seconds -> zero-padded HH:MM:SS (yt-dlp --download-sections)."""
    sec = max(int(sec), 0)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _fmt_clock(sec: int) -> str:
    """Seconds -> human M:SS / H:MM:SS for clip suffixes in titles."""
    sec = max(int(sec), 0)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _output_template(template: str, section: Optional[Dict[str, int]]) -> str:
    """Give a clip its own filename so it never collides with the full video.

    Without this, a clip of a video that was already downloaded in full
    resolves to the exact same path, yt-dlp reports "has already been
    downloaded" and skips the job - handing the user the whole video
    instead of the range they asked for.
    """
    if not section:
        return template
    suffix = (f" [clip {_hms(section['start_sec']).replace(':', '-')}"
              f"-{_hms(section['end_sec']).replace(':', '-')}]")
    marker = ".%(ext)s"
    if template.endswith(marker):
        return template[:-len(marker)] + suffix + marker
    return template + suffix


def _parse_section(section: Optional[Dict[str, Any]]
                   ) -> Optional[Dict[str, int]]:
    """Normalize a {start_sec, end_sec} clip range from the request."""
    if not section:
        return None
    try:
        start = int(section.get("start_sec", 0))
        end = int(section.get("end_sec", 0))
    except (TypeError, ValueError, AttributeError):
        return None
    if start < 0 or end <= start:
        raise ValueError("The clip range needs a start before its end.")
    return {"start_sec": start, "end_sec": end}


def _is_cookie_decrypt_error(blob: str) -> bool:
    low = (blob or "").lower()
    return "dpapi" in low or "failed to decrypt" in low


def _cookie_args(s: Settings) -> List[str]:
    """Cookie flags for yt-dlp.

    An exported data\\cookies.txt always wins: it bypasses browser cookie
    decryption entirely, which is the reliable escape hatch when Chrome/Edge
    block DPAPI decryption of their cookie store (yt-dlp issue #10927).
    """
    cookie_file = config.DATA_DIR / "cookies.txt"
    if cookie_file.exists():
        return ["--cookies", str(cookie_file)]
    if s.cookies_from_browser:
        return ["--cookies-from-browser", s.cookies_from_browser]
    return []


def _kill_proc_tree(proc: Optional[subprocess.Popen]) -> None:
    """Terminate a job and (on Windows) its whole child tree, so a cancelled
    yt-dlp never leaves an orphan ffmpeg.exe behind."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if config.IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15,
                           creationflags=_NO_WINDOW)
        else:
            proc.terminate()
    except Exception:
        pass
    if proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass


def _norm_title(text: Optional[str]) -> str:
    """Loose title key: case/punctuation/spacing differences can't break the
    match between yt-dlp's progress title and the analyzer's entry title."""
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _art_map_from_preview(preview: Dict[str, Any]) -> Dict[str, str]:
    """title/index -> that track's OWN artwork, from the analyzer's entries.

    This is what lets the queue card show the cover of the track that is
    downloading instead of the playlist's icon (BUG 7). Keyed by both a
    normalised title and "#<playlist index>" so a match is found whether or
    not yt-dlp reports an index for the current item.
    """
    art: Dict[str, str] = {}
    for entry in (preview.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        thumb = entry.get("thumbnail")
        if not isinstance(thumb, str) or not thumb:
            continue
        index = entry.get("index")
        if isinstance(index, int):
            art["#" + str(index)] = thumb
        key = _norm_title(entry.get("title"))
        if key:
            art.setdefault(key, thumb)
    return art


def _art_for(item: "DownloadItem", title: Optional[str],
             index: Optional[int]) -> Optional[str]:
    """The current track's own cover, by title first then playlist index."""
    art = item.art_map
    if not art:
        return None
    key = _norm_title(title)
    if key and art.get(key):
        return art[key]
    if isinstance(index, int):
        return art.get("#" + str(index))
    return None


@dataclass
class DownloadItem:
    url: str
    platform: str
    title: str = ""
    thumbnail: Optional[str] = None
    kind: str = "single"                 # single | playlist
    audio_only: bool = False
    meta: Optional[Dict[str, Any]] = None   # spotify track metadata
    overrides: Optional[Dict[str, Any]] = None  # per-download quality/format
    playlist_items: Optional[str] = None    # yt-dlp --playlist-items value
    section: Optional[Dict[str, int]] = None   # {start_sec, end_sec} clip
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "queued"               # queued|downloading|processing|paused|completed|error|cancelled
    percent: float = 0.0
    speed: Optional[float] = None        # bytes/s
    eta: Optional[int] = None            # seconds
    downloaded: int = 0
    total: int = 0
    item_index: Optional[int] = None
    item_count: Optional[int] = None
    file_path: Optional[str] = None
    error: Optional[str] = None
    created_at: str = field(default_factory=_now)
    completed_at: Optional[str] = None
    # index/title -> per-track artwork for playlist jobs (BUG 7). Runtime
    # only: it is rebuilt from the preview, never persisted to history.
    art_map: Optional[Dict[str, str]] = field(default=None, repr=False)
    proc: Optional[subprocess.Popen] = field(default=None, repr=False)
    cancelled: bool = field(default=False, repr=False)
    paused: bool = field(default=False, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in (
            "id", "url", "platform", "title", "thumbnail", "kind",
            "audio_only", "overrides", "playlist_items", "section",
            "status", "percent", "speed", "eta", "downloaded",
            "total", "item_index", "item_count", "file_path", "error",
            "created_at", "completed_at")}


class DownloadManager:
    def __init__(self) -> None:
        self.items: Dict[str, DownloadItem] = {}
        self._order: List[str] = []
        self._cond = threading.Condition()
        self._active = 0
        self._started = False

    # ---------------- public API ----------------

    def start(self) -> None:
        if not self._started:
            self._started = True
            # Clean up temp print-files a crashed previous run left in data\.
            try:
                for stale in config.DATA_DIR.glob("files_*.txt"):
                    stale.unlink(missing_ok=True)
            except OSError:
                pass
            # Rows stuck in a live state from a previous run can never finish.
            try:
                history.mark_interrupted()
            except Exception:
                pass
            threading.Thread(target=self._dispatch_loop, daemon=True).start()

    def add(self, url: str, mode: str, preview: Dict[str, Any],
            overrides: Optional[Dict[str, Any]] = None,
            items: Optional[str] = None,
            section: Optional[Dict[str, Any]] = None,
            selected_indices: Optional[List[int]] = None) -> List[dict]:
        """mode: 'single' or 'playlist' (the playlist-confirmation answer).

        overrides: per-download {quality, video_format, audio_format,
        audio_bitrate} values that win over the global Settings (TASK 6).
        items: yt-dlp --playlist-items selection, e.g. "1,4,7-12" (TASK 7).
        section: {start_sec, end_sec} clip range for single videos (TASK 9).
        selected_indices: 0-based Spotify track picker choice; absent or
        empty downloads every track (BUG 5).
        """
        platform = preview.get("platform") or "unknown"
        overrides = {k: v for k, v in (overrides or {}).items()
                     if v is not None} or None
        section = _parse_section(section)
        if section and (mode == "playlist" or platform == "spotify"):
            raise ValueError(
                "A time range can only be downloaded for a single video.")
        created: List[DownloadItem] = []

        if platform == "spotify":
            # Spotify is always audio-only; only the audio format/bitrate
            # part of an override applies.
            if overrides:
                overrides = {k: v for k, v in overrides.items()
                             if k in ("audio_format", "audio_bitrate")} or None
            tracks = spotify.resolve_tracks(url)
            if mode == "single" and tracks:
                tracks = tracks[:1]
            elif selected_indices:
                # Track picker (BUG 5): 0-based indices; empty means all.
                tracks = [tracks[i] for i in selected_indices
                          if 0 <= i < len(tracks)]
            if not tracks:
                raise ValueError("Couldn't resolve any tracks from this Spotify link.")
            for t in tracks:
                # The card must NEVER show the shared playlist/album art -
                # only this track's own cover. When the list only carried
                # the shared art, the worker swaps in the track's own cover
                # the moment its download starts (TASK 2).
                own_art = (None if t.get("cover_track_id")
                           else t.get("cover"))
                created.append(DownloadItem(
                    url=url, platform="spotify", title=f"{t['artist']} - {t['title']}",
                    thumbnail=own_art,
                    kind="single", audio_only=True, meta=t,
                    overrides=overrides))
        else:
            if platform == "soundcloud" and overrides:
                # SoundCloud is always audio-only (music platform): a video
                # quality override would be meaningless, so only the audio
                # format/bitrate part of an override applies (same as
                # Spotify).
                overrides = {k: v for k, v in overrides.items()
                             if k in ("audio_format", "audio_bitrate")} or None
            quality = ((overrides or {}).get("quality")
                       or load_settings().quality)
            title = preview.get("title") or url
            if section:
                # Make it obvious in Downloads/history that this is a clip.
                title += (f" [{_fmt_clock(section['start_sec'])}"
                          f"\u2013{_fmt_clock(section['end_sec'])}]")
            # Per-track artwork for the card (BUG 7): a playlist job shows
            # the cover of the track it is actually working on, matching
            # what ends up embedded in the finished files.
            art_map = _art_map_from_preview(preview)
            first_art = None
            if mode == "playlist" and art_map:
                entries = [e for e in (preview.get("entries") or [])
                           if isinstance(e, dict)]
                if selected_indices:
                    entries = [entries[i] for i in selected_indices
                               if 0 <= i < len(entries)]
                for entry in entries:
                    thumb = entry.get("thumbnail")
                    if isinstance(thumb, str) and thumb:
                        first_art = thumb
                        break
            created.append(DownloadItem(
                url=url, platform=platform,
                title=title,
                thumbnail=first_art or preview.get("thumbnail"),
                art_map=art_map or None,
                kind="playlist" if mode == "playlist" else "single",
                audio_only=platform == "soundcloud" or quality == "audio",
                overrides=overrides,
                playlist_items=items if mode == "playlist" else None,
                section=section))

        with self._cond:
            for item in created:
                self.items[item.id] = item
                self._order.append(item.id)
                history.upsert(item.to_dict())
            self._cond.notify_all()
        bus.publish({"type": "queue.changed"})
        return [i.to_dict() for i in created]

    def cancel(self, item_id: str) -> bool:
        item = self.items.get(item_id)
        if not item:
            return False
        item.cancelled = True
        item.paused = False
        _kill_proc_tree(item.proc)
        if item.status in ("queued", "paused"):
            self._set_state(item, "cancelled")
        return True

    def stop_all(self) -> None:
        """Kill every running job (engine shutdown / watchdog path)."""
        for item in list(self.items.values()):
            if item.status in ("starting", "downloading", "processing"):
                item.cancelled = True
                _kill_proc_tree(item.proc)

    def retry(self, item_id: str) -> bool:
        item = self.items.get(item_id)
        if item is not None and item.status not in ("error", "cancelled"):
            return False
        if item is None:
            item = self._rebuild_from_history(item_id, ("error", "cancelled"))
            if item is None:
                return False
        self._requeue(item, keep_progress=False)
        return True

    def pause(self, item_id: str) -> bool:
        """Pause a live download: kill the process tree exactly like cancel,
        but keep the .part files and mark the item 'paused' (TASK 8)."""
        item = self.items.get(item_id)
        if item is None or item.status not in ("queued", "starting",
                                               "downloading"):
            return False
        item.paused = True
        _kill_proc_tree(item.proc)
        if item.status == "queued":
            self._set_state(item, "paused")
        return True

    def resume(self, item_id: str) -> bool:
        """Re-queue a paused item like retry; yt-dlp's --continue default
        picks the download back up from the kept .part file."""
        item = self.items.get(item_id)
        if item is not None and item.status != "paused":
            return False
        if item is None:
            # Paused rows survive engine restarts as 'paused' in history.
            item = self._rebuild_from_history(item_id, ("paused",))
            if item is None:
                return False
        self._requeue(item, keep_progress=True)
        return True

    def pause_all(self) -> int:
        count = 0
        for item_id in list(self._order):
            if self.pause(item_id):
                count += 1
        return count

    def resume_all(self) -> int:
        count = 0
        seen = set()
        for item_id in list(self._order):
            seen.add(item_id)
            if self.resume(item_id):
                count += 1
        for row in history.list_all():
            if row.get("status") == "paused" and row["id"] not in seen:
                if self.resume(row["id"]):
                    count += 1
        return count

    def _rebuild_from_history(self, item_id: str,
                              statuses: Tuple[str, ...]
                              ) -> Optional[DownloadItem]:
        """The engine restarted since this item ran: rebuild it from the
        history DB so Retry/Resume keep working across engine restarts."""
        row = next((r for r in history.list_all()
                    if r.get("id") == item_id), None)
        if (not row or not row.get("url")
                or (row.get("status") or "") not in statuses):
            return None
        platform = row.get("platform") or "unknown"
        audio_only = row.get("audio_only")
        if audio_only is None:
            audio_only = (platform in ("spotify", "soundcloud")
                          or load_settings().quality == "audio")
        item = DownloadItem(
            url=row["url"], platform=platform,
            title=row.get("title") or "",
            thumbnail=row.get("thumbnail"),
            kind=row.get("kind") or "single",
            audio_only=bool(audio_only),
            overrides=row.get("overrides"),
            playlist_items=row.get("playlist_items"),
            section=row.get("section"),
            id=item_id,
            created_at=row.get("created_at") or _now())
        self.items[item_id] = item
        return item

    def _requeue(self, item: DownloadItem, keep_progress: bool) -> None:
        item.status = "queued"
        item.cancelled = False
        item.paused = False
        item.error = None
        item.speed = None
        item.eta = None
        item.completed_at = None
        if not keep_progress:
            # Retry starts over; Resume keeps the last percent/bytes visible
            # until fresh progress arrives.
            item.percent = 0.0
            item.downloaded = 0
            item.total = 0
        with self._cond:
            if item.id not in self._order:
                self._order.append(item.id)
            self._cond.notify_all()
        self._set_state(item, "queued")

    def snapshot(self) -> List[dict]:
        with self._cond:
            return [self.items[i].to_dict()
                    for i in self._order if i in self.items]

    # ---------------- internals ----------------

    def _set_state(self, item: DownloadItem, status: str,
                   error: Optional[str] = None) -> None:
        item.status = status
        item.error = error
        if status in ("completed", "error", "cancelled"):
            item.completed_at = _now()
        history.upsert(item.to_dict())
        bus.publish({"type": "download.state", "item": item.to_dict()})
        if status in ("downloading", "paused", "completed", "error",
                      "cancelled"):
            logs.log(f"[{item.platform}] {item.title or item.url}: {status}"
                     + (f" - {error}" if error else ""),
                     level="error" if status == "error" else "info",
                     source="downloader")

    def _dispatch_loop(self) -> None:
        while True:
            with self._cond:
                nxt = next((self.items[i] for i in self._order
                            if self.items[i].status == "queued"), None)
                if nxt is None or self._active >= load_settings().max_concurrent:
                    self._cond.wait(timeout=1.0)
                    continue
                self._active += 1
                nxt.status = "starting"
            threading.Thread(target=self._run_job, args=(nxt,), daemon=True).start()

    def _run_job(self, item: DownloadItem) -> None:
        try:
            self._worker(item)
        except Exception:
            self._set_state(item, "error", "Something went wrong with this download.")
        finally:
            with self._cond:
                self._active -= 1
                self._cond.notify_all()

    def _build_cmd(self, item: DownloadItem, s: Settings, printfile: Path,
                   use_cookies: bool = True,
                   force_best: bool = False) -> List[str]:
        out_dir = Path(s.output_dir)
        if s.per_platform_subfolders:
            out_dir = out_dir / item.platform.capitalize()
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [str(config.resolve_tool("yt-dlp") or config.YTDLP_PATH),
               "--newline", "--no-warnings",
               "--progress-template", PROGRESS_TMPL,
               "--retries", str(s.retries), "--windows-filenames",
               "--trim-filenames", "150",
               "-o", str(out_dir / _output_template(s.filename_template,
                                                    item.section)),
               "--print-to-file", "after_move:filepath", str(printfile)]
        # Prefer the portable vendor ffmpeg; fall back to one on PATH.
        if config.FFMPEG_PATH.exists():
            cmd += ["--ffmpeg-location", str(config.VENDOR_DIR)]
        else:
            ffmpeg = config.resolve_tool("ffmpeg")
            if ffmpeg is not None:
                cmd += ["--ffmpeg-location", str(ffmpeg.parent)]

        if item.kind == "playlist":
            cmd += ["--yes-playlist", "--ignore-errors"]
            if item.playlist_items:
                cmd += ["--playlist-items", item.playlist_items]
        else:
            cmd += ["--no-playlist"]
            if item.section:
                # Clip download: only grab the chosen time range (TASK 9).
                cmd += ["--download-sections",
                        f"*{_hms(item.section['start_sec'])}"
                        f"-{_hms(item.section['end_sec'])}",
                        "--force-keyframes-at-cuts"]

        if s.speed_limit_kbps:
            cmd += ["-r", f"{s.speed_limit_kbps}K"]
        if use_cookies:
            cmd += _cookie_args(s)

        # Per-download overrides win over the global Settings (TASK 6).
        ov = item.overrides or {}
        quality = ov.get("quality") or s.quality
        video_format = ov.get("video_format") or s.video_format
        audio_format = ov.get("audio_format") or s.audio_format
        audio_bitrate = ov.get("audio_bitrate") or s.audio_bitrate

        audio = item.audio_only
        if audio:
            cmd += ["-f", "best" if force_best else "bestaudio/best", "-x",
                    "--audio-format", audio_format]
            if audio_format != "flac":
                cmd += ["--audio-quality", f"{audio_bitrate}K"]
        else:
            # bestvideo* also matches combined video+audio formats, and the
            # trailing /best is an unconditional fallback - both matter for
            # sites like Instagram that expose only muxed or HLS formats.
            if force_best:
                cmd += ["-f", "best"]
            elif quality in ("2160", "1440", "1080", "720"):
                h = quality
                cmd += ["-f", f"bestvideo*[height<={h}]+bestaudio/"
                              f"best[height<={h}]/best"]
            else:
                cmd += ["-f", "bestvideo*+bestaudio/best"]
            # Harmless for single-format downloads; when merging actually
            # happens the container still follows the user's setting.
            cmd += ["--merge-output-format", video_format]

        if s.embed_metadata:
            cmd += ["--embed-metadata"]
        # Spotify cover art comes from Spotify itself in _postprocess; never
        # embed or save the matched YouTube video's thumbnail (TASK 4).
        if s.embed_thumbnail and item.platform != "spotify":
            cmd += ["--embed-thumbnail"]
        if s.save_thumbnail_file and item.platform != "spotify":
            cmd += ["--write-thumbnail"]
        if s.embed_chapters and not audio:
            cmd += ["--embed-chapters"]
        if s.embed_subtitles and not audio:
            cmd += ["--embed-subs"]
        # Needed for the description-timestamp chapters fallback:
        if item.platform == "youtube" and not audio and s.embed_chapters:
            cmd += ["--write-info-json"]

        if item.platform == "spotify":
            # YouTube proper (ytsearch), NOT YouTube Music - more reliable.
            # After an engine restart the resolved meta is gone; the stored
            # title is already "Artist - Title", which is the same query.
            query = ((item.meta or {}).get("search_query")
                     or item.title or item.url)
            cmd += [f"ytsearch1:{query}"]
        else:
            cmd += [item.url]
        return cmd

    def _worker(self, item: DownloadItem) -> None:
        s = load_settings()
        if item.cancelled:
            # Cancelled while still "starting" (before the process existed).
            self._set_state(item, "cancelled")
            return
        if item.paused:
            # Paused while still "starting" (before the process existed).
            self._set_state(item, "paused")
            return
        if config.resolve_tool("yt-dlp") is None:
            self._set_state(item, "error",
                            "yt-dlp isn't installed yet. Open Settings > "
                            "Dependencies and click Install.")
            return

        printfile = config.DATA_DIR / f"files_{item.id}.txt"
        printfile.unlink(missing_ok=True)
        try:
            self._worker_inner(item, s, printfile)
        finally:
            # Cancelled/failed jobs must not leave temp print-files in data\,
            # and the finished process handle must not be kept around.
            printfile.unlink(missing_ok=True)
            item.proc = None

    def _worker_inner(self, item: DownloadItem, s: Settings,
                      printfile: Path) -> None:
        # From the very first "downloading" event the preview must carry
        # this track's OWN album art, never the playlist cover (TASK 2).
        self._resolve_spotify_art(item)
        try:
            cmd = self._build_cmd(item, s, printfile)
        except OSError as exc:
            logs.log(f"Cannot prepare output folder: {exc}", level="error",
                     source="downloader")
            self._set_state(item, "error",
                            "Couldn't create the output folder. Check the "
                            "output folder in Settings.")
            return

        self._set_state(item, "downloading")
        rc, tail = self._run_ytdlp(item, cmd)

        # Chrome/Edge can refuse to let yt-dlp decrypt their cookie store
        # (DPAPI, yt-dlp issue #10927). Retry once without cookies so public
        # links still download even with a broken browser selection.
        cookie_decrypt_failed = False
        if (rc != 0 and not item.cancelled and not item.paused
                and _cookie_args(s)
                and _is_cookie_decrypt_error("\n".join(tail))):
            cookie_decrypt_failed = True
            for ln in tail[-3:]:
                logs.log(ln[:300], level="error", source="yt-dlp")
            logs.log("Browser cookies couldn't be decrypted (DPAPI); "
                     "retrying without cookies.", source="downloader")
            try:
                cmd = self._build_cmd(item, s, printfile, use_cookies=False)
                rc, tail = self._run_ytdlp(item, cmd)
            except OSError:
                pass  # keep the first attempt's result

        # Instagram & friends sometimes only offer unusual formats that the
        # normal selector rejects; retry once with the plain "best" selector
        # (TASK 5), mirroring the DPAPI cookie retry above.
        if (rc != 0 and not item.cancelled and not item.paused
                and "requested format is not available"
                in "\n".join(tail).lower()):
            for ln in tail[-3:]:
                logs.log(ln[:300], level="error", source="yt-dlp")
            logs.log("Requested format unavailable; retrying once with the "
                     "generic 'best' selector.", source="downloader")
            try:
                cmd = self._build_cmd(item, s, printfile,
                                      use_cookies=not cookie_decrypt_failed,
                                      force_best=True)
                rc, tail = self._run_ytdlp(item, cmd)
            except OSError:
                pass  # keep the first attempt's result

        if item.paused:
            # Paused mid-download: keep the .part files and offer Resume.
            self._set_state(item, "paused")
            return
        if item.cancelled:
            self._set_state(item, "cancelled")
            return
        if rc != 0:
            # Raw yt-dlp output to the developer log; friendly text to the UI.
            for ln in tail[-5:]:
                logs.log(ln[:300], level="error", source="yt-dlp")
            msg = self._friendly_error(tail)
            if cookie_decrypt_failed and any(
                    k in msg.lower() for k in ("sign-in", "sign in",
                                               "login", "cookies")):
                # The real blocker is the unreadable cookies, not the
                # sign-in prompt that the cookie-less retry ran into.
                msg = _DPAPI_MESSAGE
            self._set_state(item, "error", msg)
            return

        self._set_state(item, "processing")
        try:
            self._postprocess(item, s, printfile)
        except Exception:
            pass  # never fail a finished download on post-processing polish
        item.percent = 100.0
        self._set_state(item, "completed")

    def _run_ytdlp(self, item: DownloadItem,
                   cmd: List[str]) -> Tuple[int, List[str]]:
        """Run one yt-dlp attempt; returns (returncode, last output lines)."""
        env = os.environ.copy()
        env["PATH"] = str(config.VENDOR_DIR) + os.pathsep + env.get("PATH", "")
        tail: List[str] = []
        last_pub = 0.0
        try:
            item.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=env, creationflags=_NO_WINDOW)
            if item.cancelled or item.paused:
                # Cancel/pause raced with process launch; kill the fresh one.
                _kill_proc_tree(item.proc)
            assert item.proc.stdout is not None
            for line in item.proc.stdout:
                line = line.rstrip()
                if line.startswith("MIDAS|"):
                    self._apply_progress(item, line)
                    now = time.monotonic()
                    if now - last_pub > 0.25:
                        last_pub = now
                        bus.publish({"type": "download.progress",
                                     "item": item.to_dict()})
                elif line:
                    tail.append(line)
                    if len(tail) > 30:
                        tail.pop(0)
            rc = item.proc.wait()
        except Exception as exc:
            logs.log(f"yt-dlp failed to run: {type(exc).__name__}: "
                     f"{str(exc)[:200]}", level="error", source="downloader")
            rc = -1
        return rc, tail

    @staticmethod
    def _friendly_error(tail: List[str]) -> str:
        blob = "\n".join(tail)
        for needle, message in FRIENDLY_ERRORS:
            if needle.lower() in blob.lower():
                return message
        for line in reversed(tail):
            if "ERROR" in line.upper():
                return line.split(":", 1)[-1].strip()[:160] or \
                    "Download failed. Please retry."
        return "Download failed. Please retry."

    @staticmethod
    def _apply_progress(item: DownloadItem, line: str) -> None:
        parts = line.split("|")
        if len(parts) < 9:
            return

        def num(v: str) -> Optional[float]:
            try:
                parsed = float(v)
            except (TypeError, ValueError):
                return None
            # yt-dlp can print NaN/inf for unknown sizes; int(NaN) raises.
            return parsed if math.isfinite(parsed) else None

        downloaded = num(parts[1]) or 0
        total = num(parts[2]) or num(parts[3]) or 0
        item.downloaded = int(downloaded)
        item.total = int(total)
        if total:
            item.percent = round(downloaded / total * 100, 1)
        item.speed = num(parts[4])
        eta = num(parts[5])
        item.eta = int(eta) if eta is not None else None
        idx, count = num(parts[6]), num(parts[7])
        item.item_index = int(idx) if idx else None
        item.item_count = int(count) if count else None
        # The title is the LAST template field and may itself contain "|";
        # re-join the remaining segments instead of truncating at the first.
        title = "|".join(parts[8:]).strip()
        if title and title != "NA":
            if item.platform != "spotify":  # keep 'Artist - Track' for Spotify
                item.title = title
        # Show the artwork of the track being downloaded right now, not the
        # playlist's icon (BUG 7). The finished file already carries this
        # exact cover, so the card now matches the file explorer.
        own_art = _art_for(item, title if title != "NA" else None,
                           item.item_index)
        if own_art and own_art != item.thumbnail:
            item.thumbnail = own_art
            try:
                history.upsert(item.to_dict())
            except Exception:
                pass  # artwork polish must never break a live download

    def _resolve_spotify_art(self, item: DownloadItem) -> None:
        """Swap the shared playlist/album art for this track's own cover
        BEFORE the download starts, so the in-progress card never shows
        the playlist image (TASK 2)."""
        if item.platform != "spotify" or not item.meta:
            return
        track_id = item.meta.get("cover_track_id")
        if not track_id:
            return
        own = spotify.track_cover_url(track_id)
        if own:
            item.meta["cover"] = own
            item.meta["cover_track_id"] = None
            item.thumbnail = own
            history.upsert(item.to_dict())

    def _postprocess(self, item: DownloadItem, s: Settings,
                     printfile: Path) -> None:
        paths: List[Path] = []
        if printfile.exists():
            for line in printfile.read_text(encoding="utf-8").splitlines():
                p = Path(line.strip())
                if line.strip() and p.exists():
                    paths.append(p)
            printfile.unlink(missing_ok=True)
        if paths:
            item.file_path = str(paths[0])

        # Download the Spotify cover ONCE; the same bytes are embedded into
        # the tags and (optionally) saved as <media filename>.jpg (TASK 4).
        cover: Optional[bytes] = None
        if (item.platform == "spotify" and item.meta
                and item.meta.get("cover") and paths):
            art_url = item.meta["cover"]
            track_id = item.meta.get("cover_track_id")
            if track_id:
                # The list only carried the shared album/playlist art; one
                # lazy lookup swaps in this track's own cover.
                own = spotify.track_cover_url(track_id)
                if own:
                    art_url = own
                    item.thumbnail = own
            try:
                cover = httpx.get(art_url, timeout=20,
                                  follow_redirects=True).content
            except Exception:
                cover = None
                logs.log("Couldn't download the Spotify cover art; the file "
                         "keeps its tags without artwork.", level="error",
                         source="downloader")

        for path in paths:
            if item.platform == "spotify" and item.meta:
                self._tag_audio(path, item.meta, cover)
                if cover and s.save_thumbnail_file:
                    try:
                        path.with_suffix(".jpg").write_bytes(cover)
                    except OSError:
                        pass
            if (item.platform == "youtube" and not item.audio_only
                    and s.embed_chapters):
                self._chapters_fallback(path)

    @staticmethod
    def _chapters_fallback(path: Path) -> None:
        info_path = path.with_suffix(".info.json")
        if not info_path.exists():
            return
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            if not info.get("chapters"):
                parsed = chapters.parse_description(info.get("description", ""))
                if parsed:
                    chapters.inject(path, parsed, info.get("duration"))
        except Exception:
            pass  # malformed info.json must not break the remaining files
        finally:
            info_path.unlink(missing_ok=True)

    @staticmethod
    def _tag_audio(path: Path, meta: Dict[str, Any],
                   cover: Optional[bytes]) -> None:
        """Embed title/artist/album + cover art (ID3 / MP4 / FLAC / Opus).

        The cover bytes are downloaded ONCE by the caller (so they can also
        be saved as the thumbnail file). The picture embedding itself lives
        in covers.embed_cover_audio - shared with the Studio - which strips
        any pre-existing art before embedding, so the Spotify cover always
        replaces whatever was there instead of being appended next to it.
        """
        try:
            suffix = path.suffix.lower()
            if suffix == ".mp3":
                from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TIT2, \
                    TPE1
                try:
                    tags = ID3(path)
                except ID3NoHeaderError:
                    tags = ID3()
                tags.setall("TIT2", [TIT2(encoding=3, text=meta["title"])])
                tags.setall("TPE1", [TPE1(encoding=3, text=meta["artist"])])
                if meta.get("album"):
                    tags.setall("TALB", [TALB(encoding=3, text=meta["album"])])
                tags.save(path)
            elif suffix in (".m4a", ".mp4"):
                from mutagen.mp4 import MP4
                mp4 = MP4(path)
                mp4["\xa9nam"] = [meta["title"]]
                mp4["\xa9ART"] = [meta["artist"]]
                if meta.get("album"):
                    mp4["\xa9alb"] = [meta["album"]]
                mp4.save()
            elif suffix == ".flac":
                from mutagen.flac import FLAC
                flac = FLAC(path)
                flac["title"] = meta["title"]
                flac["artist"] = meta["artist"]
                if meta.get("album"):
                    flac["album"] = meta["album"]
                flac.save()
            elif suffix in (".opus", ".ogg"):
                from mutagen.oggopus import OggOpus
                opus = OggOpus(path)
                opus["title"] = [meta["title"]]
                opus["artist"] = [meta["artist"]]
                if meta.get("album"):
                    opus["album"] = [meta["album"]]
                opus.save()
            if cover:
                embed_cover_audio(path, cover)
        except Exception:
            pass  # tagging is best-effort polish


manager = DownloadManager()
