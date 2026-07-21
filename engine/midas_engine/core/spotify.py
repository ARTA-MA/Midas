"""Key-less Spotify metadata extraction (no official Web API).

Strategy (same approach as spotdl's metadata layer / public scrapers):
  1. oEmbed endpoint  -> title + cover thumbnail (very stable, no auth)
  2. open.spotify.com/embed/<type>/<id> page -> inline JSON with artists,
     album name, duration and full track lists for albums/playlists.
Both are public endpoints that need no API keys. All parsing is defensive:
if Spotify changes markup we degrade gracefully to oEmbed-only metadata.
"""
import json
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from . import logs

_URL_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[\w-]+/)?(track|album|playlist|artist)/([A-Za-z0-9]+)")
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
# Some embed-page variants only ship for mobile clients (BUG 3 fallback).
_MOBILE_HEADERS = {"User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 "
                                  "like Mac OS X) AppleWebKit/605.1.15 "
                                  "(KHTML, like Gecko) Mobile/15E148")}


def parse_url(url: str) -> Optional[Dict[str, str]]:
    m = _URL_RE.search(url)
    return {"kind": m.group(1), "id": m.group(2)} if m else None


def _oembed(url: str) -> Dict[str, Any]:
    try:
        r = httpx.get("https://open.spotify.com/oembed", params={"url": url},
                      headers=_HEADERS, timeout=15)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def _embed_json(kind: str, sid: str,
                headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Fetch the embed page and pull out its inline JSON blob."""
    try:
        r = httpx.get("https://open.spotify.com/embed/" + kind + "/" + sid,
                      headers=headers or _HEADERS, timeout=20,
                      follow_redirects=True)
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                      r.text, re.DOTALL)
        return json.loads(m.group(1)) if m else {}
    except Exception:
        return {}


def _find_key(obj: Any, key: str) -> Optional[Any]:
    """Depth-first search for the first occurrence of `key` in nested JSON."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def _artists_of(entity: Dict[str, Any]) -> str:
    artists = entity.get("artists") or []
    if isinstance(artists, dict):
        artists = artists.get("items") or []
    names: List[str] = []
    for a in artists:
        if not isinstance(a, dict):
            continue
        name = a.get("name")
        if not name and isinstance(a.get("profile"), dict):
            name = a["profile"].get("name")
        if name:
            names.append(name)
    return ", ".join(names)


def _cover_of(entity: Dict[str, Any]) -> Optional[str]:
    cover = entity.get("coverArt") or entity.get("images") or {}
    sources = cover.get("sources") if isinstance(cover, dict) else cover
    if isinstance(sources, list) and sources:
        best = max(sources, key=lambda s: s.get("width") or 0)
        return best.get("url")
    return None


def _duration_sec(t: Dict[str, Any]) -> Optional[int]:
    """Track length in seconds from any known duration shape."""
    raw = t.get("duration") or t.get("trackDuration")
    if isinstance(raw, dict):
        raw = raw.get("totalMilliseconds") or raw.get("milliseconds")
    if raw is None:
        raw = t.get("duration_ms") or t.get("durationMs")
    try:
        return int(int(raw) / 1000) or None
    except (TypeError, ValueError):
        return None


def _unwrap_track(entry: Any) -> Any:
    """PlaylistV2-style rows wrap the track under itemV2.data / track."""
    if isinstance(entry, dict):
        for key in ("itemV2", "item"):
            inner = entry.get(key)
            if isinstance(inner, dict):
                entry = (inner.get("data")
                         if isinstance(inner.get("data"), dict) else inner)
        if isinstance(entry, dict) and isinstance(entry.get("track"), dict):
            entry = entry["track"]
    return entry


def _clean_tracklist(value: Any) -> List[Dict[str, Any]]:
    """Keep only dict entries that look like Spotify track objects."""
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    cleaned: List[Dict[str, Any]] = []
    for entry in value:
        entry = _unwrap_track(entry)
        if isinstance(entry, dict) and any(
                entry.get(k) for k in ("title", "name", "uri")):
            cleaned.append(entry)
    return cleaned


def _find_track_items(obj: Any) -> List[Any]:
    """First "items"/"tracks" list of track-object dicts (uri/id) anywhere."""
    if isinstance(obj, dict):
        for key in ("items", "tracks"):
            value = obj.get(key)
            if isinstance(value, list) and value:
                unwrapped = [_unwrap_track(e) for e in value]
                if all(isinstance(e, dict) for e in unwrapped) and any(
                        e.get("uri") or e.get("id") for e in unwrapped):
                    return value
        for v in obj.values():
            found = _find_track_items(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_track_items(v)
            if found:
                return found
    return []


def _access_token(data: Dict[str, Any]) -> Optional[str]:
    """Anonymous Web API token that ships inside the embed-page JSON."""
    token = _find_key(data, "accessToken")
    return token if isinstance(token, str) and token else None


def _token_from_endpoint(url: str) -> Optional[str]:
    """Anonymous session token from the web player's own token endpoint."""
    try:
        r = httpx.get(url, headers=_HEADERS, timeout=10,
                      follow_redirects=True)
        if r.status_code == 200:
            token = (r.json() or {}).get("accessToken")
            if isinstance(token, str) and token:
                return token
    except Exception:
        pass
    return None


def _scrape_token(path: str) -> Optional[str]:
    """Anonymous token scraped straight out of a page's HTML."""
    try:
        r = httpx.get("https://" + "open.spotify.com/" + path,
                      headers=_HEADERS, timeout=12,
                      follow_redirects=True)
        m = re.search(r'"accessToken"\s*:\s*"([^"]+)"', r.text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _page_token(kind: str, sid: str) -> Optional[str]:
    """Fallback token source kept for compatibility: first scrape hit."""
    for path in (kind + "/" + sid, "embed/" + kind + "/" + sid):
        token = _scrape_token(path)
        if token:
            return token
    return None


class _Deadline:
    """Hard wall-clock budget so an analyze request can never hang the
    UI, however slow or blocked the network is."""

    def __init__(self, seconds: float):
        self._until = time.monotonic() + seconds

    def expired(self) -> bool:
        return time.monotonic() >= self._until


# 15-minute caches: analyze must feel instant on repeats, and clicking
# Download must reuse the list analyze already fetched instead of
# re-fetching it (that re-fetch was a 15+ second stall before the queue
# appeared).
_LIST_CACHE: Dict[Any, Any] = {}
_HASH_CACHE: Dict[str, Any] = {"checked": 0.0, "hash": None}
def _token_candidates(kind: str, sid: str,
                      data: Optional[Dict[str, Any]] = None,
                      deadline: Optional[_Deadline] = None):
    """Yield anonymous tokens from EVERY known source, deduplicated.

    Spotify rotates which surface still ships a usable anonymous token,
    so relying on a single source regularly comes up empty and the
    caller silently fell back to the embed page's 100-row list. Trying
    each source in turn keeps full pagination alive whichever one works.
    """
    seen: set = set()

    def fresh(token: Optional[str]) -> bool:
        if token and token not in seen:
            seen.add(token)
            return True
        return False

    token = _access_token(data) if data else None
    if fresh(token):
        yield token
    for endpoint in (
            "https://" + "open.spotify.com/api/token"
            "?reason=init&productType=web-player",
            "https://" + "open.spotify.com/get_access_token"
            "?reason=transport&productType=web_player"):
        if deadline is not None and deadline.expired():
            return
        token = _token_from_endpoint(endpoint)
        if fresh(token):
            yield token
    for path in (kind + "/" + sid, "embed/" + kind + "/" + sid):
        if deadline is not None and deadline.expired():
            return
        token = _scrape_token(path)
        if fresh(token):
            yield token
    if deadline is not None and deadline.expired():
        return
    token = _access_token(_embed_json(kind, sid, headers=_MOBILE_HEADERS))
    if fresh(token):
        yield token


class _TokenSupply:
    """Lazily pulls anonymous tokens; remembers the ones already fetched."""

    def __init__(self, kind: str, sid: str,
                 data: Optional[Dict[str, Any]] = None,
                 deadline: Optional[_Deadline] = None):
        self._gen = _token_candidates(kind, sid, data, deadline)
        self.seen: List[str] = []

    def next(self) -> Optional[str]:
        for token in self._gen:
            self.seen.append(token)
            return token
        return None

    def known(self) -> List[str]:
        if not self.seen:
            self.next()
        return list(self.seen)


def _api_tracks(kind: str, sid: str, supply: "_TokenSupply",
                deadline: Optional[_Deadline] = None
                ) -> List[Dict[str, Any]]:
    """Every track of an album/playlist via the paginated public Web API.

    The embed JSON caps long lists at its first page. Pagination follows
    "next" (with an explicit offset fallback) until every page is in,
    and on an auth error it rotates through every other token source
    instead of silently giving up after one retry.
    """
    plural = "albums" if kind == "album" else "playlists"
    limit = 50 if kind == "album" else 100
    base = f"https://api.spotify.com/v1/{plural}/{sid}/tracks"
    token = supply.next()
    if not token:
        logs.log("Spotify: no anonymous token from any source; only the "
                 "embed-page list is available.", level="warning",
                 source="spotify")
        return []
    headers = dict(_HEADERS, Authorization=f"Bearer {token}")
    collected: List[Any] = []
    url: Optional[str] = f"{base}?limit={limit}&offset=0"
    pages = 0
    rate_limited = 0
    while url:
        pages += 1
        if pages > 300:
            break
        if deadline is not None and deadline.expired():
            logs.log("Spotify Web API: time budget reached; keeping the "
                     f"{len(collected)} row(s) fetched so far.",
                     level="warning", source="spotify")
            break
        try:
            resp = httpx.get(url, headers=headers, timeout=20,
                             follow_redirects=True)
        except Exception as exc:
            logs.log(f"Spotify Web API request failed: {str(exc)[:120]}",
                     level="warning", source="spotify")
            break
        if resp.status_code in (400, 401, 403):
            nxt = supply.next()
            if nxt:
                logs.log(f"Spotify Web API rejected a token "
                         f"({resp.status_code}); trying the next token "
                         "source.", level="warning", source="spotify")
                headers = dict(_HEADERS, Authorization=f"Bearer {nxt}")
                continue
            logs.log(f"Spotify Web API answered {resp.status_code} for "
                     "every token source.", level="warning",
                     source="spotify")
            break
        if resp.status_code == 429:
            rate_limited += 1
            if rate_limited > 3 or (deadline is not None
                                    and deadline.expired()):
                logs.log("Spotify Web API keeps rate-limiting; keeping "
                         f"the {len(collected)} row(s) fetched so far.",
                         level="warning", source="spotify")
                break
            try:
                delay = min(float(resp.headers.get("Retry-After") or 2), 10.0)
            except (TypeError, ValueError):
                delay = 2.0
            time.sleep(delay)
            continue
        if resp.status_code != 200:
            logs.log(f"Spotify Web API answered {resp.status_code}; "
                     "falling back to the embed list.",
                     level="warning", source="spotify")
            break
        payload = resp.json()
        page = payload.get("items")
        if not isinstance(page, list) or not page:
            break
        collected.extend(page)
        # Keep requesting pages until EVERY track is in, however large
        # the playlist is. "next" is authoritative; when a response
        # omits it, fall back to an explicit offset for the next page.
        url = payload.get("next")
        if not url:
            total = payload.get("total")
            if isinstance(total, int) and len(collected) < total:
                url = f"{base}?limit={limit}&offset={len(collected)}"
    return _clean_tracklist(collected)


_QUERY_HASH_RE = re.compile(
    r'"fetchPlaylistContents"\s*,\s*"([0-9a-f]{64})"')
_QUERY_HASH_LOOSE_RE = re.compile(
    r'fetchPlaylistContents.{0,300}?([0-9a-f]{64})', re.DOTALL)
_BUNDLE_RE = re.compile(
    r'https://open\.spotifycdn\.com/cdn/build/web-player/[\w.-]+\.js')


def _playlist_query_hash(
        deadline: Optional[_Deadline] = None) -> Optional[str]:
    """Persisted-query hash for fetchPlaylistContents, read live from the
    web player's own JS bundles (it changes between releases, so it can
    never be hardcoded)."""
    if _HASH_CACHE["hash"] or time.time() - _HASH_CACHE["checked"] < 900:
        return _HASH_CACHE["hash"]
    _HASH_CACHE["checked"] = time.time()
    try:
        r = httpx.get("https://" + "open.spotify.com/", headers=_HEADERS,
                      timeout=12, follow_redirects=True)
        for bundle in _BUNDLE_RE.findall(r.text)[:3]:
            if deadline is not None and deadline.expired():
                return None
            try:
                js = httpx.get(bundle, headers=_HEADERS, timeout=15).text
            except Exception:
                continue
            m = _QUERY_HASH_RE.search(js) or _QUERY_HASH_LOOSE_RE.search(js)
            if m:
                _HASH_CACHE["hash"] = m.group(1)
                return m.group(1)
    except Exception:
        pass
    return None


def _pathfinder_tracks(sid: str, tokens: List[str],
                       deadline: Optional[_Deadline] = None
                       ) -> List[Dict[str, Any]]:
    """Full playlist via the partner GraphQL API the web player uses.

    Second, independent pagination path for when api.spotify.com stops
    accepting anonymous tokens: open.spotify.com can still list a full
    playlist while logged out, and this is the API it does that with.
    """
    query_hash = _playlist_query_hash(deadline)
    if not query_hash:
        logs.log("Spotify partner API: query hash not found in the "
                 "web-player bundle.", level="warning", source="spotify")
        return []
    endpoint = "https://" + "api-partner.spotify.com/pathfinder/v1/query"
    best: List[Any] = []
    for token in tokens:
        headers = dict(_HEADERS, Authorization=f"Bearer {token}")
        headers["app-platform"] = "WebPlayer"
        collected: List[Any] = []
        offset = 0
        total: Optional[int] = None
        pages = 0
        while total is None or offset < total:
            pages += 1
            if pages > 300:
                break
            if deadline is not None and deadline.expired():
                logs.log("Spotify partner API: time budget reached; "
                         f"keeping the {len(collected)} row(s) fetched "
                         "so far.", level="warning", source="spotify")
                break
            params = {
                "operationName": "fetchPlaylistContents",
                "variables": json.dumps(
                    {"uri": f"spotify:playlist:{sid}",
                     "offset": offset, "limit": 100}),
                "extensions": json.dumps(
                    {"persistedQuery":
                        {"version": 1, "sha256Hash": query_hash}}),
            }
            try:
                resp = httpx.get(endpoint, params=params, headers=headers,
                                 timeout=20)
            except Exception as exc:
                logs.log(f"Spotify partner API request failed: "
                         f"{str(exc)[:120]}", level="warning",
                         source="spotify")
                break
            if resp.status_code != 200:
                logs.log(f"Spotify partner API answered "
                         f"{resp.status_code}.", level="warning",
                         source="spotify")
                break
            content = _find_key(resp.json(), "content") or {}
            items = (content.get("items")
                     if isinstance(content, dict) else None)
            if not isinstance(items, list) or not items:
                break
            collected.extend(items)
            got = content.get("totalCount")
            if isinstance(got, int):
                total = got
            elif total is None:
                break
            offset += len(items)
        if len(collected) > len(best):
            best = collected
        if total is not None and len(best) >= total:
            break
    return _clean_tracklist(best)


def _track_id(t: Dict[str, Any]) -> Optional[str]:
    """The track id, from a bare "id" or a spotify:track: uri."""
    uri = t.get("uri")
    if isinstance(uri, str) and uri.startswith("spotify:track:"):
        return uri.rsplit(":", 1)[-1]
    tid = t.get("id")
    return tid if isinstance(tid, str) and tid else None


def _track_cover(t: Dict[str, Any]) -> Optional[str]:
    """This track's own art: embed coverArt, or its album's images."""
    direct = _cover_of(t)
    if direct:
        return direct
    for key in ("album", "albumOfTrack"):
        album = t.get(key)
        if isinstance(album, dict):
            found = _cover_of(album)
            if found:
                return found
    return None


def track_cover_url(track_id: str) -> Optional[str]:
    """One track's own art via its embed page (oEmbed as fallback).

    Used lazily at download time when the playlist/album list only carried
    the shared art, so nothing slows the initial analyze step down."""
    try:
        entity = _find_key(_embed_json("track", track_id), "entity") or {}
        found = _cover_of(entity)
        if found:
            return found
    except Exception:
        pass
    try:
        oe = _oembed("https://" + "open.spotify.com/track/" + track_id)
        return oe.get("thumbnail_url")
    except Exception:
        return None


def _extract_tracklist(data: Dict[str, Any], kind: str,
                       sid: str) -> List[Dict[str, Any]]:
    """Full track list from the embed-page JSON, trying every known layout
    in turn (BUG 3) — Spotify moves this around between releases."""
    cache_key = (kind, sid)
    cached = _LIST_CACHE.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < 900:
        return cached[1]

    # a. Classic layout: a flat "trackList" array.
    tracks = _clean_tracklist(_find_key(data, "trackList"))

    # Known per-kind structure variants.
    if kind == "album" and len(tracks) < 2:
        union = _find_key(data, "albumUnion")
        nested = union.get("tracks") if isinstance(union, dict) else None
        found = _clean_tracklist(
            nested.get("items") if isinstance(nested, dict) else None)
        tracks = max(tracks, found, key=len)
    if kind == "playlist" and len(tracks) < 2:
        v2 = _find_key(data, "playlistV2")
        content = v2.get("content") if isinstance(v2, dict) else None
        found = _clean_tracklist(
            content.get("items") if isinstance(content, dict) else None)
        tracks = max(tracks, found, key=len)

    # b. Any "items"/"tracks" list of Spotify track objects in the tree.
    if len(tracks) < 2:
        tracks = max(tracks, _clean_tracklist(_find_track_items(data)),
                     key=len)

    # Full paginated list via the Web API using the embed page's own
    # anonymous token (the embed JSON itself caps long lists) — this also
    # gives each playlist row its track's own album art (BUG 4).
    if kind in ("album", "playlist"):
        expected = _find_key(data, "totalCount")
        expected = expected if isinstance(expected, int) else None
        deadline = _Deadline(10.0)
        supply = _TokenSupply(kind, sid, data, deadline)
        api_tracks = _api_tracks(kind, sid, supply, deadline)
        if api_tracks:
            logs.log(f"Spotify Web API listed {len(api_tracks)} track(s) "
                     f"for this {kind}.", source="spotify")
        tracks = max(tracks, api_tracks, key=len)
        # When the Web API stops accepting anonymous tokens, the partner
        # GraphQL API (what open.spotify.com itself paginates with while
        # logged out) is a second, independent path to the FULL list.
        need_more = ((expected is not None and len(tracks) < expected)
                     or not api_tracks)
        if kind == "playlist" and need_more and supply.known():
            pf_tracks = _pathfinder_tracks(sid, supply.known(), deadline)
            if pf_tracks:
                logs.log(f"Spotify partner API listed {len(pf_tracks)} "
                         "track(s) for this playlist.", source="spotify")
                tracks = max(tracks, pf_tracks, key=len)

    # c. Last resort: the mobile embed page sometimes ships a different
    #    JSON structure. (Never one oEmbed call per track — far too slow.)
    if len(tracks) < 2 and kind != "track":
        mobile = _embed_json(kind, sid, headers=_MOBILE_HEADERS)
        found = _clean_tracklist(_find_key(mobile, "trackList"))
        if len(found) < 2:
            found = max(found, _clean_tracklist(_find_track_items(mobile)),
                        key=len)
        tracks = max(tracks, found, key=len)

    if kind != "track" and len(tracks) < 2:
        logs.log(f"Spotify {kind} {sid}: every strategy returned only "
                 f"{len(tracks)} track(s).", level="warning",
                 source="spotify")
    else:
        logs.log(f"Spotify {kind} {sid}: found {len(tracks)} track(s).",
                 source="spotify")
    now = time.monotonic()
    if len(_LIST_CACHE) >= 128:
        for stale in [k for k, v in _LIST_CACHE.items() if now - v[0] >= 900]:
            _LIST_CACHE.pop(stale, None)
        while len(_LIST_CACHE) >= 128:
            _LIST_CACHE.pop(next(iter(_LIST_CACHE)))
    _LIST_CACHE[cache_key] = (now, tracks)
    return tracks


def analyze(url: str) -> Optional[Dict[str, Any]]:
    """Preview metadata for the universal link box."""
    parsed = parse_url(url)
    if not parsed:
        return None
    kind, sid = parsed["kind"], parsed["id"]
    oe = _oembed(url)
    data = _embed_json(kind, sid)
    entity = _find_key(data, "entity") or {}
    tracklist = _extract_tracklist(data, kind, sid) if kind != "track" else []
    duration_ms = entity.get("duration") or 0

    # Track picker rows (BUG 5) for the FULL list - _api_tracks paginates
    # until every page is in, so playlists over 100 tracks show completely.
    entries = [{
        "index": i + 1,
        "title": t.get("title") or t.get("name") or "",
        "artist": t.get("subtitle") or _artists_of(t) or "",
        "duration": _duration_sec(t),
    } for i, t in enumerate(tracklist)]

    return {
        "platform": "spotify",
        "kind": "single" if kind == "track" else "playlist",
        "content_type": kind,
        "title": entity.get("name") or oe.get("title") or "Spotify item",
        "author": _artists_of(entity) or entity.get("subtitle") or "",
        "thumbnail": _cover_of(entity) or oe.get("thumbnail_url"),
        "duration": int(duration_ms / 1000) if duration_ms else None,
        "count": len(tracklist) if kind != "track" else 1,
        "entries": entries,
        "url": url,
    }


def resolve_tracks(url: str) -> List[Dict[str, Any]]:
    """Full track list -> one search job per track.

    Each entry: title / artist / album / cover / search_query.
    """
    parsed = parse_url(url)
    if not parsed:
        return []
    kind, sid = parsed["kind"], parsed["id"]
    data = _embed_json(kind, sid)
    entity = _find_key(data, "entity") or {}
    album = entity.get("name") if kind == "album" else None
    cover = _cover_of(entity)

    def to_track(t: Dict[str, Any]) -> Dict[str, Any]:
        title = t.get("title") or t.get("name") or ""
        artist = t.get("subtitle") or _artists_of(t) or _artists_of(entity)
        album_name = (t.get("albumName")
                      if isinstance(t.get("albumName"), str) else "")
        own_cover = _track_cover(t)
        return {
            "title": title,
            "artist": artist,
            "album": album or album_name or "",
            # Each track ships its own coverArt (BUG 4); the shared
            # album/playlist art is only the fallback.
            "cover": own_cover or cover,
            # When only the shared art is known, the downloader swaps in
            # this track's own art with one lazy lookup at download time.
            "cover_track_id": None if own_cover else _track_id(t),
            "duration": _duration_sec(t),
            # YouTube proper via ytsearch (NOT YouTube Music) - see spec.
            "search_query": f"{artist} - {title}".strip(" -"),
        }

    if kind == "track":
        info = analyze(url) or {}
        return [{
            "title": info.get("title", ""),
            "artist": info.get("author", ""),
            "album": album or "",
            "cover": info.get("thumbnail"),
            "duration": info.get("duration"),
            "search_query": f"{info.get('author', '')} - {info.get('title', '')}".strip(" -"),
        }]

    tracklist = _extract_tracklist(data, kind, sid)
    return [to_track(t) for t in tracklist if isinstance(t, dict)]
