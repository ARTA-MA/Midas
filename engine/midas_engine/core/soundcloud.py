"""Key-less SoundCloud metadata extraction (real track names for sets).

Why this module exists
----------------------
Analysis asks yt-dlp for a *flat* playlist (one cheap request), because
deep per-track extraction of a SoundCloud set trips SoundCloud's rate
limiter (HTTP 403) and can block the user's connection for a while.
Flat entries, however, carry **no titles at all** for SoundCloud sets:

    {"_type": "url", "url": "https://api-v2.soundcloud.com/tracks/1625800446",
     "title": null, ...}

so every row in the playlist picker used to read "Item 1", "Item 2", ...

This module fills in the real names using the two public, key-less
surfaces the SoundCloud web player itself uses:

  1. ``window.__sc_hydration`` on the set page -> the FULL ordered track
     list (ids for every track, titles for the handful SoundCloud
     pre-hydrates), plus the set title / owner / artwork / track count.
  2. ``api-v2.soundcloud.com/tracks?ids=...`` -> titles, artists and
     durations for up to 50 ids per request, using the anonymous
     ``client_id`` scraped live from the page's own JS bundles.

Two requests are enough for a 100-track set, so analysis stays fast and
well under SoundCloud's rate limits. Everything degrades gracefully: any
failure returns ``None`` and the caller keeps its previous behaviour.
"""
import json
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from . import logs

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}
_API = "https://api-v2.soundcloud.com"
_BATCH = 50                     # ids per /tracks request (API limit)
_CACHE_TTL = 900.0              # 15 minutes, like the Spotify caches
_CACHE_MAX = 32

_SET_RE = re.compile(r"soundcloud\.com/[^/]+/sets/", re.I)
_HYDRATION_RE = re.compile(
    r"window\.__sc_hydration\s*=\s*(\[.*?\]);", re.DOTALL)
_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="(https://[^"]+\.js)"')
_CLIENT_ID_RES = (
    re.compile(r'client_id\s*:\s*"([0-9a-zA-Z]{32})"'),
    re.compile(r'client_id=([0-9a-zA-Z]{32})'),
    re.compile(r'"clientId"\s*:\s*"([0-9a-zA-Z]{32})"'),
)

_PLAYLIST_CACHE: Dict[str, Any] = {}
_CLIENT_ID_CACHE: Dict[str, Any] = {"id": None, "checked": 0.0}


class _Deadline:
    """Hard wall-clock budget: metadata enrichment must never hang the UI."""

    def __init__(self, seconds: float):
        self._until = time.monotonic() + seconds

    def expired(self) -> bool:
        return time.monotonic() >= self._until


def is_playlist_url(url: str) -> bool:
    """True for /sets/ links (SoundCloud playlists and albums)."""
    return bool(_SET_RE.search(url or ""))


def _get(url: str, **kw: Any) -> Optional[httpx.Response]:
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=kw.pop("timeout", 20),
                         follow_redirects=True, **kw)
        return resp
    except Exception as exc:
        logs.log("SoundCloud request failed: " + str(exc)[:120],
                 level="warning", source="soundcloud")
        return None


def _client_id(html: str, deadline: Optional[_Deadline] = None
               ) -> Optional[str]:
    """Anonymous client_id, scraped from the page's own JS bundles.

    SoundCloud rotates it every few weeks, so it can never be hardcoded;
    it is cached for 15 minutes once found.
    """
    cached = _CLIENT_ID_CACHE.get("id")
    if cached and time.time() - _CLIENT_ID_CACHE["checked"] < _CACHE_TTL:
        return cached
    for pattern in _CLIENT_ID_RES:
        m = pattern.search(html or "")
        if m:
            _CLIENT_ID_CACHE.update({"id": m.group(1), "checked": time.time()})
            return m.group(1)
    # The id lives in the last-loaded bundle, so walk the scripts backwards.
    for src in reversed(_SCRIPT_SRC_RE.findall(html or "")):
        if deadline is not None and deadline.expired():
            break
        resp = _get(src, timeout=15)
        if resp is None or resp.status_code != 200:
            continue
        for pattern in _CLIENT_ID_RES:
            m = pattern.search(resp.text)
            if m:
                _CLIENT_ID_CACHE.update({"id": m.group(1),
                                         "checked": time.time()})
                return m.group(1)
    if cached:
        return cached          # stale is better than nothing
    logs.log("SoundCloud: no anonymous client_id found in the page bundles.",
             level="warning", source="soundcloud")
    return None


