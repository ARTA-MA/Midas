# Smoke test - 2026-08-02 (v1.2.1)

Every download path was exercised against the **live engine** (`POST
/analyze` -> `POST /downloads` -> poll `GET /downloads`), not just the
offline suite. Each finished file was verified on disk with `ffprobe`
(byte size + real duration), because a job reporting `completed` is not
proof that the right media landed.

## Environment

| | |
|---|---|
| Host | Ubuntu 22.04.5, 2 vCPU / 12 GB, datacenter IP |
| Python | 3.12.13 |
| yt-dlp | 2026.07.04 |
| ffmpeg | system `/usr/bin/ffmpeg` (no `vendor/`, tools resolved from PATH) |
| Engine | 1.2.0 on `127.0.0.1:8765`, `MIDAS_DATA_DIR` redirected |

> On Linux `--windows-filenames` rewrites `:` to the fullwidth `：`. That is
> intentional portability behaviour, not a bug.

## Results

| # | Case | Link | Result |
|---|------|------|--------|
| 1 | YouTube single, 720p | `watch?v=bLojKyJx7fA` | PASS - 11,977,965 B, 179.60s |
| 2 | YouTube **clip** 10-20s | same video | **FAILED -> fixed in v1.2.1** (see below) |
| 3 | YouTube audio-only (mp3) | same video | PASS - 4,887,492 B |
| 4 | YouTube playlist, items 1-2 | `PLa1F2ddGya_8u-HEvmfCVuS_OImW8HaLd` (84 videos) | PASS - 66,247,778 B |
| 5 | Spotify single track | `playlist/37i9dQZF1DXcBWIGoYBM5M` | PASS - 9,889,094 B, 410.47s mp3 |
| 6 | Spotify playlist, 2 selected tracks | same playlist (50 tracks) | PASS - 9,887,082 B + 5,320,228 B |
| 7 | SoundCloud single track | `nasa/houston-we-have-a-podcast-mars-audio-log-12` | PASS - 94,459,177 B, 3927.64s |
| 8 | SoundCloud set, items 1-2 | `nasa/sets/mars-audio-log` (13 tracks) | PASS - 860,681 B, 26.23s |
| 9 | TikTok single video | `@nasa` video | PASS - 1,085,654 B, 17.23s |
| 10 | TikTok profile, items 1-2 | `tiktok.com/@nasa` (18 videos) | PASS - 8,644,832 B, 140.88s |
| 11 | Instagram reel | `reel/C2Yg9SDL0Zt` | BLOCKED - environment, see below |

Offline suite: `python engine/tests/stress_test.py` -> **14/14 passed**.

## The one real bug (fixed)

Case 2 reported `completed`, but the file on disk was the **full 179.6s
video**, byte-identical to case 1 and at the same path. yt-dlp had logged:

```
[info] bLojKyJx7fA: Downloading 1 time ranges: 10.0-20.0
[download] ...mp4 has already been downloaded
```

The clip resolved to the same output template as the full video, so yt-dlp
skipped the download entirely and the engine happily marked it done.

**Fix:** `_output_template()` in `engine/midas_engine/core/downloader.py`
appends a ` [clip HH-MM-SS-HH-MM-SS]` suffix (before `.%(ext)s`) whenever a
section is set.

**Re-verified after the fix**, with the full video still on disk:

| Range | Duration on disk | Path |
|---|---|---|
| 10-20s | **10.0s** | `... [bLojKyJx7fA] [clip 00-00-10-00-00-20].mp4` |
| 30-45s | **15.0s** | `... [bLojKyJx7fA] [clip 00-00-30-00-00-45].mp4` |

The 179.6s original was left untouched.

## Two findings that were NOT code bugs

1. **Transient YouTube `HTTP Error 403: Forbidden`.** Cases 3 and 4 failed
   on the first run. The same selectors succeeded from the command line, and
   both cases were then re-run twice through the unchanged engine - four out
   of four `completed`. This is YouTube throttling a datacenter IP, not a
   defect. The engine already surfaces a readable message and offers Retry.
2. **Instagram requires a login from this IP.** yt-dlp returned
   `HTTP Error 429: Too Many Requests` and flagged the extractor as broken;
   the engine correctly showed *"This content needs a login (common for
   Instagram). Pick your browser under Settings > Cookies from browser, then
   retry."* On a normal residential machine with cookies-from-browser this
   path works; it cannot be validated from a datacenter IP.

## Reproducing

```bash
cd engine
python tests/stress_test.py            # offline, 14/14
python main.py --port 8765             # then drive /analyze + /downloads
```
