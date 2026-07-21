"""Universal link analysis: platform + content-type detection + preview data."""
import json
import re
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from .. import config
from ..settings import load as load_settings
from . import logs, spotify

_NO_WINDOW = 0x08000000 if config.IS_WINDOWS else 0

_DPAPI_MESSAGE = (
    "Couldn't read your browser's cookies (Windows blocked the decryption). "
    "Close the browser fully and retry, pick Firefox under Settings > "
    "Cookies from browser, or put an exported cookies.txt file in Midas's "
    "data folder.")

PLATFORM_PATTERNS = [
    ("youtube", re.compile(r"(youtube\.com|youtu\.be)", re.I)),
    ("spotify", re.compile(r"open\.spotify\.com", re.I)),
    ("soundcloud", re.compile(r"soundcloud\.com", re.I)),
    ("instagram", re.compile(r"instagram\.com", re.I)),
    ("tiktok", re.compile(r"tiktok\.com", re.I)),
    ("reddit", re.compile(r"(reddit\.com|redd\.it)", re.I)),
]


# In-memory cache of successful analyses (SPEED). Re-analyzing the same
# link (clipboard re-copies, manual edits that are undone, playlist picker
# round-trips) is served instantly instead of spawning yt-dlp again.
_CACHE_TTL = 600.0   # seconds
_CACHE_MAX = 64      # entries
_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _cache_get(url: str) -> Optional[Dict[str, Any]]:
    with _cache_lock:
        hit = _cache.get(url)
        if hit is None:
            return None
        stamp, payload = hit
        if time.monotonic() - stamp > _CACHE_TTL:
            _cache.pop(url, None)
            return None
        return dict(payload)


def _cache_put(url: str, payload: Dict[str, Any]) -> None:
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
        _cache[url] = (time.monotonic(), dict(payload))


def detect_platform(url: str) -> Optional[str]:
    for name, pattern in PLATFORM_PATTERNS:
        if pattern.search(url):
            return name
    return None


def _youtube_has_both(url: str) -> bool:
    """True for watch?v=...&list=... URLs (single video inside a playlist)."""
    q = parse_qs(urlparse(url).query)
    return bool(q.get("v")) and bool(q.get("list"))


def _cookie_args() -> List[str]:
    """Cookie flags for yt-dlp, matching the downloader's behavior.

    An exported data\\cookies.txt always wins: it bypasses browser cookie
    decryption entirely, which is the reliable escape hatch when Chrome/Edge
    block DPAPI decryption of their cookie store (yt-dlp issue #10927).
    """
    cookie_file = config.DATA_DIR / "cookies.txt"
    if cookie_file.exists():
        return ["--cookies", str(cookie_file)]
    # Use the same cookies as downloads, otherwise private / login-required
    # links (e.g. Instagram) keep failing analysis even after the user picks
    # a browser in Settings.
    cookies = load_settings().cookies_from_browser
    if cookies:
        return ["--cookies-from-browser", cookies]
    return []


def _is_cookie_decrypt_error(raw: str) -> bool:
    low = (raw or "").lower()
    return "dpapi" in low or "failed to decrypt" in low


def _analysis_flags(url: str, platform: Optional[str]) -> List[str]:
    """yt-dlp flags tuned for fast previews (SPEED).

    - --socket-timeout keeps one slow CDN edge from stalling the whole run.
    - YouTube: skip HLS/DASH manifest fetches - previews only need metadata,
      and the downloader re-resolves formats on its own anyway.
    - Instagram/TikTok/Reddit: full extraction. Posts are single items (or
      tiny carousels) and the clip picker needs the real duration, which
      flat extraction omits.
    - Everything else (including SoundCloud sets) uses one cheap flat
      request. Deeper per-track set analysis was tried and reverted: it
      triggers SoundCloud's rate limiter (HTTP 403) and can block the
      user's connection for a while. Missing set titles fall back to
      names derived from the track URLs (_entry_title).
    """
    flags = ["--no-warnings", "--socket-timeout", "10"]
    if platform in ("instagram", "tiktok", "reddit"):
        flags += ["--playlist-items", "1-100"]
    else:
        flags += ["--flat-playlist", "--playlist-items", "1-2000"]
    if platform == "youtube":
        flags += ["--extractor-args", "youtube:skip=hls,dash,translated_subs"]
    return flags


