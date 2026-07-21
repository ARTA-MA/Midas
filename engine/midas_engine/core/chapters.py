"""Description-timestamp fallback chapters.

yt-dlp --embed-chapters covers native YouTube chapters. When a video has no
native chapters but its description contains a timestamp list, we parse it
and inject chapters with ffmpeg (FFMETADATA), codec-copy so it is instant.
"""
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from .. import config

_NO_WINDOW = 0x08000000 if config.IS_WINDOWS else 0

# Matches "0:00 Intro", "[12:34] - Part two", "1:02:03 Finale" etc.
_TS_LINE = re.compile(
    r"^\W{0,3}(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\W{0,3}\s+(.{2,100})$")


def parse_description(description: str) -> List[Tuple[int, str]]:
    chapters: List[Tuple[int, str]] = []
    for line in (description or "").splitlines():
        m = _TS_LINE.match(line.strip())
        if not m:
            continue
        h, mnt, sec, title = m.groups()
        start = (int(h or 0) * 3600) + int(mnt) * 60 + int(sec)
        chapters.append((start, title.strip(" -–—:|")))
    chapters.sort(key=lambda c: c[0])
    # A real chapter list starts at 0:00 and has at least 2 entries.
    if len(chapters) < 2 or chapters[0][0] != 0:
        return []
    return chapters


def _ffmetadata(chapters: List[Tuple[int, str]], total: Optional[int]) -> str:
    lines = [";FFMETADATA1"]
    for i, (start, title) in enumerate(chapters):
        end = chapters[i + 1][0] if i + 1 < len(chapters) else (total or start + 1)
        safe = title.replace("\\", "\\\\").replace("=", "\\=").replace(
            ";", "\\;").replace("#", "\\#").replace("\n", " ")
        lines += ["[CHAPTER]", "TIMEBASE=1/1", f"START={start}",
                  f"END={end}", f"title={safe}"]
    return "\n".join(lines) + "\n"


def inject(file_path: Path, chapters: List[Tuple[int, str]],
           duration: Optional[int]) -> bool:
    """Codec-copy remux adding chapters. Returns True on success."""
    ffmpeg = config.resolve_tool("ffmpeg")
    if not chapters or ffmpeg is None:
        return False
    if file_path.suffix.lower() not in (".mp4", ".m4a", ".mkv", ".webm", ".mov"):
        return False
    meta = file_path.with_suffix(".ffmeta.txt")
    tmp = file_path.with_name(file_path.stem + ".chapters" + file_path.suffix)
    try:
        meta.write_text(_ffmetadata(chapters, duration), encoding="utf-8")
        cmd = [str(ffmpeg), "-y", "-i", str(file_path),
               "-i", str(meta), "-map_metadata", "0", "-map_chapters", "1",
               "-codec", "copy", str(tmp)]
        proc = subprocess.run(cmd, capture_output=True, timeout=300,
                              creationflags=_NO_WINDOW)
        if proc.returncode == 0 and tmp.exists():
            tmp.replace(file_path)
            return True
        return False
    except Exception:
        return False
    finally:
        meta.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
