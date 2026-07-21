"""Post-download media editing: cover art, conversion, subtitles, trim.

All heavy media work happens here in the engine via the vendored
ffmpeg/ffprobe (resolved through config.resolve_tool) and mutagen — the
Flutter shell only sends edit requests. Long jobs publish `studio.progress`
and `studio.state` events on the WebSocket bus, mirroring the downloader's
event style.

Safety rules:
  * never operate on files that are still downloading,
  * only one Studio job per file at a time,
  * every write goes to a temp file first and is os.replace()d into place,
    so a failed job always leaves the original file untouched.
"""
import base64
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import config
from ..events import bus
from . import history, logs
from .covers import (AUDIO_SUFFIXES, embed_cover_audio, extract_cover_audio,
                     mime_of)
from .downloader import manager

_NO_WINDOW = 0x08000000 if config.IS_WINDOWS else 0

_LIVE_STATUSES = ("queued", "starting", "downloading", "processing", "paused")

AUDIO_TARGETS = {"mp3": "libmp3lame", "m4a": "aac", "flac": "flac",
                 "opus": "libopus"}
VIDEO_TARGETS = ("mp4", "mkv")

_busy_lock = threading.Lock()
_busy: Dict[str, str] = {}   # item_id -> running operation name


class StudioError(Exception):
    """Raised with a short, user-friendly message (never a traceback)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- plumbing


class _job:
    """Guard: one Studio job per file at a time, with state events."""

    def __init__(self, item_id: str, op: str) -> None:
        self.item_id = item_id
        self.op = op

    def __enter__(self) -> "_job":
        with _busy_lock:
            if self.item_id in _busy:
                raise StudioError("Another Studio edit is already running "
                                  "for this file. Let it finish first.")
            _busy[self.item_id] = self.op
        bus.publish({"type": "studio.state", "item_id": self.item_id,
                     "op": self.op, "state": "running"})
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        with _busy_lock:
            _busy.pop(self.item_id, None)
        if exc_type is None:
            state = {"state": "done"}
        else:
            message = (str(exc)[:200] if isinstance(exc, StudioError)
                       else "Something went wrong during this edit.")
            state = {"state": "error", "message": message}
        bus.publish({"type": "studio.state", "item_id": self.item_id,
                     "op": self.op, **state})


def _progress(item_id: str, op: str, percent: Optional[float]) -> None:
    bus.publish({"type": "studio.progress", "item_id": item_id, "op": op,
                 "percent": percent})


def _ffmpeg() -> Path:
    tool = config.resolve_tool("ffmpeg")
    if tool is None:
        raise StudioError("FFmpeg isn't installed yet. Open Settings > "
                          "Dependencies and click Install.")
    return tool


def _ffprobe() -> Path:
    tool = config.resolve_tool("ffprobe")
    if tool is None:
        raise StudioError("FFprobe isn't installed yet. Open Settings > "
                          "Dependencies and click Install.")
    return tool


def _run(cmd: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=_NO_WINDOW)


def _history_row(item_id: str) -> Dict[str, Any]:
    row = next((r for r in history.list_all()
                if r.get("id") == item_id), None)
    if row is None:
        raise StudioError("This download isn't in the history anymore.")
    return row


def _media_path(item_id: str) -> Path:
    """Resolve the on-disk file for a finished download, refusing live jobs."""
    item = manager.items.get(item_id)
    if item is not None and item.status in _LIVE_STATUSES:
        raise StudioError("This file is still downloading. "
                          "Wait for it to finish first.")
    row = _history_row(item_id)
    if (row.get("status") or "") in _LIVE_STATUSES:
        raise StudioError("This file is still downloading. "
                          "Wait for it to finish first.")
    path = Path(row.get("file_path") or "")
    if not row.get("file_path") or not path.exists():
        raise StudioError("The file for this download no longer exists "
                          "on disk.")
    return path


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(1, 100):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem} ({uuid.uuid4().hex[:6]}){path.suffix}")


def _tmp_beside(target: Path) -> Path:
    """Temp path on the same volume so os.replace stays atomic."""
    return target.with_name(f"{target.stem}.midastmp{target.suffix}")


def _new_history_row(source_row: Dict[str, Any], file_path: Path,
                     audio_only: bool) -> None:
    """A Studio result becomes a fresh completed row in Downloads."""
    history.upsert({
        "id": uuid.uuid4().hex[:12],
        "url": source_row.get("url"),
        "platform": source_row.get("platform"),
        "title": source_row.get("title"),
        "thumbnail": source_row.get("thumbnail"),
        "kind": source_row.get("kind") or "single",
        "audio_only": audio_only,
        "status": "completed",
        "file_path": str(file_path),
        "error": None,
        "created_at": _now(),
        "completed_at": _now(),
    })
    bus.publish({"type": "queue.changed"})


# ------------------------------------------------------------------ ffprobe


def _probe(path: Path) -> Dict[str, Any]:
    proc = _run([_ffprobe(), "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", str(path)], timeout=60)
    if proc.returncode != 0:
        raise StudioError("Couldn't read this media file.")
    try:
        return json.loads(proc.stdout)
    except Exception:
        raise StudioError("Couldn't read this media file.")


def _subtitle_streams(probe: Dict[str, Any]) -> List[Dict[str, Any]]:
    subs = []
    for s in probe.get("streams", []):
        if s.get("codec_type") != "subtitle":
            continue
        tags = s.get("tags") or {}
        subs.append({"index": s.get("index"),
                     "language": tags.get("language") or "",
                     "title": tags.get("title") or "",
                     "codec": s.get("codec_name") or ""})
    return subs


def _attached_pic_indices(probe: Dict[str, Any]) -> List[int]:
    return [s["index"] for s in probe.get("streams", [])
            if s.get("codec_type") == "video"
            and (s.get("disposition") or {}).get("attached_pic") == 1]


def _attachment_indices(probe: Dict[str, Any]) -> List[int]:
    return [s["index"] for s in probe.get("streams", [])
            if s.get("codec_type") == "attachment"]


def _duration_of(probe: Dict[str, Any]) -> float:
    try:
        return float((probe.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def _summary(path: Path) -> Dict[str, Any]:
    probe = _probe(path)
    streams = probe.get("streams", [])
    attached = set(_attached_pic_indices(probe))
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and s["index"] not in attached), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    is_audio = path.suffix.lower() in AUDIO_SUFFIXES or video is None
    has_cover = bool(attached) or bool(_attachment_indices(probe))
    if is_audio and not has_cover:
        has_cover = extract_cover_audio(path) is not None
    bitrate = None
    if audio and audio.get("bit_rate"):
        try:
            bitrate = int(int(audio["bit_rate"]) / 1000)
        except (TypeError, ValueError):
            bitrate = None
    return {
        "container": path.suffix.lower().lstrip("."),
        "duration": _duration_of(probe),
        "width": (video or {}).get("width"),
        "height": (video or {}).get("height"),
        "video_codec": (video or {}).get("codec_name"),
        "audio_codec": (audio or {}).get("codec_name"),
        "audio_bitrate_kbps": bitrate,
        "is_audio": is_audio,
        "has_cover": has_cover,
        # File mtime: lets the app cache-bust cover thumbnails after edits.
        "cover_version": int(path.stat().st_mtime),
        "subtitles": _subtitle_streams(probe),
    }


# -------------------------------------------------------------------- items


# ffprobe summaries for unchanged files (same mtime/size) are reused so a
# Studio refresh does not spawn one ffprobe process per file on every call.
_PROBE_CACHE: Dict[str, Tuple[Tuple[int, int], Dict[str, Any]]] = {}


def list_items() -> Dict[str, Any]:
    """Completed downloads whose file still exists, with ffprobe info.

    Sources both the in-memory queue items and the history rows (BUG 1):
    a just-finished download with a slightly stale history row must still
    show up, so fresh in-memory items win the per-id dedup.
    """
    _ffprobe()  # friendly error early when the tool is missing
    items: List[Dict[str, Any]] = []
    seen: set = set()
    rows: List[Dict[str, Any]] = [
        item.to_dict() for item in list(manager.items.values())
        if item.status == "completed" and item.file_path]
    rows += history.list_all()
    for row in rows:
        item_id = row.get("id")
        if not item_id or item_id in seen:
            continue
        if row.get("status") != "completed" or not row.get("file_path"):
            continue
        path = Path(row["file_path"])
        if not path.exists():
            continue
        seen.add(item_id)
        entry = {"id": item_id, "title": row.get("title") or path.stem,
                 "platform": row.get("platform") or "unknown",
                 "thumbnail": row.get("thumbnail"),
                 "file_path": str(path), "file_name": path.name}
        try:
            stat = path.stat()
            sig = (stat.st_mtime_ns, stat.st_size)
            cached = _PROBE_CACHE.get(str(path))
            if cached is not None and cached[0] == sig:
                entry.update(cached[1])
            else:
                info = _summary(path)
                if len(_PROBE_CACHE) > 512:
                    _PROBE_CACHE.clear()
                _PROBE_CACHE[str(path)] = (sig, info)
                entry.update(info)
        except Exception as exc:
            # A probe failure must never hide the file from the list.
            logs.log(f"Couldn't probe {path.name}: {str(exc)[:120]}",
                     level="warning", source="studio")
        items.append(entry)
    return {"items": items}


def import_local_file(file_path: str) -> Dict[str, Any]:
    """Register any local media file as an editable Studio item (BUG 1).

    Inserts a synthetic completed history row so the file flows through
    the exact same list/edit plumbing as a download.
    """
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise StudioError("That file doesn't exist on disk anymore.")
    row = {"id": uuid.uuid4().hex, "url": "", "platform": "local",
           "title": path.stem, "status": "completed",
           "file_path": str(path.resolve()), "created_at": _now(),
           "completed_at": _now()}
    history.upsert(row)
    return {"id": row["id"], "title": row["title"],
            "file_path": row["file_path"]}


# -------------------------------------------------------------------- cover


def get_cover(item_id: str) -> Tuple[bytes, str]:
    """Currently embedded cover art (or attached thumbnail / poster frame)."""
    path = _media_path(item_id)
    if path.suffix.lower() in AUDIO_SUFFIXES:
        got = extract_cover_audio(path)
        if got is None:
            raise StudioError("This file has no embedded cover art yet.")
        return got
    probe = _probe(path)
    with tempfile.TemporaryDirectory(prefix="midas_cover_") as tmpdir:
        out = Path(tmpdir) / "cover.png"
        attached = _attached_pic_indices(probe)
        if attached:
            proc = _run([_ffmpeg(), "-y", "-i", path,
                         "-map", f"0:{attached[0]}", "-frames:v", "1", out])
        elif _attachment_indices(probe):
            out = Path(tmpdir) / "attachment.bin"
            proc = _run([_ffmpeg(), "-y",
                         f"-dump_attachment:{_attachment_indices(probe)[0]}",
                         str(out), "-i", path])
        else:
            # No embedded art: fall back to a poster frame so the Cover
            # editor always has an image to start from.
            seek = max(min(_duration_of(probe) / 2, 1.0), 0.0)
            proc = _run([_ffmpeg(), "-y", "-ss", f"{seek:.2f}", "-i", path,
                         "-frames:v", "1", out])
        if not out.exists() or out.stat().st_size == 0:
            del proc  # noqa: F841 - keep the friendly message below
            raise StudioError("Couldn't extract the cover from this file.")
        data = out.read_bytes()
    return data, mime_of(data)


def _transform_image(image: bytes,
                     transform: Optional[Dict[str, Any]]) -> bytes:
    """Apply rotate/crop/scale with one ffmpeg image filter chain."""
    t = transform or {}
    filters: List[str] = []
    rotate = int(t.get("rotate") or 0) % 360
    for _ in range(rotate // 90):
        filters.append("transpose=1")
    crop = t.get("crop") or None
    if crop:
        w, h = int(crop.get("width") or 0), int(crop.get("height") or 0)
        x, y = int(crop.get("x") or 0), int(crop.get("y") or 0)
        if w > 0 and h > 0:
            filters.append(f"crop={w}:{h}:{x}:{y}")
    if t.get("width") and t.get("height"):
        filters.append(f"scale={int(t['width'])}:{int(t['height'])}")
    if t.get("pad_width") and t.get("pad_height"):
        px = int(t.get("pad_x") or 0)
        py = int(t.get("pad_y") or 0)
        filters.append(
            f"pad={int(t['pad_width'])}:{int(t['pad_height'])}:{px}:{py}")
    if not filters:
        return image
    with tempfile.TemporaryDirectory(prefix="midas_img_") as tmpdir:
        src = Path(tmpdir) / "in.img"
        dst = Path(tmpdir) / "out.jpg"
        src.write_bytes(image)
        proc = _run([_ffmpeg(), "-y", "-i", src, "-vf", ",".join(filters),
                     "-frames:v", "1", "-q:v", "2", dst])
        if proc.returncode != 0 or not dst.exists():
            raise StudioError("Couldn't apply the image transform.")
        return dst.read_bytes()


def set_cover(item_id: str, image_base64: Optional[str],
              transform: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Replace/edit the cover art of a finished download."""
    path = _media_path(item_id)
    with _job(item_id, "cover"):
        if image_base64:
            try:
                image = base64.b64decode(image_base64)
            except Exception:
                raise StudioError("The uploaded image couldn't be read.")
        else:
            image, _ = get_cover_bytes_for_edit(path)
        image = _transform_image(image, transform)

        suffix = path.suffix.lower()
        if suffix in AUDIO_SUFFIXES:
            # Same mutagen logic the downloader uses (strips old art first).
            if not embed_cover_audio(path, image):
                raise StudioError("Couldn't embed the cover into this "
                                  "audio file.")
            logs.log(f"Cover updated for {path.name}", source="studio")
            return {}

        probe = _probe(path)
        with tempfile.TemporaryDirectory(prefix="midas_cover_",
                                         dir=str(path.parent)) as tmpdir:
            cover = Path(tmpdir) / ("cover.png" if mime_of(image) ==
                                    "image/png" else "cover.jpg")
            cover.write_bytes(image)
            tmp = _tmp_beside(path)
            try:
                if suffix == ".mkv":
                    cmd = [_ffmpeg(), "-y", "-i", path, "-map", "0"]
                    if _attachment_indices(probe):
                        cmd += ["-map", "-0:t"]
                    for idx in _attached_pic_indices(probe):
                        cmd += ["-map", f"-0:{idx}"]
                    cmd += ["-c", "copy",
                            "-attach", cover,
                            "-metadata:s:t:0", f"mimetype={mime_of(image)}",
                            "-metadata:s:t:0", f"filename={cover.name}",
                            tmp]
                elif suffix in (".mp4", ".m4v", ".mov", ".m4a"):
                    attached = _attached_pic_indices(probe)
                    keep_videos = [
                        s for s in probe.get("streams", [])
                        if s.get("codec_type") == "video"
                        and s["index"] not in attached]
                    cmd = [_ffmpeg(), "-y", "-i", path, "-i", cover,
                           "-map", "0"]
                    for idx in attached:
                        cmd += ["-map", f"-0:{idx}"]
                    cmd += ["-map", "1", "-c", "copy",
                            f"-disposition:v:{len(keep_videos)}",
                            "attached_pic", tmp]
                else:
                    raise StudioError("Cover art isn't supported for this "
                                      "container.")
                proc = _run(cmd)
                if proc.returncode != 0 or not tmp.exists():
                    for ln in (proc.stderr or "").splitlines()[-3:]:
                        logs.log(ln[:300], level="error", source="studio")
                    raise StudioError("Couldn't embed the cover into this "
                                      "video file.")
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        logs.log(f"Cover updated for {path.name}", source="studio")
        return {}


