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
# Spotify answers 429 to anonymous Web API tokens for HOURS at a time
# (observed Retry-After: ~19h). Waiting on it, or retrying it on every
# link, is what made analysing a Spotify playlist feel slow, so a 429 is
# remembered process-wide and the Web API is simply skipped until it
# expires. Nothing is ever lost: the partner API is the primary path.
_API_COOLDOWN: Dict[str, float] = {"until": 0.0}
_COOLDOWN_MIN = 300.0     # seconds, when Spotify sends no Retry-After
_COOLDOWN_MAX = 3600.0    # seconds, cap so a huge Retry-After isn't forever


def _api_cooling() -> bool:
    """True while the public Web API is known to be rate-limiting us."""
    return time.time() < _API_COOLDOWN["until"]


def _api_cooldown(retry_after: float = 0.0) -> None:
    """Remember a 429 so no later request wastes the user's time on it."""
    wait = max(_COOLDOWN_MIN, min(retry_after or 0.0, _COOLDOWN_MAX))
    _API_COOLDOWN["until"] = time.time() + wait
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
    if _api_cooling():
        # Straight past the rate limiter instead of into it (BUG 6).
        logs.log("Spotify Web API is still rate-limited; skipping it and "
                 "using the partner API instead.", source="spotify")
        return []
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
            # Do NOT sleep and retry: that is exactly what made every
            # Spotify link take ~30s before falling back (BUG 6). Record
            # the cooldown, stop instantly, keep whatever came back.
            try:
                retry_after = float(resp.headers.get("Retry-After") or 0)
            except (TypeError, ValueError):
                retry_after = 0.0
            _api_cooldown(retry_after)
            logs.log("Spotify Web API is rate-limiting; switching to the "
                     f"partner API (kept {len(collected)} row(s)).",
                     source="spotify")
            break
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


# ---------------------------------------------------------------------------
# Partner GraphQL API (api-partner.spotify.com) - the SAME API the logged-out
# web player paginates a playlist with. This is what lifts the 100-track cap
# (BUG 1) with nothing for the user to provide: no login, no API keys, no
# client secret. Verified against a 323-track playlist.
# ---------------------------------------------------------------------------

# The web-player bundle registers persisted queries as
#   new X("fetchPlaylistContents", "query", "<sha256>", null)
# so the hash is the THIRD argument, not the second. The old pattern only
# accepted name+hash and therefore never matched -> no pagination at all.
_QUERY_HASH_RES = (
    re.compile(r'"fetchPlaylistContents"\s*,\s*"query"\s*,\s*'
               r'"([0-9a-f]{64})"'),
    re.compile(r'"fetchPlaylistContents"\s*,\s*"([0-9a-f]{64})"'),
    re.compile(r'fetchPlaylistContents.{0,400}?([0-9a-f]{64})', re.DOTALL),
)
_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="(https://[^"]+\.js)"')
_BUNDLE_RE = re.compile(
    r'https://open\.spotifycdn\.com/cdn/build/web-player/[\w.~-]+\.js')
# Last-known-good hash, used only if every live lookup fails, so a
# temporarily unreachable CDN never costs the user their full playlist.
_FALLBACK_QUERY_HASH = (
    "e4b2953f160e58e38ac025d79b5a9b3aceee5c4c716598e9830bfceb69faff5f")
_PARTNER_ENDPOINTS = (
    ("GET", "https://" + "api-partner.spotify.com/pathfinder/v1/query"),
    ("POST", "https://" + "api-partner.spotify.com/pathfinder/v2/query"),
)


def _bundle_urls(sid: Optional[str] = None,
                 deadline: Optional[_Deadline] = None) -> List[str]:
    """Web-player JS bundle URLs, from the playlist page and the home page.

    The old code scraped open.spotify.com/ only and matched bundle URLs
    with a pattern that the current markup never yields (0 bundles found),
    so the query hash was never discovered. Reading the <script src=...>
    tags of the playlist page itself is what actually works today.
    """
    pages = []
    if sid:
        pages.append("https://" + "open.spotify.com/playlist/" + sid)
    pages.append("https://" + "open.spotify.com/")
    urls: List[str] = []
    for page in pages:
        if deadline is not None and deadline.expired():
            break
        try:
            r = httpx.get(page, headers=_HEADERS, timeout=12,
                          follow_redirects=True)
        except Exception:
            continue
        found = [u for u in _SCRIPT_SRC_RE.findall(r.text)
                 if "open.spotifycdn.com" in u]
        found += _BUNDLE_RE.findall(r.text)
        # web-player.*.js carries the persisted queries; try it first.
        found.sort(key=lambda u: ("web-player." not in u.rsplit("/", 1)[-1],
                                  u))
        for u in found:
            if u not in urls:
                urls.append(u)
        if urls:
            break
    return urls


