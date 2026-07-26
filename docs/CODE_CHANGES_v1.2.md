# Midas v1.2 — code documentation

What changed, why, and where. Four user-visible problems were fixed; all of
them live in the engine, with a small Flutter change for the pickers.
Nothing new is required from the user: no login, no cookies, no API keys.

Files touched:

| File | Change |
|---|---|
| `engine/midas_engine/core/spotify.py` | full playlist pagination + speed rework |
| `engine/midas_engine/core/soundcloud.py` | **new** — keyless names + per-track art |
| `engine/midas_engine/core/analyzer.py` | serves real names and per-entry artwork |
| `engine/midas_engine/core/downloader.py` | queue card follows the current track's art |
| `app/lib/models/models.dart` | `PlaylistEntry.thumbnail` |
| `app/lib/screens/home/home_screen.dart` | per-row covers in both pickers |
| `engine/tests/stress_test.py` | 13 offline regression checks |

---

## 1. Spotify playlists were capped at the first 100 tracks

**Root cause.** Discovery relied on `open.spotify.com/embed/playlist/<id>`,
whose `__NEXT_DATA__` JSON ships **at most one page (100 rows)** and ignores
`?offset=` / `?page=`. The documented fallback
(`api.spotify.com/v1/playlists/<id>/tracks?offset=100`) answers **429
QUOTA_EXCEEDED** to anonymous tokens with `Retry-After` of roughly 19 hours,
so every row past 100 silently vanished.

**Fix — the web player's own partner GraphQL API** (`spotify.py`):

- `_playlist_query_hash(deadline, sid, refresh=False)` — returns the
  `fetchPlaylistContents` persisted-query hash. Cached, with a
  last-known-good constant (`_FALLBACK_QUERY_HASH`) so an unreachable CDN
  never costs the user their playlist. `refresh=True` re-scrapes the live
  web-player bundle.
- `_bundle_urls(sid, deadline)` — collects candidate JS bundles from the
  `<script src=...>` tags of the playlist page (then the home page),
  preferring `web-player.*.js`, which is where the persisted queries live.
- `_partner_page(sid, token, query_hash, offset, limit=100)` — one page via
  `pathfinder/v1/query` (GET) and `pathfinder/v2/query` (POST), using the
  embed page's anonymous bearer token plus the `app-platform: WebPlayer`,
  `origin` and `referer` headers. Returns the parsed JSON, the sentinel
  `"stale-hash"` when Spotify rejects the persisted query, or `None`.
- `_pathfinder_tracks(sid, tokens, deadline, refresh_hash=False)` — pages by
  the authoritative `content.totalCount` in 100-row batches, de-duplicates
  by `uri`/`id`, refreshes a stale hash exactly once, and keeps partial
  results if a page fails mid-way.
- `_TokenSupply` / `_token_candidates()` — yields anonymous tokens from every
  known surface (embed JSON, both token endpoints, page scrapes, mobile
  embed) so a single dead source can't silently truncate a list.
- `_extract_tracklist(data, kind, sid)` — unchanged strategy ladder, plus:
  the partner list is merged with `max(..., key=len)`, and an exactly-100-row
  result is treated as "probably truncated" and retried with a freshly
  scraped hash.
- `resolve_tracks()` — also reads album names from partner rows
  (`albumOfTrack.name` / `album.name`) so tags and cover art stay correct.

Short playlists, albums and single tracks still take exactly the same paths
as before.

## 2. "Item 1, Item 2, Item 3, ..." instead of SoundCloud track names

**Root cause.** SoundCloud sets are listed with `yt-dlp --flat-playlist`,
which for this platform returns entries with **no `title` field at all**
(verified: 106 of 106 blank). The analyzer fell through to its
`f"Item {index + 1}"` placeholder. Deep per-track extraction is not an
option: it trips SoundCloud's rate limiter (HTTP 403).

**Fix — `core/soundcloud.py` (new)**, all public endpoints, no keys:

- `_client_id()` — scrapes the site's own public `client_id` out of its JS
  (with a known-good fallback), cached.
- `_lookup(url, field, budget)` — shared engine behind the two public
  helpers: reads `window.__sc_hydration` from the set page for the real
  track order and ids, then resolves missing rows through
  `api-v2.soundcloud.com/tracks?ids=<≤50 csv>` in batches of 50
  (`_BATCH = 50`). Results are cached for 15 minutes (`_CACHE_TTL`), bounded,
  and every request is inside a wall-clock budget.
- `track_names(url)` — `{track id / permalink -> "Artist - Title"}`.
- `track_art(url)` — `{track id / permalink -> 500x500 cover}`; artwork URLs
  are upgraded from `-large.` to `-t500x500.`.
- `playlist(url, budget=25.0)` — full rows (name, artist, duration,
  thumbnail) for callers that want everything at once.
- `is_playlist_url(url)` — cheap guard so single-track links never pay for a
  set lookup.

**Fix — `core/analyzer.py`:**