def get_cover_bytes_for_edit(path: Path) -> Tuple[bytes, str]:
    """Existing cover (audio tags / attached pic); friendly error if none."""
    if path.suffix.lower() in AUDIO_SUFFIXES:
        got = extract_cover_audio(path)
        if got is None:
            raise StudioError("This file has no embedded cover yet. "
                              "Pick an image to add one.")
        return got
    probe = _probe(path)
    attached = _attached_pic_indices(probe)
    if not attached:
        raise StudioError("This file has no embedded cover yet. "
                          "Pick an image to add one.")
    with tempfile.TemporaryDirectory(prefix="midas_cover_") as tmpdir:
        out = Path(tmpdir) / "cover.png"
        _run([_ffmpeg(), "-y", "-i", path, "-map", f"0:{attached[0]}",
              "-frames:v", "1", out])
        if not out.exists() or out.stat().st_size == 0:
            raise StudioError("Couldn't extract the current cover.")
        data = out.read_bytes()
    return data, mime_of(data)


# ------------------------------------------------------------ ffmpeg + bus


def _run_ffmpeg_progress(item_id: str, op: str, cmd: List[str],
                         duration: float, base: float = 0.0,
                         span: float = 100.0) -> Tuple[int, List[str]]:
    """Run ffmpeg with -progress pipe:1, publishing percent over the bus.

    `base`/`span` let multi-step jobs map this run into a slice of 0..100.
    """
    full = [str(cmd[0]), "-y", "-nostats", "-loglevel", "error",
            "-progress", "pipe:1"] + [str(c) for c in cmd[1:]]
    tail: List[str] = []
    last_pub = 0.0
    try:
        proc = subprocess.Popen(full, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace",
                                bufsize=1, creationflags=_NO_WINDOW)
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if line.startswith("out_time_ms=") and duration > 0:
                try:
                    done = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                pct = base + min(done / duration, 1.0) * span
                now = time.monotonic()
                if now - last_pub > 0.25:
                    last_pub = now
                    _progress(item_id, op, round(min(pct, 99.9), 1))
            elif "=" not in line:
                tail.append(line)
                if len(tail) > 20:
                    tail.pop(0)
        rc = proc.wait()
    except Exception as exc:
        logs.log(f"ffmpeg failed to run: {type(exc).__name__}: "
                 f"{str(exc)[:200]}", level="error", source="studio")
        rc = -1
    return rc, tail


