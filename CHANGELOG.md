# Changelog

All notable changes to Midas. Installers (`Midas-Setup.msi`) for each
version are attached to the matching entry on the
[Releases page](../../releases).

---

## v1.2.1

### Fixed
- **Downloading a clip of a video you already had gave you the whole video.**
  A time-range download reused the full video's output filename, so yt-dlp
  found the existing file, reported *"has already been downloaded"* and
  skipped the job - while the queue still reported **completed**. Clips now
  get their own `[clip HH-MM-SS-HH-MM-SS]` filename, so a range can never
  collide with the full video, and two different ranges of the same video no
  longer overwrite each other. The suffix is added before the `.%(ext)s`
  marker and contains no `:`, so it stays Windows-safe.

### Added
- A 14th offline regression check (`engine/tests/stress_test.py`) pinning the
  clip output template and asserting that `_build_cmd` emits a
  clip-specific `-o` while still passing `--download-sections`.
- `docs/SMOKE_TEST_2026-08-02.md` - the full five-platform download matrix
  (YouTube, Spotify, SoundCloud, TikTok, Instagram) used to validate this
  release, including single, playlist, audio-only and clip jobs.

---

## v1.2.0

### Added
- **Full Spotify playlists, whatever their size.** Track discovery now uses
  the partner GraphQL API that `open.spotify.com` itself paginates with
  while logged out, so playlists past the old 100-track ceiling come back
  complete. Nothing is required from you: no login, no cookies, no API keys
  or client secret. Verified end to end on a 323-track playlist.
- **`engine/midas_engine/core/soundcloud.py`** - keyless SoundCloud
  metadata: real track names, artists, durations and 500x500 per-track
  artwork, read from the set page's own hydration blob plus the public
  track API in batches of 50 (cached 15 minutes, budget-limited).
- **Per-track artwork in the UI.** Playlist entries now carry their own
  cover, so both playlist pickers render a small round cover per row.
- **Offline regression suite** (`engine/tests/stress_test.py`) covering
  pagination, name fallbacks, artwork resolution, the rate-limit breaker,
  and hostile/malformed input.

### Improved
- **Spotify analysis is dramatically faster: ~34.6s -> ~2.4s cold** (0.8s
  warm) on a 323-track playlist.
  - The rate-limited public Web API is no longer tried first; the partner
    API (complete *and* fast) runs first for playlists.
  - A `429` is never slept on or retried - it is recorded and abandoned
    instantly, keeping whatever rows already arrived.
  - A process-wide cooldown breaker (5 min .. 1 h) makes later links skip
    the Web API entirely instead of re-discovering the rate limit. Spotify
    answers with `Retry-After` values of ~19 hours, which the cap prevents
    from disabling the path for a whole day.
  - The persisted-query hash is used optimistically from cache, so the
    multi-megabyte web-player JS bundle is only downloaded when Spotify
    actually rejects the query.
  - Overall extraction deadline lowered 45s -> 30s now that the slow path
    is off the happy path.
- **Live queue cards show the right cover.** A playlist job starts on the
  first selected track's artwork and follows yt-dlp through the set, so the
  card matches the artwork embedded in the finished file.

### Fixed
- SoundCloud set entries showed **"Item 1, Item 2, Item 3, ..."** instead of
  track names. yt-dlp's flat listing returns no title at all for SoundCloud
  sets (106/106 entries were blank on the test set); real names are now
  resolved by track id, `webpage_url` or `url`, with the URL slug and only
  then the old placeholder as fallbacks.
- Playlist previews and in-progress downloads showed the **playlist icon**
  for every track even though the finished files had correct per-track
  covers.
- Spotify album names are read from partner rows (`albumOfTrack.name` /
  `album.name`), keeping tags and cover art correct for those tracks.

---

## v1.1.0

### Added
- **Studio - Crop tab**: visually crop the picture of any finished video.
  Scrub to any frame, drag horizontal/vertical range sliders over a live
  preview with golden crop guides, and export either as a copy or over the
  original ("Keep original file" toggle).
- **Clip (time-section) downloads for Instagram, TikTok and Reddit** - the
  same start/end selector YouTube has. Their posts are now fully analyzed so
  the real duration is known.
- **Embedded cover art in the Downloads list** - local/converted files with
  no web thumbnail now show the artwork embedded in the file itself.
- **Windows installer**: `build_msi.bat` builds a single-file
  `Midas-Setup.msi` (per-machine install, Start-menu + desktop shortcuts,
  clean uninstall). The script downloads every build dependency itself,
  including the WiX toolset - zero manual setup.
- **Per-user settings location**: settings, history, logs and cookies now
  live in `%APPDATA%\Midas` (macOS/Linux equivalents supported) and survive
  app moves, updates and reinstalls. Existing data is migrated automatically
  from the old `data/` folder on first start. Set the `MIDAS_DATA_DIR`
  environment variable for a fully portable setup.

### Improved
- **Analysis is much faster** (all platforms): results cache, tighter
  network timeouts, YouTube manifest skipping, quicker clipboard detection
  and higher-resolution preview thumbnails.
- **Link bar behaves naturally**: typing or editing a link re-analyzes it
  automatically (debounced), and clearing the bar clears the preview card.
- **Download card**: hover/interaction animations removed (entrance and
  click feedback kept).
- **SoundCloud**: playlist entries without titles get readable names derived
  from their track URLs instead of "Item 1..N"; sets are treated as
  audio-only (no bogus video-quality selector); rate-limit errors (HTTP 403)
  now show a clear "wait a minute" message instead of raw tool output.

### Fixed
- Crop tab's "Keep original file" switch was invisible (gold knob on gold
  track).
- SoundCloud set previews could hang on the loading skeleton for minutes;
  set analysis is back to a single lightweight request that cannot trip
  SoundCloud's rate limiter.
- Removing or hand-editing the link no longer leaves a stale preview card.

---

## v1.0.0

Initial release.

- One universal paste-a-link box for **YouTube, Spotify, SoundCloud,
  Instagram, TikTok and Reddit** with automatic platform and
  single-vs-playlist detection.
- Preview card with title, uploader, duration, thumbnail and a playlist
  track picker; per-download quality/format overrides.
- Download queue with concurrent `yt-dlp` jobs, cancel, and resume-on-retry;
  SQLite download history.
- **Spotify without any API keys**: metadata via the public embed endpoints,
  audio sourced from YouTube and fully tagged (ID3/MP4/FLAC/Opus + cover).
- **YouTube chapters**: native chapters embedded; if missing, timestamps are
  parsed from the description and injected with ffmpeg.
- **Studio**: convert formats, trim, and edit cover art / metadata of any
  finished download.
- Self-managing dependencies (Deno, yt-dlp, ffmpeg) with one-click updates;
  black-and-gold luxury UI; i18n-ready strings scaffold.
