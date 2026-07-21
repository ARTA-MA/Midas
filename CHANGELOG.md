# Changelog

All notable changes to Midas. Installers (`Midas-Setup.msi`) for each
version are attached to the matching entry on the
[Releases page](../../releases).

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