def _fail(tail: List[str], message: str) -> None:
    for ln in tail[-3:]:
        logs.log(ln[:300], level="error", source="studio")
    raise StudioError(message)


# ------------------------------------------------------------------ convert


def convert(item_id: str, target: str, bitrate_kbps: Optional[int],
            keep_original: bool) -> Dict[str, Any]:
    """Convert/extract to a new file (new history row)."""
    target = (target or "").lower().lstrip(".")
    if target not in AUDIO_TARGETS and target not in VIDEO_TARGETS:
        raise StudioError("That target format isn't supported.")
    path = _media_path(item_id)
    row = _history_row(item_id)
    if path.suffix.lower().lstrip(".") == target:
        raise StudioError("The file is already in that format.")
    with _job(item_id, "convert"):
        probe = _probe(path)
        duration = _duration_of(probe)
        out = _unique_path(path.with_suffix(f".{target}"))
        tmp = _tmp_beside(out)
        try:
            if target in AUDIO_TARGETS:
                cmd = [_ffmpeg(), "-i", path, "-vn", "-map", "0:a:0",
                       "-map_metadata", "0",
                       "-c:a", AUDIO_TARGETS[target]]
                if target != "flac" and bitrate_kbps:
                    cmd += ["-b:a", f"{int(bitrate_kbps)}k"]
                cmd += [tmp]
                rc, tail = _run_ffmpeg_progress(item_id, "convert", cmd,
                                                duration)
                if rc != 0 or not tmp.exists():
                    _fail(tail, "Conversion failed. The file may use an "
                                "unusual codec.")
            else:
                cmd = [_ffmpeg(), "-i", path, "-map", "0"]
                if _attachment_indices(probe) and target == "mp4":
                    cmd += ["-map", "-0:t"]
                cmd += ["-map_metadata", "0", "-c", "copy"]
                if target == "mp4" and _subtitle_streams(probe):
                    cmd += ["-c:s", "mov_text"]
                cmd += [tmp]
                rc, tail = _run_ffmpeg_progress(item_id, "convert", cmd,
                                                duration)
                if rc != 0 or not tmp.exists():
                    # Odd subtitle/attachment streams: retry without them.
                    logs.log("Remux failed; retrying without subtitle "
                             "streams.", source="studio")
                    cmd = [_ffmpeg(), "-i", path, "-map", "0:v?",
                           "-map", "0:a?", "-map_metadata", "0",
                           "-c", "copy", tmp]
                    rc, tail = _run_ffmpeg_progress(item_id, "convert", cmd,
                                                    duration)
                    if rc != 0 or not tmp.exists():
                        _fail(tail, "Conversion failed. The file may use "
                                    "an unusual codec.")
            os.replace(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)

        # Carry the cover over when the target format supports it.
        if target in AUDIO_TARGETS:
            try:
                cover, _mime = get_cover_bytes_for_edit(path)
                embed_cover_audio(out, cover)
            except (StudioError, Exception):
                pass  # cover carry-over is best-effort polish

        _progress(item_id, "convert", 100.0)
        _new_history_row(row, out, audio_only=target in AUDIO_TARGETS)
        if not keep_original:
            try:
                path.unlink()
                row["file_path"] = None
                history.upsert(row)
            except OSError:
                logs.log(f"Couldn't delete the original file {path.name}",
                         level="error", source="studio")
        logs.log(f"Converted {path.name} -> {out.name}", source="studio")
        return {"file_path": str(out)}