- `_soundcloud_names(url, platform, entries)` — only runs for SoundCloud
  playlists that actually came back with blank titles; any failure is logged
  and ignored, so analysis degrades exactly as before.
- `_entry_title(entry, index, names)` — matches by `id`, `webpage_url` and
  `url` (query strings stripped), then the URL slug (`_slug_name`), and only
  then the old `Item N` placeholder.

## 3. Every Spotify link paid ~30s of rate-limiting before it worked

**Root cause.** `_extract_tracklist()` tried the public Web API **first**,
slept on its `429`s and retried them, and only then fell through to the
partner API — the path that actually works. The user's own log showed 34
seconds of pure waiting before the correct result arrived. A second, smaller
tax: the persisted-query hash lookup downloaded and regex-scanned
multi-megabyte JS bundles before the first query.

**Fix — `spotify.py`:**

- **Partner API first** for playlists; the Web API only runs when the partner
  path did not already return everything (albums always, since the partner
  query is playlist-only).
- **Never sleep on a 429.** `_api_tracks()` records `Retry-After` and returns
  instantly with whatever it collected.
- **Process-wide cooldown breaker.** `_api_cooldown(retry_after)` and
  `_api_cooling()` remember the rate limit in `_API_COOLDOWN`, clamped to
  `_COOLDOWN_MIN = 300s` .. `_COOLDOWN_MAX = 3600s`, so later links skip the
  Web API in ~0ms instead of re-discovering the 429 — and Spotify's 19-hour
  `Retry-After` can never disable the path for a whole day.
- **Zero-network hash fast path.** `_playlist_query_hash()` returns the
  cached / known-good hash without touching the network; the bundle is only
  scraped when `_partner_page()` reports `"stale-hash"`.
- `_Deadline` budget lowered 45s -> 30s, and `_LIST_CACHE` (15 min) means
  pressing *Download* reuses the list that *Analyze* already fetched instead
  of re-fetching it.

**Measured on the 323-track playlist: 34.6s -> 2.44s cold, 0.84s warm**,
still 323/323 rows with zero blank titles.

## 4. The app showed the playlist icon while downloading, not the track

**Root cause.** A set download is a single queue item whose `thumbnail` was
set once from `preview["thumbnail"]` (the set's icon). The finished files
looked right only because yt-dlp embeds per-track art itself, which the app
never saw; per-track covers never reached the analyzer's entries either, so
the picker had nothing to show.

**Fix:**

- `core/soundcloud.py` — every row carries `"thumbnail"`, its own 500x500
  cover; `track_art()` shares the cached set page with `track_names()`, so
  artwork costs **no extra requests**.
- `core/analyzer.py` — `_soundcloud_art()`, `_entry_keys()` and
  `_entry_thumbnail(entry, art)`: every playlist entry now ships a
  `thumbnail`, resolved by track id / `webpage_url` / `url`, falling back to
  the flat listing's own image and never to the playlist icon.
- `core/downloader.py` — `_art_map_from_preview()` builds a
  `{normalised title, "#index"} -> cover` map, `_norm_title()` makes the
  match punctuation-insensitive, the job starts on the **first selected
  track's** cover, and `_art_for()` inside `_apply_progress()` swaps in the
  current track's cover as yt-dlp moves through the set. `DownloadItem.
  art_map` is runtime-only — excluded from `to_dict()`, never written to
  history — and artwork failures can never break a live download.
- `_resolve_spotify_art()` runs *before* the first `downloading` event, so
  Spotify rows never flash the album/playlist art.
- Flutter: `PlaylistEntry.thumbnail` in `models.dart`; both pickers in
  `home_screen.dart` render a 28px rounded cover per row (omitted silently
  when a track has none). `queue_screen.dart` already rendered
  `item.thumbnail`, so the live card needed no change.

---

## Validation

Offline (`engine/tests/stress_test.py`, no network): **13/13 checks pass**,
plus `py_compile` over the whole engine package.

Live, on a Linux VM with real internet and the user's own playlists:

| Check | Result |
|---|---|
| Spotify `analyze()`, 323-track playlist, cold | 2.44s, 323/323 rows, 0 blank titles |
| Same, warm cache | 0.84s, 323 rows |
| Cooldown breaker while rate-limited | Web API skipped in 0.000s |
| Spotify editorial playlist (50) / single track / bad id | 50 / 1 / degrades cleanly |
| SoundCloud set (106 tracks) | 106/106 real names, 0 "Item N" left |
| SoundCloud per-track covers | 106 distinct covers, 0 equal to the set icon |
| Cover URLs actually load | 5/5 HTTP 200, `image/*`, all `-t500x500` |
| SoundCloud single track / dead set / junk input | unaffected / `{}` / `{}`, never raises |

**Not verified here:** the Flutter changes were reviewed but not compiled
(no Windows/Flutter toolchain was available), and the `.msi` must be built
on Windows — see `.github/workflows/release-v1.2.yml`.