def _playlist_query_hash(deadline: Optional[_Deadline] = None,
                         sid: Optional[str] = None,
                         refresh: bool = False) -> Optional[str]:
    """Persisted-query hash for fetchPlaylistContents, read live from the
    web player's own JS bundles (it changes between releases, so it can
    never be hardcoded)."""
    if not refresh:
        # Fast path, no network at all: the known-good hash is tried
        # optimistically. Scraping the web player's JS bundles up front
        # cost seconds and several megabytes on every single link; it now
        # only happens if Spotify actually rejects the persisted query
        # (_partner_page reports "stale-hash"), which is rare (BUG 6).
        return _HASH_CACHE.get("hash") or _FALLBACK_QUERY_HASH
    _HASH_CACHE["checked"] = time.time()
    for bundle in _bundle_urls(sid, deadline)[:6]:
        if deadline is not None and deadline.expired():
            break
        try:
            js = httpx.get(bundle, headers=_HEADERS, timeout=25).text
        except Exception:
            continue
        for pattern in _QUERY_HASH_RES:
            m = pattern.search(js)
            if m:
                _HASH_CACHE["hash"] = m.group(1)
                return m.group(1)
    logs.log("Spotify partner API: query hash not found in the web-player "
             "bundle; using the last known good hash.", level="warning",
             source="spotify")
    return _HASH_CACHE.get("hash") or _FALLBACK_QUERY_HASH


def _partner_headers(token: str) -> Dict[str, str]:
    headers = dict(_HEADERS, Authorization="Bearer " + token)
    headers["app-platform"] = "WebPlayer"
    headers["accept"] = "application/json"
    headers["content-type"] = "application/json;charset=UTF-8"
    headers["origin"] = "https://" + "open.spotify.com"
    headers["referer"] = "https://" + "open.spotify.com/"
    return headers


def _partner_page(sid: str, token: str, query_hash: str, offset: int,
                  limit: int = 100) -> Any:
    """One page of playlist rows. Tries pathfinder v1 (GET) then v2 (POST).

    Returns the parsed JSON of the first 200 answer, the string
    "stale-hash" when Spotify rejected the persisted query (the bundle
    rotated), or None when neither endpoint answered.
    """
    variables = {"uri": "spotify:playlist:" + sid, "offset": offset,
                 "limit": limit}
    extensions = {"persistedQuery": {"version": 1,
                                     "sha256Hash": query_hash}}
    stale = False
    for method, endpoint in _PARTNER_ENDPOINTS:
        try:
            if method == "GET":
                resp = httpx.get(endpoint, params={
                    "operationName": "fetchPlaylistContents",
                    "variables": json.dumps(variables),
                    "extensions": json.dumps(extensions),
                }, headers=_partner_headers(token), timeout=25)
            else:
                resp = httpx.post(endpoint, json={
                    "operationName": "fetchPlaylistContents",
                    "variables": variables,
                    "extensions": extensions,
                }, headers=_partner_headers(token), timeout=25)
        except Exception as exc:
            logs.log("Spotify partner API request failed: " + str(exc)[:120],
                     level="warning", source="spotify")
            continue
        if resp.status_code == 200:
            try:
                payload = resp.json()
            except Exception:
                continue
            blob = str(payload)[:400]
            if "PersistedQueryNotFound" in blob or "NOT_FOUND" in blob:
                stale = True
                continue
            return payload
        if resp.status_code in (400, 404) and "persisted" in resp.text.lower():
            stale = True
            continue
        logs.log("Spotify partner API answered " + str(resp.status_code)
                 + " (" + method + ").", level="warning", source="spotify")
    return "stale-hash" if stale else None