# ---------------------------------------------------------------- subtitles


_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _validate_srt(content: str) -> int:
    """Returns the number of cues; raises StudioError when unparseable."""
    cues = _SRT_TIME.findall(content or "")
    if not cues:
        raise StudioError("This doesn't look like valid SRT: no "
                          "'00:00:01,000 --> 00:00:02,000' timings found.")
    return len(cues)


def _sub_codec_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in AUDIO_SUFFIXES:
        raise StudioError("Subtitles can only be embedded in video files.")
    if suffix in (".mp4", ".m4v", ".mov"):
        return "mov_text"
    if suffix == ".webm":
        return "webvtt"
    return "srt"  # mkv and friends


def list_subtitles(item_id: str) -> Dict[str, Any]:
    path = _media_path(item_id)
    return {"subtitles": _subtitle_streams(_probe(path))}


def extract_subtitle(item_id: str, stream_index: int) -> Dict[str, Any]:
    """Extract one stream to a UTF-8 .srt next to the media file."""
    path = _media_path(item_id)
    with _job(item_id, "subtitles"):
        out = _unique_path(
            path.with_name(f"{path.stem}.track{stream_index}.srt"))
        proc = _run([_ffmpeg(), "-y", "-i", path,
                     "-map", f"0:{stream_index}", "-c:s", "srt", out])
        if proc.returncode != 0 or not out.exists():
            for ln in (proc.stderr or "").splitlines()[-3:]:
                logs.log(ln[:300], level="error", source="studio")
            raise StudioError("Couldn't extract this subtitle track.")
        content = out.read_text(encoding="utf-8", errors="replace")
        logs.log(f"Extracted subtitles from {path.name}", source="studio")
        return {"file_path": str(out), "content": content}