def _run_ytdlp_json(url: str, timeout: int = 60,
                    flags: Optional[List[str]] = None,
                    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Returns (info, None) on success, or (None, friendly_error_message).

    Raw yt-dlp error lines are pushed to the developer log so the log panel
    always shows the real reason an analysis failed. If the browser cookies
    can't be decrypted (DPAPI), analysis is retried once without cookies so
    public links keep working.
    """
    ytdlp = config.resolve_tool("yt-dlp")
    if ytdlp is None:
        return None, ("yt-dlp isn't installed yet. Open Settings > "
                      "Dependencies and click Install.")
    if flags is None:
        flags = ["--flat-playlist", "--no-warnings",
                 "--playlist-items", "1-2000"]
    base = [str(ytdlp), "-J"] + list(flags)
    cookie_args = _cookie_args()
    # The cookie-less attempt is only reached when the first attempt fails
    # because the browser cookies couldn't be decrypted (DPAPI).
    attempts: List[List[str]] = [cookie_args]
    if cookie_args:
        attempts.append([])
    raw = ""
    cookie_decrypt_failed = False
    for attempt_no, extra in enumerate(attempts):
        cmd = base + extra + [url]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, creationflags=_NO_WINDOW,
                                  encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return None, "The site took too long to respond. Try again in a moment."
        except Exception as exc:
            return None, f"Couldn't run yt-dlp ({type(exc).__name__})."
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                return json.loads(proc.stdout), None
            except Exception:
                return None, ("yt-dlp returned data Midas couldn't read. "
                              "Try updating yt-dlp in Settings.")
        raw = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
        if (attempt_no == 0 and len(attempts) > 1
                and _is_cookie_decrypt_error(raw)):
            cookie_decrypt_failed = True
            for ln in raw.splitlines()[-3:]:
                if ln.strip():
                    logs.log(ln.strip()[:300], level="error", source="yt-dlp")
            logs.log("Browser cookies couldn't be decrypted (DPAPI); "
                     "retrying without cookies.", source="analyzer")
            continue
        break
    for ln in raw.splitlines()[-3:]:
        if ln.strip():
            logs.log(ln.strip()[:300], level="error", source="yt-dlp")
    msg = _friendly_ytdlp_error(raw)
    if cookie_decrypt_failed and any(
            k in msg.lower() for k in ("sign-in", "sign in", "login",
                                       "cookies")):
        # The real blocker is the unreadable cookies, not the sign-in
        # prompt that the cookie-less retry ran into.
        msg = _DPAPI_MESSAGE
    return None, msg


def _friendly_ytdlp_error(output: str) -> str:
    """Turn raw yt-dlp stderr into a short, human error message."""
    lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
    err = next((ln for ln in reversed(lines) if "ERROR" in ln.upper()),
               lines[-1] if lines else "")
    low = err.lower()
    if "dpapi" in low or "failed to decrypt" in low:
        return _DPAPI_MESSAGE
    if "not a bot" in low or ("sign in" in low and "confirm" in low):
        return ("YouTube wants a sign-in check for this link. Pick your "
                "browser under Settings > Cookies from browser, then retry.")
    if "403" in low or "forbidden" in low:
        return ("The site refused the request (HTTP 403) - it is probably "
                "rate-limiting after many requests. Wait a minute and try "
                "again; if it keeps happening, update yt-dlp in Settings.")
    if any(k in low for k in ("login", "cookies", "rate-limit", "rate limit",
                              "private", "age-restricted", "sign in",
                              "not available", "restricted")):
        return ("This content needs a login (common for Instagram). Pick your "
                "browser under Settings > Cookies from browser, then retry.")
    if "unsupported url" in low:
        return "yt-dlp doesn't support this exact link - try the post's main URL."
    if any(k in low for k in ("getaddrinfo", "network", "timed out",
                              "connection", "resolve")):
        return "Network problem while contacting the site. Check your connection."
    return err[:180] if err else "Couldn't analyze this link."


def _best_thumbnail(info: Dict[str, Any]) -> Optional[str]:
    """A thumbnail sized for the preview card (SPEED).

    The card renders at ~380 physical px, so the smallest image that is
    still >= 480px wide is preferred. The original poster (often 4K, 10-20x
    heavier) used to delay the card visibly on every analysis.
    """
    thumbs = [t for t in (info.get("thumbnails") or [])
              if isinstance(t, dict) and t.get("url")]
    sized = sorted((t for t in thumbs if t.get("width")),
                   key=lambda t: t["width"])
    for t in sized:
        if t["width"] >= 480:
            return t["url"]
    if sized:
        return sized[-1]["url"]
    if info.get("thumbnail"):
        return info["thumbnail"]
    if thumbs:
        return thumbs[-1].get("url")
    entries = info.get("entries") or []
    if entries and isinstance(entries[0], dict):
        return _best_thumbnail(entries[0])
    return None


_SLUG_SEP = re.compile(r"[-_+]+")


def _slug_name(entry: Dict[str, Any]) -> Optional[str]:
    """Readable name derived from the entry's URL slug, if it has one.

    API-style URLs (…/tracks/303984674) carry no words and yield None.
    """
    for key in ("webpage_url", "url"):
        raw = entry.get(key)
        if not isinstance(raw, str) or not raw.startswith("http"):
            continue
        segment = unquote(urlparse(raw).path).rstrip("/").rsplit("/", 1)[-1]
        segment = _SLUG_SEP.sub(" ", segment).strip()
        if segment and not segment.isdigit():
            return segment
    return None


def _entry_title(entry: Dict[str, Any], index: int) -> str:
    """Readable playlist-entry name; never a bare "Item N" when avoidable.

    Flat-playlist entries (SoundCloud sets especially) may omit titles, so a
    name is derived from the track URL slug before falling back.
    """
    title = str(entry.get("title") or "").strip()
    if title:
        return title
    return _slug_name(entry) or f"Item {index + 1}"


def analyze(url: str) -> Dict[str, Any]:
    """Returns a preview payload, or {'error': friendly_message}."""
    url = url.strip()
    cached = _cache_get(url)
    if cached is not None:
        logs.log(f"Analyze cache hit: {url}", source="analyzer")
        return cached
    logs.log(f"Analyzing {url}", source="analyzer")
    platform = detect_platform(url)
    if not platform:
        logs.log(f"Unsupported link: {url}", level="error", source="analyzer")
        return {"error": "unsupported",
                "message": "This link doesn't look like a supported platform."}

    if platform == "spotify":
        info = spotify.analyze(url)
        if not info:
            logs.log(f"Couldn't read Spotify link: {url}",
                     level="error", source="analyzer")
            return {"error": "invalid",
                    "message": "Couldn't read this Spotify link."}
        _cache_put(url, info)
        return info

    info, err = _run_ytdlp_json(url, flags=_analysis_flags(url, platform))
    if info is None:
        logs.log(f"Analyze failed: {err}", level="error", source="analyzer")
        return {"error": "unreachable", "message": err}

    is_playlist = info.get("_type") == "playlist"
    entries = info.get("entries") or []
    kind = "single"
    if is_playlist:
        kind = "both" if (platform == "youtube" and _youtube_has_both(url)) else "playlist"

    logs.log(f"Analyzed OK: {info.get('title') or url}", source="analyzer")
    payload = {
        "platform": platform,
        "kind": kind,
        "content_type": "playlist" if is_playlist else "single",
        "title": info.get("title") or "Untitled",
        "author": info.get("uploader") or info.get("channel")
                  or info.get("uploader_id") or "",
        "thumbnail": _best_thumbnail(info),
        # Single-entry results (e.g. an Instagram post extracted as a
        # one-item carousel) carry the duration on the entry, not the
        # wrapper - without it the clip/section picker never shows up.
        "duration": info.get("duration") or (
            (entries[0] or {}).get("duration")
            if len(entries) == 1 else None),
        "count": len(entries) if is_playlist else 1,
        # Per-entry data for the playlist picker (TASK 7). The flat-playlist
        # JSON already carries it; capped at 2000 like the fetch above.
        "entries": [
            {
                "index": i + 1,
                "id": (e or {}).get("id"),
                "title": _entry_title(e or {}, i),
                "duration": (e or {}).get("duration"),
                "url": (e or {}).get("webpage_url") or (e or {}).get("url"),
            }
            for i, e in enumerate(entries[:2000])
        ] if is_playlist else [],
        "url": url,
    }
    _cache_put(url, payload)
    return payload
