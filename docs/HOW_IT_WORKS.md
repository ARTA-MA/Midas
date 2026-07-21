# How Midas works

A technical walkthrough of the app, subsystem by subsystem. For build
instructions see the [README](../README.md); for version history see the
[CHANGELOG](../CHANGELOG.md).

## The two halves

```
Flutter desktop shell (app/)        Python engine (engine/)
  UI, theming, i18n        HTTP     FastAPI on 127.0.0.1:<random port>
  screens + widgets  <------------->  yt-dlp / ffmpeg orchestration
  Riverpod state      WebSocket      SQLite history, JSON settings
```

1. On launch the Flutter shell spawns `engine/midas-engine.exe`
   (`app/lib/services/engine_process.dart`). The engine prints
   `MIDAS_ENGINE_PORT=<port>` on stdout; the shell reads it and builds an
   HTTP client against `127.0.0.1:<port>`.
2. All realtime updates (download progress, dependency installs, queue
   changes) stream over one WebSocket at `/events`.
3. On exit the shell calls `/shutdown`; a heartbeat watchdog inside the
   engine also self-terminates it if the UI ever disappears, so no orphan
   processes are left behind.

## Analysis pipeline (paste a link -> preview card)

`engine/midas_engine/core/analyzer.py`

1. The URL is normalized and the platform detected (YouTube, Spotify,
   SoundCloud, Instagram, TikTok, Reddit, or generic).
2. Results are served from a short-lived in-memory cache when the same link
   is analyzed twice.
3. `yt-dlp` runs with speed-tuned flags (`_analysis_flags`):
   - flat playlist listing for most platforms (one cheap request),
   - full extraction for Instagram/TikTok/Reddit posts so the real duration
     is known (enables the clip picker),
   - YouTube manifest downloads skipped (metadata only).
4. Spotify links skip yt-dlp entirely: metadata comes from the public
   embed/oEmbed endpoints (`core/spotify.py`).
5. The payload sent to the UI contains platform, single-vs-playlist, title,
   uploader, duration, best thumbnail, and named playlist entries (names
   fall back to URL slugs when a flat listing has no titles).

## Download pipeline

`engine/midas_engine/core/downloader.py`

- Every download is an independent `yt-dlp.exe` subprocess with the
  `vendor/` folder prepended to `PATH` (so ffmpeg/deno are found).
- Progress lines are parsed and published to the event bus
  (`events.py`) -> WebSocket -> UI progress bars.
- Cancel terminates just that subprocess; `.part` files are kept so Retry
  resumes where it stopped.
- Audio-only platforms (SoundCloud, Spotify) automatically drop
  video-quality overrides.
- Spotify downloads search YouTube (`ytsearch1:"artist - title"`), then tag
  the file (ID3/MP4/FLAC/Opus + cover art) with mutagen.
- YouTube: native chapters are embedded; if absent, `core/chapters.py`
  parses timestamps from the description and injects them via an instant
  ffmpeg stream-copy remux.
- Finished items are stored in SQLite (`core/history.py`).

## Studio (post-processing)

`engine/midas_engine/core/studio.py`

Every finished download (and any local file) can be post-processed:

- **Convert** - format/codec changes via ffmpeg.
- **Trim** - keep a time range.
- **Crop** - keep a picture region: the UI scrubs frames
  (`GET /studio/{id}/frame`), the user drags range sliders over the
  preview, and `POST /studio/{id}/crop` runs the ffmpeg crop.
- **Cover art / metadata** - extract or replace embedded artwork
  (`GET /studio/{id}/cover`); the Downloads list reuses this endpoint to
  show artwork for local files that have no web thumbnail.
- Long jobs report progress over the same `/events` WebSocket.

## Engine API surface (dev mode: http://127.0.0.1:8765/docs)

| Route | Purpose |
|---|---|
| `POST /analyze` | link -> preview payload |
| `GET/POST /downloads` | queue state / start a download |
| `POST /downloads/{id}/cancel|retry` | control a job |
| `GET /studio/{id}/frame` | JPEG frame at a timestamp (crop preview) |
| `GET /studio/{id}/cover` | embedded artwork |
| `POST /studio/{id}/convert|trim|crop|...` | post-processing jobs |
| `GET/PUT /settings` | persisted user settings |
| `WS /events` | realtime progress + queue events |
| `POST /shutdown` | clean exit |

## Data locations (since v1.1.0)

| What | Where |
|---|---|
| Settings, history, logs, cookies.txt | `%APPDATA%\Midas` (Windows), `~/Library/Application Support/Midas` (macOS), `$XDG_DATA_HOME/midas` (Linux) |
| Portable override | set `MIDAS_DATA_DIR` env var |
| Old `<app>/data` folder | migrated automatically on first start |
| Downloads | `Downloads\Midas` by default (changeable in Settings) |
| Binaries (deno, yt-dlp, ffmpeg) | `vendor/` next to the app - updating yt-dlp is a simple file replacement |

## Build pipeline

- `build.bat` -> portable `dist/Midas/` (auto-downloads portable Python,
  Flutter SDK, Git, Deno, yt-dlp, ffmpeg, fonts; PyInstaller for the
  engine, `flutter build windows` for the shell).
- `build_msi.bat` -> single-file `dist/Midas-Setup.msi` (auto-downloads the
  WiX 3.11 toolset, harvests `dist/Midas`, compiles the installer).
- `run_dev.bat` -> engine on `:8765` + `flutter run` for development.
- Engine stress tests: `python engine/tests/stress_test.py`.