def save_subtitle(item_id: str, content: str, replace_index: Optional[int],
                  language: Optional[str]) -> Dict[str, Any]:
    """Validate edited SRT text and re-embed it as a soft track."""
    path = _media_path(item_id)
    _validate_srt(content)
    codec = _sub_codec_for(path)
    with _job(item_id, "subtitles"):
        probe = _probe(path)
        existing = _subtitle_streams(probe)
        remaining = [s for s in existing if s["index"] != replace_index]
        with tempfile.TemporaryDirectory(prefix="midas_subs_",
                                         dir=str(path.parent)) as tmpdir:
            srt = Path(tmpdir) / "edited.srt"
            srt.write_text(content, encoding="utf-8")
            tmp = _tmp_beside(path)
            try:
                cmd = [_ffmpeg(), "-y", "-i", path, "-i", srt, "-map", "0"]
                if replace_index is not None:
                    cmd += ["-map", f"-0:{replace_index}"]
                cmd += ["-map", "1:0", "-c", "copy", "-c:s", codec,
                        f"-metadata:s:s:{len(remaining)}",
                        f"language={(language or 'und')[:3]}", tmp]
                proc = _run(cmd)
                if proc.returncode != 0 or not tmp.exists():
                    for ln in (proc.stderr or "").splitlines()[-3:]:
                        logs.log(ln[:300], level="error", source="studio")
                    raise StudioError("Couldn't embed the edited subtitles.")
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        logs.log(f"Re-embedded subtitles into {path.name}", source="studio")
        return {}


def delete_subtitle(item_id: str, stream_index: int) -> Dict[str, Any]:
    """Stream-copy remux without the chosen subtitle track."""
    path = _media_path(item_id)
    with _job(item_id, "subtitles"):
        probe = _probe(path)
        if stream_index not in [s["index"] for s in
                                _subtitle_streams(probe)]:
            raise StudioError("That subtitle track no longer exists.")
        tmp = _tmp_beside(path)
        try:
            proc = _run([_ffmpeg(), "-y", "-i", path, "-map", "0",
                         "-map", f"-0:{stream_index}", "-c", "copy", tmp])
            if proc.returncode != 0 or not tmp.exists():
                for ln in (proc.stderr or "").splitlines()[-3:]:
                    logs.log(ln[:300], level="error", source="studio")
                raise StudioError("Couldn't remove this subtitle track.")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        logs.log(f"Removed a subtitle track from {path.name}",
                 source="studio")
        return {}


_ASS_ALIGNMENT = {"bottom": 2, "middle": 5, "top": 8}