def _pathfinder_tracks(sid: str, tokens: List[str],
                       deadline: Optional[_Deadline] = None,
                       refresh_hash: bool = False
                       ) -> List[Dict[str, Any]]:
    """FULL playlist via the partner GraphQL API the web player uses.

    This is the path that removes the 100-track ceiling: open.spotify.com
    lists a whole playlist while logged out, and this is the API it does
    that with. Pagination follows the authoritative totalCount and
    deduplicates rows, so a playlist of any size comes back complete.
    """
    query_hash = _playlist_query_hash(deadline, sid, refresh=refresh_hash)
    if not query_hash:
        return []
    best: List[Any] = []
    for token in tokens:
        collected: List[Any] = []
        seen: set = set()
        offset = 0
        total: Optional[int] = None
        refreshed = False
        while total is None or offset < total:
            if deadline is not None and deadline.expired():
                logs.log("Spotify partner API: time budget reached; keeping "
                         "the " + str(len(collected)) + " row(s) fetched so "
                         "far.", level="warning", source="spotify")
                break
            payload = _partner_page(sid, token, query_hash, offset)
            if payload == "stale-hash" and not refreshed:
                # The web player shipped a new bundle: re-read the hash
                # once and retry this page instead of giving up.
                refreshed = True
                fresh = _playlist_query_hash(deadline, sid, refresh=True)
                if fresh and fresh != query_hash:
                    query_hash = fresh
                    continue
                break
            if not isinstance(payload, dict):
                break
            content = _find_key(payload, "content") or {}
            items = (content.get("items")
                     if isinstance(content, dict) else None)
            if not isinstance(items, list) or not items:
                break
            got = content.get("totalCount")
            if isinstance(got, int) and got > 0:
                total = got
            new_rows = 0
            for row in items:
                track = _unwrap_track(row)
                key = (track.get("uri") or track.get("id")
                       if isinstance(track, dict) else None)
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                collected.append(row)
                new_rows += 1
            offset += len(items)
            if new_rows == 0 and total is None:
                break
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
        # A 300+ track playlist needs several API pages; 10s was not
        # enough and truncated long lists back to the embed page's 100.
        deadline = _Deadline(30.0)
        supply = _TokenSupply(kind, sid, data, deadline)
        # PARTNER API FIRST (BUG 6). It is both complete and fast: one
        # request per 100 tracks, no rate limiting in practice. The public
        # Web API used to run first, and since it answers 429 to anonymous
        # tokens for hours on end, every Spotify link paid ~30s of retries
        # and sleeps before the working path was even tried.
        pf_tracks: List[Dict[str, Any]] = []
        if kind == "playlist" and supply.known():
            pf_tracks = _pathfinder_tracks(sid, supply.known(), deadline)
            if pf_tracks:
                logs.log(f"Spotify partner API listed {len(pf_tracks)} "
                         "track(s) for this playlist.", source="spotify")
                tracks = max(tracks, pf_tracks, key=len)
        # Only bother with the Web API when the partner API did not already
        # return everything (albums always, since the partner query above
        # is playlist-only).
        complete = (expected is not None and len(tracks) >= expected)
        if not complete and not (pf_tracks and len(tracks) > 100):
            api_tracks = _api_tracks(kind, sid, supply, deadline)
            if api_tracks:
                logs.log(f"Spotify Web API listed {len(api_tracks)} track(s) "
                         f"for this {kind}.", source="spotify")
            tracks = max(tracks, api_tracks, key=len)
            # Last chance for a playlist the partner API could not read at
            # all (e.g. a brand-new persisted-query hash AND a cold cache):
            # an embed list of exactly 100 rows is the classic "capped at
            # one page" signature, so retry the partner path once with a
            # freshly scraped hash (BUG 1).
            if (kind == "playlist" and not pf_tracks and supply.known()
                    and (len(tracks) >= 100
                         or (expected is not None and len(tracks) < expected))):
                retry = _pathfinder_tracks(sid, supply.known(), deadline,
                                           refresh_hash=True)
                if retry:
                    logs.log(f"Spotify partner API listed {len(retry)} "
                             "track(s) for this playlist.", source="spotify")
                    tracks = max(tracks, retry, key=len)

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
        if not album_name:
            # Partner-API rows carry the album under albumOfTrack/album.
            for key in ("albumOfTrack", "album"):
                nested = t.get(key)
                if isinstance(nested, dict) and isinstance(
                        nested.get("name"), str):
                    album_name = nested["name"]
                    break
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