def _hydration(html: str) -> List[Dict[str, Any]]:
    m = _HYDRATION_RE.search(html or "")
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except Exception:
        return []
    return [d for d in data if isinstance(d, dict)]


def _hydrated(html: str, kind: str) -> Optional[Dict[str, Any]]:
    for block in _hydration(html):
        if block.get("hydratable") == kind and isinstance(block.get("data"),
                                                          dict):
            return block["data"]
    return None


def _artwork(entity: Dict[str, Any]) -> Optional[str]:
    art = entity.get("artwork_url") or (entity.get("user") or {}).get(
        "avatar_url")
    if isinstance(art, str) and art:
        # -large is 100x100; -t500x500 is the biggest generally available.
        return art.replace("-large.", "-t500x500.")
    return None


def _username(track: Dict[str, Any]) -> str:
    user = track.get("user")
    if isinstance(user, dict):
        name = user.get("username") or user.get("permalink")
        if isinstance(name, str):
            return name
    name = track.get("publisher_metadata")
    if isinstance(name, dict) and isinstance(name.get("artist"), str):
        return name["artist"]
    return ""


def _duration_sec(track: Dict[str, Any]) -> Optional[int]:
    raw = track.get("full_duration") or track.get("duration")
    try:
        return int(int(raw) / 1000) or None
    except (TypeError, ValueError):
        return None


def _resolve_ids(ids: List[int], client_id: str,
                 deadline: Optional[_Deadline] = None
                 ) -> Dict[int, Dict[str, Any]]:
    """Titles/artists/durations for track ids, 50 per request."""
    out: Dict[int, Dict[str, Any]] = {}
    for start in range(0, len(ids), _BATCH):
        if deadline is not None and deadline.expired():
            logs.log("SoundCloud: time budget reached while resolving track "
                     "names; keeping " + str(len(out)) + " resolved name(s).",
                     level="warning", source="soundcloud")
            break
        batch = ids[start:start + _BATCH]
        resp = _get(_API + "/tracks", params={
            "ids": ",".join(str(i) for i in batch),
            "client_id": client_id,
            "app_locale": "en",
        })
        if resp is None:
            break
        if resp.status_code != 200:
            logs.log("SoundCloud /tracks answered " + str(resp.status_code)
                     + "; keeping the names resolved so far.",
                     level="warning", source="soundcloud")
            break
        try:
            rows = resp.json()
        except Exception:
            break
        if not isinstance(rows, list):
            break
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("id"), int):
                out[row["id"]] = row
    return out


def _cache_get(url: str) -> Optional[Dict[str, Any]]:
    hit = _PLAYLIST_CACHE.get(url)
    if not hit:
        return None
    stamp, payload = hit
    if time.monotonic() - stamp > _CACHE_TTL:
        _PLAYLIST_CACHE.pop(url, None)
        return None
    return payload


def _cache_put(url: str, payload: Dict[str, Any]) -> None:
    if len(_PLAYLIST_CACHE) >= _CACHE_MAX:
        oldest = min(_PLAYLIST_CACHE, key=lambda k: _PLAYLIST_CACHE[k][0])
        _PLAYLIST_CACHE.pop(oldest, None)
    _PLAYLIST_CACHE[url] = (time.monotonic(), payload)