def _srt_to_positioned_ass(srt_path: Path, ass_path: Path, position: str,
                           font_size: int) -> None:
    """Convert SRT -> ASS, then patch the style's Alignment / Fontsize."""
    proc = _run([_ffmpeg(), "-y", "-i", srt_path, ass_path])
    if proc.returncode != 0 or not ass_path.exists():
        raise StudioError("Couldn't prepare the subtitles for burning.")
    alignment = _ASS_ALIGNMENT.get(position, 2)
    lines = ass_path.read_text(encoding="utf-8",
                               errors="replace").splitlines()
    fields: List[str] = []
    for i, line in enumerate(lines):
        if line.startswith("Format:") and not fields:
            fields = [f.strip() for f in line.split(":", 1)[1].split(",")]
        elif line.startswith("Style:") and fields:
            values = line.split(":", 1)[1].split(",")
            if len(values) == len(fields):
                if "Alignment" in fields:
                    values[fields.index("Alignment")] = str(alignment)
                if "Fontsize" in fields and font_size:
                    values[fields.index("Fontsize")] = str(int(font_size))
                lines[i] = "Style:" + ",".join(values)
    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def burn_subtitle(item_id: str, stream_index: Optional[int],
                  content: Optional[str], position: str,
                  font_size: int) -> Dict[str, Any]:
    """Hard-burn subtitles into the video (re-encode; long job)."""
    path = _media_path(item_id)
    if path.suffix.lower() in AUDIO_SUFFIXES:
        raise StudioError("Subtitles can only be burned into video files.")
    if content:
        _validate_srt(content)
    elif stream_index is None:
        raise StudioError("Pick a subtitle track (or extract and edit one) "
                          "to burn first.")
    with _job(item_id, "burn"):
        probe = _probe(path)
        duration = _duration_of(probe)
        # Work inside a temp dir with a relative filter filename: this
        # avoids the Windows drive-colon escaping minefield in lavfi.
        with tempfile.TemporaryDirectory(prefix="midas_burn_",
                                         dir=str(path.parent)) as tmpdir:
            tmpdir_path = Path(tmpdir)
            srt = tmpdir_path / "burn.srt"
            if content:
                srt.write_text(content, encoding="utf-8")
            else:
                proc = _run([_ffmpeg(), "-y", "-i", path,
                             "-map", f"0:{stream_index}", "-c:s", "srt",
                             srt])
                if proc.returncode != 0 or not srt.exists():
                    raise StudioError("Couldn't read that subtitle track.")
            ass = tmpdir_path / "burn.ass"
            _srt_to_positioned_ass(srt, ass, position, font_size)
            tmp = _tmp_beside(path)
            try:
                cmd = [_ffmpeg(), "-i", path.resolve(),
                       "-map", "0:v:0", "-map", "0:a?",
                       "-vf", "ass=burn.ass",
                       "-c:v", "libx264", "-crf", "18",
                       "-preset", "veryfast", "-c:a", "copy",
                       tmp.resolve()]
                full = ([str(cmd[0]), "-y", "-nostats", "-loglevel",
                         "error", "-progress", "pipe:1"]
                        + [str(c) for c in cmd[1:]])
                rc, tail = _run_popen_progress(item_id, "burn", full,
                                               duration, cwd=tmpdir)
                if rc != 0 or not tmp.exists():
                    _fail(tail, "Burning the subtitles failed.")
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        _progress(item_id, "burn", 100.0)
        logs.log(f"Burned subtitles into {path.name}", source="studio")
        return {}


def _run_popen_progress(item_id: str, op: str, full: List[str],
                        duration: float,
                        cwd: Optional[str] = None) -> Tuple[int, List[str]]:
    """Popen variant of _run_ffmpeg_progress supporting a working dir."""
    tail: List[str] = []
    last_pub = 0.0
    try:
        proc = subprocess.Popen(full, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace",
                                bufsize=1, cwd=cwd,
                                creationflags=_NO_WINDOW)
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if line.startswith("out_time_ms=") and duration > 0:
                try:
                    done = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                now = time.monotonic()
                if now - last_pub > 0.25:
                    last_pub = now
                    _progress(item_id, op,
                              round(min(done / duration * 100, 99.9), 1))
            elif "=" not in line:
                tail.append(line)
                if len(tail) > 20:
                    tail.pop(0)
        rc = proc.wait()
    except Exception as exc:
        logs.log(f"ffmpeg failed to run: {type(exc).__name__}: "
                 f"{str(exc)[:200]}", level="error", source="studio")
        rc = -1
    return rc, tail


# --------------------------------------------------------------------- trim


def _validate_segments(segments: List[Dict[str, Any]],
                       duration: float) -> List[Tuple[float, float]]:
    if not segments:
        raise StudioError("Add at least one time range first.")
    parsed: List[Tuple[float, float]] = []
    for seg in segments:
        try:
            start = float(seg.get("start_sec", 0))
            end = float(seg.get("end_sec", 0))
        except (TypeError, ValueError):
            raise StudioError("One of the time ranges couldn't be read.")
        if start < 0 or end <= start:
            raise StudioError("Each range needs a start before its end.")
        if duration > 0 and start >= duration:
            raise StudioError("A range starts after the file ends.")
        parsed.append((start, min(end, duration) if duration > 0 else end))
    parsed.sort(key=lambda s: s[0])
    for (a_start, a_end), (b_start, _b_end) in zip(parsed, parsed[1:]):
        if b_start < a_end:
            raise StudioError("The time ranges overlap — merge them first.")
    return parsed


