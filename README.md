# Midas — All-in-One Media Downloader (Windows)

> *Everything you touch turns to gold.*

Midas downloads media from **YouTube, Spotify, SoundCloud, Instagram, TikTok and Reddit** behind one universal paste-a-link box, wrapped in a black-and-gold luxury UI.

## Install (for users)

Grab **`Midas-Setup.msi`** from the [Releases page](../../releases) and run
it - no other setup. (The installer is unsigned, so SmartScreen may show
"unknown publisher"; click *More info > Run anyway*.)

## Features

- **One paste box** - YouTube, Spotify, SoundCloud, Instagram, TikTok,
  Reddit; auto-detects the platform and single-vs-playlist.
- **Preview card** - title, uploader, duration, thumbnail, playlist track
  picker; per-download quality/format overrides (audio-only platforms skip
  video options automatically).
- **Clip downloads** - grab just a time-section of a video (YouTube,
  Instagram, TikTok, Reddit).
- **Queue and history** - concurrent downloads, cancel, resume-on-retry,
  SQLite history, embedded cover art shown for local files.
- **Studio** - post-process any finished item: convert formats, trim, crop
  the picture, edit cover art / metadata.
- **Self-managing dependencies** - Deno, yt-dlp and ffmpeg are installed
  and updated from inside the app.
- **Per-user settings** - stored in `%APPDATA%\Midas`, migrated
  automatically from old versions (portable override: `MIDAS_DATA_DIR`).

More depth: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) - versions:
[CHANGELOG.md](CHANGELOG.md).

## Architecture

```
Flutter (Windows)  ──HTTP──▶  Python engine (FastAPI on 127.0.0.1:<random port>)
        ▲                        │
        └───── WebSocket /events ┘   (realtime progress, deps install, queue changes)
```

**Why a local FastAPI server + WebSocket (vs JSON-over-stdio)?**

- **Realtime progress:** one WebSocket multiplexes progress for many concurrent downloads without stdout framing/buffering pitfalls.
- **Cancel & concurrency:** each download is an independent `yt-dlp.exe` subprocess; cancel is a REST call that terminates just that process. `.part` files are kept, so *Retry* resumes where it stopped.
- **Clean shutdown:** the Flutter shell owns the engine process, calls `/shutdown` on exit, and a heartbeat watchdog inside the engine self-terminates if the UI ever disappears — no orphan processes.
- **Debuggability:** in dev mode the whole engine is inspectable with a browser/`curl` at `http://127.0.0.1:8765/docs`.

Other design choices:

- **yt-dlp runs as the vendored `vendor\yt-dlp.exe` subprocess**, not as an imported library → the *Update yt-dlp* button is a simple file replacement, no re-packaging.
- The vendor folder (with `deno.exe` as yt-dlp's JS runtime and `ffmpeg`/`ffprobe`) is prepended to `PATH` for every job, so yt-dlp finds everything automatically. Fully portable — nothing touches the system.
- **Spotify without any API:** the public oEmbed endpoint + the embed page's inline JSON give track/artist/album/cover (same approach as spotdl's metadata layer); the engine then searches **YouTube proper** via `ytsearch1:"artist - title"` and tags the audio (ID3/MP4/FLAC/Opus + cover art) with mutagen.
- **YouTube chapters:** `--embed-chapters` handles native chapters; if a video has none, the engine parses timestamps out of the description and injects them with ffmpeg (`FFMETADATA` remux, stream-copy so it's instant).

## Folder structure

```
midas/
├── build.bat            ← one-click release build → dist\Midas\Midas.exe
├── build_msi.bat        ← one-click installer build → dist\Midas-Setup.msi
├── publish_to_github.bat← push this source to your own GitHub repo
├── installer/           ← WiX definition used by build_msi.bat
├── docs/                ← HOW_IT_WORKS.md + release-notes templates
├── run_dev.bat          ← dev mode (engine on :8765 + flutter run)
├── engine/              ← Python engine
│   ├── main.py          ← entrypoint (prints MIDAS_ENGINE_PORT=… for the UI)
│   └── midas_engine/
│       ├── api/app.py   ← FastAPI routes + /events WebSocket + watchdog
│       ├── core/
│       │   ├── analyzer.py    ← URL → platform / single-vs-playlist / preview
│       │   ├── downloader.py  ← queue, yt-dlp subprocesses, progress, tagging
│       │   ├── spotify.py     ← keyless Spotify metadata (oEmbed + embed page)
│       │   ├── chapters.py    ← description-timestamp → embedded chapters
│       │   ├── deps.py        ← Deno / yt-dlp / ffmpeg install & updates
│       │   └── history.py     ← SQLite download history
│       ├── settings.py  ← persisted user settings (JSON)
│       └── events.py    ← thread-safe → asyncio event bus
├── app/                 ← Flutter app (lib/, assets/, windows/midas.ico)
├── vendor/              ← deno.exe, yt-dlp.exe, ffmpeg.exe (filled by build.bat
│                          or by the in-app Dependency Manager)
├── assets_src/          ← original AI-generated brand artwork
├── scripts/             ← bootstrap.bat (auto-downloads prerequisites), get_fonts.bat
└── tools/               ← portable Python / Flutter SDK / Git (auto-downloaded)
```

## Build (release)

1. Run **`build.bat`** — that's it. Anything missing (**Python**, the **Flutter SDK**, **Git**, Deno, yt-dlp, ffmpeg, fonts) is downloaded automatically as *portable* copies into the repo's own `tools\` / `vendor\` folders — no admin rights, nothing installed system-wide.
   - One exception: the **Visual Studio C++ Build Tools** (Flutter needs them to compile Windows apps, and Microsoft offers no portable version). The script tries to install them silently via `winget`; if that's unavailable, it prints one-time manual instructions.
2. Ship or run `dist\Midas\Midas.exe`. Downloads default to `Downloads\Midas`; settings/history live per-user in `%APPDATA%\Midas` (set the `MIDAS_DATA_DIR` env var for a fully portable setup).

## Build the Windows installer (.msi)

Run **`build_msi.bat`** - it reuses (or first builds) `dist\Midas`, downloads
the WiX toolset automatically, and produces the single-file
**`dist\Midas-Setup.msi`** (per-machine install, shortcuts, clean
uninstall). Attach that file to a GitHub release - see
[docs/RELEASE_NOTES_v1.1.0.md](docs/RELEASE_NOTES_v1.1.0.md) for the
ready-made release text.

## Versions

Every release is detailed in [CHANGELOG.md](CHANGELOG.md); installers are
attached to the matching entry on the [Releases page](../../releases).

## Run (development)

Run **`run_dev.bat`** — it bootstraps the same prerequisites automatically, then starts the engine on `http://127.0.0.1:8765` (interactive API docs at `/docs`) in its own console and launches the Flutter app in debug mode. Missing tools can be installed from *Settings → Dependencies* inside the app.

## Typography & branding

- Wordmark/display: **Cormorant** · UI: **Manrope** — both open-license, downloaded at build time and bundled (no runtime font downloads).
- The requested **Bruney** face is *free for personal use only* and can't be redistributed here. If you own a license: drop `Bruney.otf` into `app/assets/fonts/`, add a `Bruney` family in `pubspec.yaml` (a commented template is there), and set `MidasTheme.displayFamily = 'Bruney'` in `app/lib/core/theme/midas_theme.dart`.
- All artwork (icon, splash, empty/error states, header) is AI-generated on a consistent black `#0B0B0D` / gold `#D4AF37–#F5D061` / restrained-red palette; the Windows icon `app/windows/midas.ico` contains 16–256 px sizes and is wired in by `build.bat`.

## i18n

English ships today; `app/lib/core/i18n/strings.dart` is a ready scaffold — add a new language map and switch via the `language` setting.