def playlist(url: str, budget: float = 25.0) -> Optional[Dict[str, Any]]:
    """Full ordered track list of a SoundCloud set, with REAL names.

    Returns ``None`` (never raises) when the set page or the public API
    can't be read, so callers can fall back to their previous behaviour.

    Shape::

        {"title": str, "author": str, "thumbnail": str | None,
         "track_count": int,
         "tracks": [{"id": int, "title": str, "artist": str,
                     "duration": int | None, "url": str | None}, ...]}
    """
    if not url:
        return None
    cached = _cache_get(url)
    if cached is not None:
        return cached
    deadline = _Deadline(budget)
    resp = _get(url, timeout=20)
    if resp is None or resp.status_code != 200:
        if resp is not None:
            logs.log("SoundCloud set page answered " + str(resp.status_code)
                     + ".", level="warning", source="soundcloud")
        return None
    html = resp.text
    entity = _hydrated(html, "playlist")
    if not entity:
        logs.log("SoundCloud: no playlist data on the set page.",
                 level="warning", source="soundcloud")
        return None

    rows = [t for t in (entity.get("tracks") or []) if isinstance(t, dict)]
    ids = [t["id"] for t in rows if isinstance(t.get("id"), int)]
    missing = [t["id"] for t in rows
               if isinstance(t.get("id"), int) and not t.get("title")]
    resolved: Dict[int, Dict[str, Any]] = {}
    if missing:
        client_id = _client_id(html, deadline)
        if client_id:
            resolved = _resolve_ids(missing, client_id, deadline)

    tracks: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        tid = row.get("id") if isinstance(row.get("id"), int) else None
        full = resolved.get(tid) if tid is not None else None
        merged = dict(row)
        if isinstance(full, dict):
            merged.update({k: v for k, v in full.items() if v is not None})
        title = merged.get("title")
        tracks.append({
            "id": tid,
            "title": title if isinstance(title, str) and title.strip()
                     else "",
            "artist": _username(merged),
            "duration": _duration_sec(merged),
            "url": merged.get("permalink_url"),
            # This track's OWN cover, never the set's. The downloaded file
            # already gets the right artwork, so the app must be able to
            # show the same thing instead of the playlist icon (BUG 7).
            "thumbnail": _artwork(merged),
        })

    named = sum(1 for t in tracks if t["title"])
    payload = {
        "title": entity.get("title") or "",
        "author": _username(entity),
        "thumbnail": _artwork(entity),
        "track_count": (entity.get("track_count")
                        if isinstance(entity.get("track_count"), int)
                        else len(tracks)),
        "tracks": tracks,
    }
    logs.log("SoundCloud set \"" + str(payload["title"])[:60] + "\": "
             + str(len(tracks)) + " track(s), " + str(named)
             + " real name(s) resolved.", source="soundcloud")
    if named:
        _cache_put(url, payload)
    return payload


def track_names(url: str, budget: float = 25.0) -> Dict[str, str]:
    """Lookup table of real names for a set: track id AND permalink -> name.

    Keys are strings so callers can match yt-dlp entries by ``id`` or by
    URL without caring which shape SoundCloud handed back.
    """
    return _lookup(url, "title", budget)


def track_art(url: str, budget: float = 25.0) -> Dict[str, str]:
    """Lookup table of each track's OWN cover art for a set.

    Same key shapes as :func:`track_names` (track id, permalink with and
    without a query string), so the analyzer and the download queue can
    show a track's real artwork instead of the set's icon (BUG 7).
    """
    return _lookup(url, "thumbnail", budget)


def _lookup(url: str, field: str, budget: float = 25.0) -> Dict[str, str]:
    """id/permalink -> one field of every track in a set (shared by the
    name and artwork lookups; the set page is fetched once and cached)."""
    data = playlist(url, budget)
    if not data:
        return {}
    found: Dict[str, str] = {}
    for track in data["tracks"]:
        value = track.get(field)
        if not isinstance(value, str) or not value:
            continue
        if track["id"] is not None:
            found[str(track["id"])] = value
        permalink = track.get("url")
        if isinstance(permalink, str) and permalink:
            found[permalink.rstrip("/")] = value
            found[permalink.rstrip("/").split("?")[0]] = value
    return found