def _encode_args_for(path: Path, is_audio: bool) -> List[str]:
    """Frame-accurate mode re-encodes; pick sane encoders per container."""
    if is_audio:
        codec = {".mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
                 ".m4a": ["-c:a", "aac", "-b:a", "192k"],
                 ".flac": ["-c:a", "flac"],
                 ".opus": ["-c:a", "libopus", "-b:a", "160k"],
                 ".ogg": ["-c:a", "libvorbis", "-q:a", "5"]}
        return codec.get(path.suffix.lower(), ["-c:a", "aac",
                                               "-b:a", "192k"])
    return ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "192k"]


def trim(item_id: str, segments: List[Dict[str, Any]], mode: str,
         keep_original: bool, precise: bool) -> Dict[str, Any]:
    """Keep only the given ranges, or cut them out and join the rest."""
    if mode not in ("keep", "remove"):
        raise StudioError("Unknown trim mode.")
    path = _media_path(item_id)
    row = _history_row(item_id)
    with _job(item_id, "trim"):
        probe = _probe(path)
        duration = _duration_of(probe)
        if duration <= 0:
            raise StudioError("Couldn't read this file's duration.")
        ranges = _validate_segments(segments, duration)

        if mode == "remove":
            keep: List[Tuple[float, float]] = []
            cursor = 0.0
            for start, end in ranges:
                if start - cursor > 0.05:
                    keep.append((cursor, start))
                cursor = max(cursor, end)
            if duration - cursor > 0.05:
                keep.append((cursor, duration))
            if not keep:
                raise StudioError("These cuts would remove the whole file.")
        else:
            keep = ranges

        is_audio = path.suffix.lower() in AUDIO_SUFFIXES
        total = sum(end - start for start, end in keep) or 1.0
        with tempfile.TemporaryDirectory(prefix="midas_trim_",
                                         dir=str(path.parent)) as tmpdir:
            tmpdir_path = Path(tmpdir)
            parts: List[Path] = []
            elapsed = 0.0
            for n, (start, end) in enumerate(keep):
                part = tmpdir_path / f"part{n}{path.suffix}"
                span = (end - start) / total * 100.0
                base = elapsed / total * 100.0
                if precise:
                    cmd = [_ffmpeg(), "-i", path, "-ss", f"{start:.3f}",
                           "-to", f"{end:.3f}", "-map", "0:v:0?",
                           "-map", "0:a?",
                           *_encode_args_for(path, is_audio), part]
                else:
                    cmd = [_ffmpeg(), "-ss", f"{start:.3f}",
                           "-t", f"{end - start:.3f}", "-i", path,
                           "-map", "0", "-map", "-0:t?", "-c", "copy",
                           "-avoid_negative_ts", "make_zero", part]
                rc, tail = _run_ffmpeg_progress(item_id, "trim", cmd,
                                                end - start, base=base,
                                                span=span)
                if (rc != 0 or not part.exists()) and not precise:
                    # Some streams refuse a pure copy cut: drop data/attach
                    # streams and retry once.
                    cmd = [_ffmpeg(), "-ss", f"{start:.3f}",
                           "-t", f"{end - start:.3f}", "-i", path,
                           "-map", "0:v:0?", "-map", "0:a?", "-c", "copy",
                           "-avoid_negative_ts", "make_zero", part]
                    rc, tail = _run_ffmpeg_progress(item_id, "trim", cmd,
                                                    end - start, base=base,
                                                    span=span)
                if rc != 0 or not part.exists():
                    _fail(tail, "Cutting this file failed. Try the "
                                "frame-accurate mode.")
                parts.append(part)
                elapsed += end - start

            if len(parts) == 1:
                result = parts[0]
            else:
                listing = tmpdir_path / "concat.txt"
                listing.write_text(
                    "\n".join("file '" + p.name.replace("'", r"'\''")
                              + "'" for p in parts) + "\n",
                    encoding="utf-8")
                result = tmpdir_path / f"joined{path.suffix}"
                proc = _run([_ffmpeg(), "-y", "-f", "concat", "-safe", "0",
                             "-i", listing, "-map", "0", "-c", "copy",
                             result])
                if proc.returncode != 0 or not result.exists():
                    for ln in (proc.stderr or "").splitlines()[-3:]:
                        logs.log(ln[:300], level="error", source="studio")
                    raise StudioError("Joining the kept parts failed.")

            if keep_original:
                out = _unique_path(path.with_name(
                    f"{path.stem} (trimmed){path.suffix}"))
                os.replace(result, out)
                _new_history_row(row, out, audio_only=is_audio)
            else:
                out = path
                os.replace(result, path)
        _progress(item_id, "trim", 100.0)
        logs.log(f"Trimmed {path.name}", source="studio")
        return {"file_path": str(out)}


# --------------------------------------------------------------------- crop


def _video_dimensions(probe: Dict[str, Any]) -> Tuple[int, int]:
    """Width/height of the real video stream (attached pics don't count)."""
    for s in probe.get("streams", []):
        if s.get("codec_type") != "video":
            continue
        if (s.get("disposition") or {}).get("attached_pic") == 1:
            continue
        w, h = int(s.get("width") or 0), int(s.get("height") or 0)
        if w > 0 and h > 0:
            return w, h
    raise StudioError("Couldn't read this file's video dimensions.")


