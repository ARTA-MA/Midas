# Midas v1.2

> *Everything you touch turns to gold.*

**Install:** download `Midas-Setup.msi` below and run it. The installer is
unsigned, so SmartScreen may say "unknown publisher" — click *More info >
Run anyway*. Installing over v1.0/v1.1 upgrades in place and keeps your
settings and history (`%APPDATA%\Midas`).

## Highlights

- **Spotify playlists are no longer capped at 100 tracks.** Long playlists
  now come back complete — tested on a 323-track playlist — with nothing
  extra to provide: no login, no cookies, no API keys.
- **Spotify links analyze in about 2 seconds instead of 35.** The
  rate-limited public API is no longer tried first, rate limits are never
  waited on, and once Spotify starts limiting, Midas stops asking.
- **Real SoundCloud track names.** "Item 1, Item 2, Item 3…" is gone; sets
  list their actual titles.
- **Per-track artwork.** Playlist pickers and the live download card show
  the cover of the track being downloaded instead of the playlist icon —
  matching the artwork embedded in the finished files.

## Also in this release

- Spotify album names are read correctly for tracks resolved through the
  playlist API, so tags and cover art stay right.
- An offline regression suite (`engine/tests/stress_test.py`, 13 checks)
  covers pagination, name fallbacks, artwork, the rate-limit breaker and
  hostile input.

Full details: [CHANGELOG.md](../CHANGELOG.md) ·
[docs/CODE_CHANGES_v1.2.md](CODE_CHANGES_v1.2.md)