def _crop_rect(left: Any, top: Any, right: Any, bottom: Any,
               src_w: int, src_h: int) -> Tuple[int, int, int, int]:
    """Fractional selection (0..1 each side) -> even-sized pixel rect."""
    try:
        left, top = float(left), float(top)
        right, bottom = float(right), float(bottom)
    except (TypeError, ValueError):
        raise StudioError("The crop values couldn't be read.")
    for v in (left, top, right, bottom):
        if v != v or not 0.0 <= v <= 1.0:   # NaN / out of range
            raise StudioError("Crop values must be between 0 and 1.")
    if right - left < 0.02 or bottom - top < 0.02:
        raise StudioError("The selected area is too small to crop.")
    x = int(round(left * src_w))
    y = int(round(top * src_h))
    w = int(round((right - left) * src_w))
    h = int(round((bottom - top) * src_h))
    # x264 wants even offsets/sizes, and the rect must stay in the frame.
    x -= x % 2
    y -= y % 2
    w = min(w, src_w - x)
    h = min(h, src_h - y)
    w -= w % 2
    h -= h % 2
    if w < 16 or h < 16:
        raise StudioError("The selected area is too small to crop.")
    if x == 0 and y == 0 and w >= src_w - 1 and h >= src_h - 1:
        raise StudioError("The selection still covers the whole frame - "
                          "drag the sliders to pick the part to keep.")
    return x, y, w, h


def get_frame(item_id: str, at_sec: float) -> Tuple[bytes, str]:
    """One poster frame (JPEG, <=960px wide) for the crop preview."""
    path = _media_path(item_id)
    if path.suffix.lower() in AUDIO_SUFFIXES:
        raise StudioError("This is an audio file - there's no video frame.")
    probe = _probe(path)
    duration = _duration_of(probe)
    try:
        at = float(at_sec or 0.0)
    except (TypeError, ValueError):
        at = 0.0
    if at != at or at < 0:
        at = 0.0
    if duration > 0:
        at = min(at, max(duration - 0.1, 0.0))
    with tempfile.TemporaryDirectory(prefix="midas_frame_") as tmpdir:
        out = Path(tmpdir) / "frame.jpg"
        proc = _run([_ffmpeg(), "-y", "-ss", f"{at:.3f}", "-i", path,
                     "-map", "0:v:0", "-frames:v", "1",
                     "-vf", "scale=min(960\\,iw):-2", "-q:v", "4", out])
        if proc.returncode != 0 or not out.exists() \
                or out.stat().st_size == 0:
            for ln in (proc.stderr or "").splitlines()[-3:]:
                logs.log(ln[:300], level="error", source="studio")
            raise StudioError("Couldn't extract a preview frame.")
        return out.read_bytes(), "image/jpeg"


def crop(item_id: str, left: float, top: float, right: float, bottom: float,
         keep_original: bool) -> Dict[str, Any]:
    """Crop the picture to the selected region ("trim the video size").

    The selection arrives as fractions of the frame so the UI never needs to
    know the exact source resolution. Video is re-encoded (cropping can't be
    a stream copy); audio is copied untouched whenever the container allows.
    """
    path = _media_path(item_id)
    if path.suffix.lower() in AUDIO_SUFFIXES:
        raise StudioError("Cropping only works on video files.")
    row = _history_row(item_id)
    with _job(item_id, "crop"):
        probe = _probe(path)
        duration = _duration_of(probe)
        src_w, src_h = _video_dimensions(probe)
        x, y, w, h = _crop_rect(left, top, right, bottom, src_w, src_h)

        # WebM can't hold H.264 - those sources come out as .mp4 instead.
        suffix = path.suffix.lower()
        container_change = suffix == ".webm"
        out_suffix = ".mp4" if container_change else path.suffix
        audio_args = (["-c:a", "aac", "-b:a", "192k"] if container_change
                      else ["-c:a", "copy"])

        out = _unique_path(
            path.with_name(f"{path.stem} (cropped){out_suffix}"))
        tmp = _tmp_beside(out)
        try:
            cmd = [_ffmpeg(), "-i", path,
                   "-map", "0:v:0", "-map", "0:a?",
                   "-map_metadata", "0",
                   "-vf", f"crop={w}:{h}:{x}:{y}",
                   "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                   *audio_args, tmp]
            rc, tail = _run_ffmpeg_progress(item_id, "crop", cmd, duration)
            if (rc != 0 or not tmp.exists()) and not container_change:
                # Odd audio codec for this container: re-encode audio too.
                cmd = [_ffmpeg(), "-i", path,
                       "-map", "0:v:0", "-map", "0:a?",
                       "-map_metadata", "0",
                       "-vf", f"crop={w}:{h}:{x}:{y}",
                       "-c:v", "libx264", "-crf", "18",
                       "-preset", "veryfast",
                       "-c:a", "aac", "-b:a", "192k", tmp]
                rc, tail = _run_ffmpeg_progress(item_id, "crop", cmd,
                                                duration)
            if rc != 0 or not tmp.exists():
                _fail(tail, "Cropping this video failed. The file may use "
                            "an unusual codec.")
            if keep_original or container_change:
                os.replace(tmp, out)
                _new_history_row(row, out, audio_only=False)
                if not keep_original:
                    try:
                        path.unlink()
                        row["file_path"] = None
                        history.upsert(row)
                    except OSError:
                        logs.log(f"Couldn't delete the original file "
                                 f"{path.name}", level="error",
                                 source="studio")
            else:
                out = path
                os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        _progress(item_id, "crop", 100.0)
        logs.log(f"Cropped {path.name} to {w}x{h}", source="studio")
        return {"file_path": str(out)}

