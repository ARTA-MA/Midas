"""MIDAS engine stress tests.

Hammers the engine's core building blocks with concurrency, malformed input
and hostile data to shake out crashes, races and slow paths:

  1. history      - concurrent sqlite upsert/list/delete/mark_interrupted
  2. events       - multi-threaded publish into asyncio queues (drop-oldest)
  3. settings     - concurrent load/save + corrupt settings.json recovery
  4. logs         - concurrent logging + ring-buffer cap
  5. chapters     - fuzzed video descriptions (huge, malformed, unsorted)
  6. spotify      - parser fuzzing + full network-failure degradation +
                    track-list cache stays bounded
  7. downloader   - progress-line fuzzing + snapshot vs. mutation race

Runs offline: when httpx / pydantic aren't installed, minimal stubs from
tests/_stubs are used (all network calls fail fast - the engine must cope).
Usage:  python tests/stress_test.py
"""
import json
import random
import string
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))       # engine root -> midas_engine
sys.path.append(str(HERE / "_stubs"))      # fallbacks only if real pkgs missing

from midas_engine import config  # noqa: E402

# Redirect all engine data into a throwaway dir BEFORE importing the rest.
_TMP = Path(tempfile.mkdtemp(prefix="midas-stress-"))
config.DATA_DIR = _TMP
config.HISTORY_DB = _TMP / "history.sqlite3"
config.SETTINGS_FILE = _TMP / "settings.json"

RESULTS = []


def check(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


def _rand_text(n):
    return "".join(random.choice(string.printable) for _ in range(n))


# ------------------------------------------------------------------ 1. history
@check("history: 16 threads x 200 mixed sqlite ops")
def _history_stress():
    from midas_engine.core import history
    errors = []

    def worker(tid):
        try:
            for i in range(200):
                item_id = f"t{tid}-{i % 25}"
                history.upsert({
                    "id": item_id, "url": f"https://example.com/{item_id}",
                    "platform": "youtube", "title": _rand_text(40),
                    "thumbnail": None, "kind": "video", "audio_only": False,
                    "overrides": {"quality": "720"}, "playlist_items": None,
                    "section": {"start_sec": 1, "end_sec": 2},
                    "file_path": None, "status": "downloading",
                    "error": None, "created_at": "2026-01-01T00:00:00Z",
                    "completed_at": None})
                if i % 20 == 0:
                    history.list_all()
                if i % 33 == 0:
                    history.delete(item_id)
                if i % 50 == 0:
                    history.mark_interrupted()
        except Exception:
            errors.append(traceback.format_exc())

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors[0]
    rows = history.list_all()
    assert isinstance(rows, list) and len(rows) <= 300
    # round-trip of JSON columns survived
    for row in rows:
        if row.get("overrides") is not None:
            assert isinstance(row["overrides"], dict), row["overrides"]
    history.clear()
    assert history.list_all() == []


# ------------------------------------------------------------------- 2. events
@check("events: 8 threads x 2000 publishes, slow consumer, bounded queues")
def _events_stress():
    import asyncio
    from midas_engine.events import EventBus
    bus = EventBus()
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    bus.attach_loop(loop)
    queues = [bus.subscribe() for _ in range(5)]
    errors = []

    def pub(tid):
        try:
            for i in range(2000):
                bus.publish({"type": "download.progress", "tid": tid, "i": i,
                             "junk": _rand_text(50)})
        except Exception:
            errors.append(traceback.format_exc())

    threads = [threading.Thread(target=pub, args=(i,)) for i in range(8)]
    [x.start() for x in threads]
    [x.join() for x in threads]
    time.sleep(0.5)  # let call_soon_threadsafe callbacks drain
    assert not errors, errors[0]
    for q in queues:
        assert q.qsize() <= 1000, f"queue overflowed: {q.qsize()}"
        bus.unsubscribe(q)
    # publishing after the loop dies must not raise (shutdown path)
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=5)
    loop.close()
    bus.publish({"type": "late"})


# ----------------------------------------------------------------- 3. settings
@check("settings: corrupt file recovery + 8 threads of load/save")
def _settings_stress():
    from midas_engine import settings as s
    # corrupt file -> defaults, never crash
    config.SETTINGS_FILE.write_text('{"max_concurrent": ###', encoding="utf-8")
    s._cached = None
    st = s.load()
    assert st.max_concurrent == 3
    errors = []

    def worker():
        try:
            for _ in range(100):
                cur = s.load()
                cur.max_concurrent = random.randint(1, 8)
                s.save(cur)
        except Exception:
            errors.append(traceback.format_exc())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors[0]
    # file is valid JSON afterwards (atomic writes never tear)
    data = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert 1 <= data["max_concurrent"] <= 8


# --------------------------------------------------------------------- 4. logs
@check("logs: 8 threads x 1000 lines, ring buffer capped")
def _logs_stress():
    from midas_engine.core import logs
    errors = []

    def worker(tid):
        try:
            for i in range(1000):
                logs.log(f"line {tid}-{i} " + _rand_text(20), source="stress")
        except Exception:
            errors.append(traceback.format_exc())

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors[0]
    entries = logs.get_all()
    assert len(entries) <= 2000, f"log buffer unbounded: {len(entries)}"


# ----------------------------------------------------------------- 5. chapters
@check("chapters: fuzzed descriptions incl. 100k-line input")
def _chapters_stress():
    from midas_engine.core.chapters import parse_description
    assert parse_description(None) == []
    assert parse_description("") == []
    assert parse_description("no timestamps here") == []
    assert parse_description("5:00 only-one") == []           # < 2 entries
    assert parse_description("1:00 a\n2:00 b") == []          # doesn't start at 0
    got = parse_description("0:00 Intro\n1:30 - Verse\n1:02:03 | Outro")
    assert got[0] == (0, "Intro") and got[-1][0] == 3723, got
    # unsorted input comes back sorted
    got = parse_description("9:59 End\n0:00 Start\n5:00 Mid")
    assert [c[0] for c in got] == sorted(c[0] for c in got)
    # hostile: huge description must stay fast and not blow up
    big = "\n".join(
        f"{i // 60}:{i % 60:02d} chapter {_rand_text(10)}" for i in range(100_000))
    t0 = time.monotonic()
    got = parse_description("0:00 zero\n" + big)
    took = time.monotonic() - t0
    assert took < 5, f"parse too slow: {took:.1f}s"
    assert got and got[0][0] == 0
    for junk in ("::::", "0:0x junk", "\x00\x01\x02", "99:99:99 overflow",
                 _rand_text(5000)):
        assert isinstance(parse_description(junk), list)


# ------------------------------------------------------------------ 6. spotify
@check("spotify: parser fuzz, offline degradation, bounded cache")
def _spotify_stress():
    from midas_engine.core import spotify
    fk = spotify._find_key
    assert fk({"a": {"b": {"trackList": [1]}}}, "trackList") == [1]
    assert fk({"a": [[{"x": 5}]]}, "x") == 5
    assert fk({}, "nope") is None
    # deeply nested structure must not smash the stack
    deep = {}
    node = deep
    for _ in range(400):
        node["child"] = {}
        node = node["child"]
    node["needle"] = 42
    try:
        found = fk(deep, "needle")
        assert found in (42, None)
    except RecursionError:
        raise AssertionError("_find_key: RecursionError on deep JSON")
    # tracklist cleaning survives garbage rows
    junk_rows = [None, 5, "x", {}, {"title": ""}, {"uri": "spotify:track:abc",
                 "title": "T", "subtitle": "A"}, {"unrelated": True}]
    cleaned = spotify._clean_tracklist(junk_rows)
    assert isinstance(cleaned, list)
    # full offline extraction: every strategy fails -> [] , no exception
    empty = spotify._extract_tracklist({"nothing": True}, "playlist", "pl0")
    assert empty == []
    # cache must stay bounded even after hundreds of distinct lookups
    for i in range(300):
        spotify._extract_tracklist({"n": i}, "playlist", f"id{i}")
    assert len(spotify._LIST_CACHE) <= 128, len(spotify._LIST_CACHE)


# --------------------------------------------------------------- 7. downloader
@check("downloader: progress fuzz + snapshot vs mutation race")
def _downloader_stress():
    from midas_engine.core import downloader
    item = downloader.DownloadItem(id="x", url="u", platform="youtube")
    apply_p = downloader.DownloadManager._apply_progress
    fn = getattr(apply_p, "__func__", apply_p)
    for line in ("", "MIDAS", "MIDAS|a|b", "|" * 40, "MIDAS|x|y|z|q|w|e|r|t",
                 "MIDAS|nan|inf|-inf|1e308|9|9|9|Title | with | pipes",
                 "MIDAS|" + "|".join(["NA"] * 8), _rand_text(2000)):
        fn(item, line)  # must never raise
    good = "MIDAS|512|1024|NA|100.5|12|1|3|My Title"
    fn(item, good)
    assert item.percent == 50.0 and item.title == "My Title"
    assert item.item_index == 1 and item.item_count == 3

    mgr = downloader.DownloadManager()
    stop = threading.Event()
    errors = []

    def mutate():
        i = 0
        while not stop.is_set():
            i += 1
            it = downloader.DownloadItem(id=f"m{i % 50}", url="u",
                                         platform="youtube")
            with mgr._cond:
                mgr.items[it.id] = it
                if it.id not in mgr._order:
                    mgr._order.append(it.id)
                if i % 7 == 0 and mgr._order:
                    victim = mgr._order.pop(0)
                    mgr.items.pop(victim, None)

    def snap():
        try:
            while not stop.is_set():
                rows = mgr.snapshot()
                assert isinstance(rows, list)
        except Exception:
            errors.append(traceback.format_exc())

    ts = [threading.Thread(target=mutate)] + \
         [threading.Thread(target=snap) for _ in range(4)]
    [t.start() for t in ts]
    time.sleep(2)
    stop.set()
    [t.join() for t in ts]
    assert not errors, errors[0]


# ------------------------------------------------------------- 8. analyzer
@check("analyzer: cache bounded/TTL, entry titles, thumbnails, fast flags")
def _analyzer_stress():
    from midas_engine.core import analyzer

    # cache: bounded, hit returns a copy, unknown misses
    analyzer._cache.clear()
    for i in range(200):
        analyzer._cache_put(f"https://example.com/{i}", {"n": i})
    assert len(analyzer._cache) <= analyzer._CACHE_MAX
    analyzer._cache_put("https://x.test/a", {"title": "T"})
    hit = analyzer._cache_get("https://x.test/a")
    assert hit == {"title": "T"}
    hit["title"] = "mutated"
    assert analyzer._cache_get("https://x.test/a") == {"title": "T"}
    assert analyzer._cache_get("https://x.test/missing") is None
    # expired entries drop out
    analyzer._cache["https://x.test/old"] = (-1e9, {"title": "old"})
    assert analyzer._cache_get("https://x.test/old") is None

    # concurrent cache access must never raise
    errors = []

    def worker(tid):
        try:
            for i in range(500):
                analyzer._cache_put(f"u{tid}-{i % 40}", {"i": i})
                analyzer._cache_get(f"u{(tid + 1) % 8}-{i % 40}")
        except Exception:
            errors.append(traceback.format_exc())

    ts = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors, errors[0]
    assert len(analyzer._cache) <= analyzer._CACHE_MAX
    analyzer._cache.clear()

    # entry titles: real title > URL slug > "Item N" (SoundCloud fix)
    et = analyzer._entry_title
    assert et({"title": "Song A"}, 0) == "Song A"
    assert et({"url": "https://soundcloud.com/artist/polarized-crh"},
              4) == "polarized crh"
    assert et({"webpage_url": "https://soundcloud.com/a/my_track+x"},
              0) == "my track x"
    # API-style URLs (digits only) are useless as names -> Item N
    assert et({"url": "https://api.soundcloud.com/tracks/303984674"},
              2) == "Item 3"
    assert et({}, 0) == "Item 1"
    assert et({"title": None, "url": None}, 9) == "Item 10"
    assert et({"url": "not a url"}, 1) == "Item 2"
    assert isinstance(et({"url": "https://x.test/%D8%AA%D8%B3%D8%AA"}, 0),
                      str)

    # thumbnails: smallest >=480 wins; graceful fallbacks
    bt = analyzer._best_thumbnail
    assert bt({"thumbnails": [
        {"url": "s", "width": 120}, {"url": "m", "width": 640},
        {"url": "xl", "width": 3840}]}) == "m"
    assert bt({"thumbnails": [{"url": "only", "width": 100}]}) == "only"
    assert bt({"thumbnails": [{"url": "nw"}], "thumbnail": "t"}) == "t"
    assert bt({"thumbnails": [{"url": "nw"}]}) == "nw"
    assert bt({"entries": [{"thumbnail": "e"}]}) == "e"
    assert bt({}) is None
    assert bt({"thumbnails": [None, 5, {"width": 500}]}) is None

    # fast analysis flags per platform
    fl = analyzer._analysis_flags(
        "https://www.youtube.com/watch?v=x", "youtube")
    assert "--socket-timeout" in fl and "--flat-playlist" in fl
    assert any("youtube:skip=" in f for f in fl)
    fl = analyzer._analysis_flags(
        "https://soundcloud.com/artist/sets/phonk", "soundcloud")
    # sets use one cheap flat request; deep per-track analysis was
    # reverted because it trips SoundCloud's rate limiter (HTTP 403)
    assert "--flat-playlist" in fl
    assert "--socket-timeout" in fl
    fl = analyzer._analysis_flags(
        "https://soundcloud.com/artist/track", "soundcloud")
    assert "--flat-playlist" in fl
    fl = analyzer._analysis_flags("https://www.instagram.com/p/x",
                                  "instagram")
    # full extraction: the clip picker needs the real post duration
    assert "--socket-timeout" in fl and "--flat-playlist" not in fl
    fl = analyzer._analysis_flags("https://www.tiktok.com/@u/video/1",
                                  "tiktok")
    assert "--flat-playlist" not in fl
    # HTTP 403 becomes a human rate-limit message, not raw yt-dlp output
    msg = analyzer._friendly_ytdlp_error(
        "ERROR: [soundcloud:set] arta/sets/sus: Unable to download JSON "
        "metadata: HTTP Error 403: Forbidden (caused by <HTTPError 403>)")
    assert "403" in msg and "rate-limit" in msg.lower()
    assert "unable to download json" not in msg.lower()

    # platform detection unchanged
    assert analyzer.detect_platform("https://youtu.be/x") == "youtube"
    assert analyzer.detect_platform("https://soundcloud.com/a/b") == \
        "soundcloud"
    assert analyzer.detect_platform("https://nope.example") is None


# ----------------------------------------------------------- 9. studio crop
@check("studio: crop rect fuzzing + video dimension probing")
def _studio_crop_stress():
    from midas_engine.core import studio

    cr = studio._crop_rect
    # sane selection -> even-sized rect inside the frame
    x, y, w, h = cr(0.25, 0.25, 0.75, 0.75, 1920, 1080)
    assert x % 2 == 0 and y % 2 == 0 and w % 2 == 0 and h % 2 == 0
    assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080
    assert (w, h) == (960, 540)
    # odd source sizes still produce valid even rects
    x, y, w, h = cr(0.0, 0.0, 0.5, 1.0, 1279, 717)
    assert w % 2 == 0 and h % 2 == 0 and x + w <= 1279 and y + h <= 717

    def must_fail(*args):
        try:
            cr(*args)
        except studio.StudioError:
            return
        raise AssertionError(f"_crop_rect accepted {args}")

    must_fail(0, 0, 1, 1, 1920, 1080)          # whole frame = no-op
    must_fail(0.5, 0.5, 0.5, 0.5, 1920, 1080)  # zero area
    must_fail(0.9, 0, 0.91, 1, 1920, 1080)     # too narrow
    must_fail(-0.2, 0, 0.5, 1, 1920, 1080)     # out of range
    must_fail(0, 0, 1.4, 1, 1920, 1080)
    must_fail("a", 0, 1, 1, 1920, 1080)        # junk types
    must_fail(None, 0, 1, 1, 1920, 1080)
    must_fail(float("nan"), 0, 0.5, 1, 1920, 1080)
    must_fail(0, 0, 0.5, 0.5, 20, 20)          # result under 16px
    # tiny fractions of a huge frame are fine
    assert cr(0.0, 0.0, 0.021, 0.021, 3840, 2160)

    # dimension probing skips attached pictures and rejects audio-only
    vd = studio._video_dimensions
    probe = {"streams": [
        {"codec_type": "audio"},
        {"codec_type": "video", "disposition": {"attached_pic": 1},
         "width": 300, "height": 300},
        {"codec_type": "video", "disposition": {"attached_pic": 0},
         "width": 1920, "height": 1080},
    ]}
    assert vd(probe) == (1920, 1080)
    for bad in ({"streams": []}, {},
                {"streams": [{"codec_type": "video", "width": 0,
                              "height": 0}]}):
        try:
            vd(bad)
            raise AssertionError("_video_dimensions accepted bad probe")
        except studio.StudioError:
            pass


# ------------------------------------------------- 10. soundcloud overrides
@check("downloader: soundcloud queues audio-only, video overrides dropped")
def _soundcloud_add_stress():
    from midas_engine.core import downloader, history
    mgr = downloader.DownloadManager()
    preview = {"platform": "soundcloud", "title": "Track",
               "thumbnail": None, "kind": "single"}
    created = mgr.add("https://soundcloud.com/a/track", "single", preview,
                      overrides={"quality": "2160", "audio_format": "flac",
                                 "video_format": "mp4"})
    assert len(created) == 1
    item = mgr.items[created[0]["id"]]
    assert item.audio_only is True
    assert item.overrides == {"audio_format": "flac"}
    # a plain youtube add keeps its quality override
    created = mgr.add("https://youtu.be/x", "single",
                      {"platform": "youtube", "title": "V", "kind":
                       "single"}, overrides={"quality": "720"})
    item = mgr.items[created[0]["id"]]
    assert item.audio_only is False
    assert item.overrides == {"quality": "720"}
    history.clear()


# ------------------------------------------- 11. spotify rate-limit breaker
@check("spotify: 429 cooldown breaker skips the slow path instantly")
def _spotify_cooldown_stress():
    from midas_engine.core import spotify

    spotify._API_COOLDOWN["until"] = 0.0
    assert spotify._api_cooling() is False

    # No Retry-After header -> the sane floor, not "retry immediately".
    spotify._api_cooldown(0.0)
    assert spotify._api_cooling() is True
    left = spotify._API_COOLDOWN["until"] - time.time()
    assert spotify._COOLDOWN_MIN - 5 <= left <= spotify._COOLDOWN_MIN + 5, left

    # Spotify's real answer is ~19 HOURS; it must be capped, never trusted.
    spotify._api_cooldown(68242.0)
    left = spotify._API_COOLDOWN["until"] - time.time()
    assert left <= spotify._COOLDOWN_MAX + 5, left

    # While cooling, the Web API is skipped without touching the network:
    # this is the ~30s of retries/sleeps the user used to wait through.
    t0 = time.monotonic()
    supply = spotify._TokenSupply("playlist", "pl", {}, spotify._Deadline(5))
    assert spotify._api_tracks("playlist", "pl", supply,
                               spotify._Deadline(5)) == []
    assert time.monotonic() - t0 < 0.10, "cooldown skip must be instant"

    # The persisted-query hash is served from memory: no bundle scraping
    # (megabytes of JS) unless a caller explicitly asks to refresh it.
    spotify._HASH_CACHE["hash"] = None
    t0 = time.monotonic()
    got = spotify._playlist_query_hash(spotify._Deadline(5), "pl")
    assert got == spotify._FALLBACK_QUERY_HASH
    assert time.monotonic() - t0 < 0.10, "hash fast path must not hit network"

    spotify._API_COOLDOWN["until"] = 0.0


# ------------------------------------------ 12. per-track artwork in the queue
@check("downloader: queue shows each track's own art, not the playlist icon")
def _downloader_artwork_stress():
    from midas_engine.core import downloader, history

    SET_ICON = "https://cdn.test/set-icon.jpg"
    preview = {
        "platform": "soundcloud", "title": "phonk", "kind": "playlist",
        "thumbnail": SET_ICON, "count": 3,
        "entries": [
            {"index": 1, "title": "Polarized", "thumbnail": "https://c/1.jpg"},
            {"index": 2, "title": "Phonkha X CRXSADER - GODDESS",
             "thumbnail": "https://c/2.jpg"},
            {"index": 3, "title": "No Art"},          # art missing -> skipped
            None, 7, {"index": 4, "thumbnail": 5},     # hostile rows
        ],
    }

    art = downloader._art_map_from_preview(preview)
    assert art["#1"] == "https://c/1.jpg"
    assert art["#2"] == "https://c/2.jpg"
    assert "#3" not in art and "#4" not in art
    assert downloader._art_map_from_preview({}) == {}
    assert downloader._art_map_from_preview({"entries": None}) == {}

    item = downloader.DownloadItem(id="a", url="u", platform="soundcloud",
                                  art_map=art)
    # Title match wins, and survives punctuation/case/spacing drift between
    # yt-dlp's reported title and the analyzer's entry title.
    assert downloader._art_for(item, "polarized", None) == "https://c/1.jpg"
    assert downloader._art_for(
        item, "PHONKHA  x  crxsader --- Goddess!!", None) == "https://c/2.jpg"
    # Unknown title falls back to the playlist index.
    assert downloader._art_for(item, "Totally Unknown", 2) == "https://c/2.jpg"
    assert downloader._art_for(item, "Totally Unknown", 99) is None
    assert downloader._art_for(item, None, None) is None
    # No map (single downloads, other platforms) -> never guesses.
    bare = downloader.DownloadItem(id="b", url="u", platform="youtube")
    assert downloader._art_for(bare, "Polarized", 1) is None

    # A playlist add starts on the FIRST TRACK's cover, not the set icon.
    mgr = downloader.DownloadManager()
    created = mgr.add("https://soundcloud.com/a/sets/s", "playlist", preview)
    queued = mgr.items[created[0]["id"]]
    assert queued.thumbnail == "https://c/1.jpg", queued.thumbnail
    assert queued.art_map, "playlist adds must carry the art map"
    assert "art_map" not in queued.to_dict(), "art map must stay internal"

    # ...and follows yt-dlp onto track 2 as the download progresses.
    apply_p = downloader.DownloadManager._apply_progress
    fn = getattr(apply_p, "__func__", apply_p)
    fn(queued, "download:MIDAS|500|1000|NA|100|5|2|3|"
                "Phonkha X CRXSADER - GODDESS")
    assert queued.thumbnail == "https://c/2.jpg", queued.thumbnail
    assert queued.title == "Phonkha X CRXSADER - GODDESS"
    # A track with no cover of its own keeps the last good image (never blank).
    fn(queued, "download:MIDAS|10|1000|NA|100|5|3|3|No Art")
    assert queued.thumbnail == "https://c/2.jpg"
    # Junk progress lines can't corrupt the artwork or crash the download.
    for junk in ("", "download:MIDAS|", "a|b|c|d|e|f|g|h|i",
                 "download:MIDAS|NaN|inf|NA|NA|NA|NA|NA|NA"):
        fn(queued, junk)
    assert queued.thumbnail == "https://c/2.jpg"

    # A single (non-playlist) add keeps the track's own preview image.
    single = mgr.add("https://soundcloud.com/a/track", "single",
                     {"platform": "soundcloud", "title": "T",
                      "kind": "single", "thumbnail": "https://c/solo.jpg"})
    assert mgr.items[single[0]["id"]].thumbnail == "https://c/solo.jpg"
    history.clear()


# -------------------------------------- 13. artwork lookups degrade offline
@check("soundcloud/analyzer: per-track art lookups degrade without network")
def _artwork_lookup_stress():
    from midas_engine.core import analyzer, soundcloud

    # Offline (or a dead set): empty maps, never an exception.
    soundcloud._PLAYLIST_CACHE.clear()
    assert soundcloud.track_art("https://soundcloud.com/a/sets/x", 0.2) == {}
    assert soundcloud.track_names("https://soundcloud.com/a/sets/x", 0.2) == {}
    assert soundcloud._lookup("https://soundcloud.com/a/sets/x",
                              "thumbnail", 0.2) == {}
    # Not a set, or another platform: no lookup is even attempted.
    assert analyzer._soundcloud_art("https://soundcloud.com/a/track",
                                    "soundcloud", [{"id": "1"}]) == {}
    assert analyzer._soundcloud_art("https://youtu.be/x", "youtube",
                                    [{"id": "1"}]) == {}
    assert analyzer._soundcloud_art("https://soundcloud.com/a/sets/x",
                                    "soundcloud", []) == {}

    # Key shapes: id, permalink, and permalink stripped of its query string.
    keys = analyzer._entry_keys({
        "id": 123,
        "webpage_url": "https://soundcloud.com/a/b?utm_source=clipboard",
        "url": "https://soundcloud.com/a/b/",
    })
    assert "123" in keys
    assert "https://soundcloud.com/a/b" in keys
    assert analyzer._entry_keys({}) == []
    assert analyzer._entry_keys({"id": None, "url": {"bad": 1}}) == []

    # Resolution order: the per-track map wins over the flat listing's image.
    art = {"123": "https://c/track.jpg"}
    entry = {"id": 123, "thumbnails": [{"url": "https://c/flat.jpg"}]}
    assert analyzer._entry_thumbnail(entry, art) == "https://c/track.jpg"
    assert analyzer._entry_thumbnail(entry, {}) == "https://c/flat.jpg"
    assert analyzer._entry_thumbnail(entry, None) == "https://c/flat.jpg"
    assert analyzer._entry_thumbnail({"id": 999}, art) is None
    assert analyzer._entry_thumbnail({}, art) is None
    # Hostile art maps must not blow up the analyzer.
    assert analyzer._entry_thumbnail({"id": 123}, {"123": ""}) is None


# ------------------------------------------------- 14. clip filename collision
@check("downloader: a clip gets its own filename, never the full video's")
def _clip_filename_collision():
    from midas_engine.core import downloader
    from midas_engine.settings import Settings

    tmpl = "%(title)s [%(id)s].%(ext)s"
    sec = {"start_sec": 10, "end_sec": 20}

    # No section -> the template is untouched.
    assert downloader._output_template(tmpl, None) == tmpl
    assert downloader._output_template(tmpl, {}) == tmpl

    # A clip gets a distinct, still-templated name with no ":" (Windows-safe).
    clip = downloader._output_template(tmpl, sec)
    assert clip == "%(title)s [%(id)s] [clip 00-00-10-00-00-20].%(ext)s", clip
    assert clip != tmpl and ":" not in clip and clip.endswith(".%(ext)s")

    # Templates that do not end in the ext marker still come out distinct.
    odd = downloader._output_template("%(title)s", sec)
    assert odd != "%(title)s" and "clip" in odd

    # Different ranges of the same video never share a filename.
    other = downloader._output_template(tmpl, {"start_sec": 30,
                                               "end_sec": 40})
    assert other != clip

    # End to end: _build_cmd must actually emit the clip-specific -o, so a
    # previously downloaded full video can't make yt-dlp skip the clip.
    mgr = downloader.DownloadManager()
    s = Settings(output_dir=str(_TMP), per_platform_subfolders=False)
    printfile = _TMP / "p.txt"

    full = downloader.DownloadItem(id="full", url="u", platform="youtube")
    cmd_full = mgr._build_cmd(full, s, printfile)
    out_full = cmd_full[cmd_full.index("-o") + 1]

    clipped = downloader.DownloadItem(id="clip", url="u", platform="youtube",
                                      section={"start_sec": 10,
                                               "end_sec": 20})
    cmd_clip = mgr._build_cmd(clipped, s, printfile)
    out_clip = cmd_clip[cmd_clip.index("-o") + 1]

    assert out_full != out_clip, (out_full, out_clip)
    assert "clip 00-00-10-00-00-20" in out_clip
    # The range itself is still requested from yt-dlp.
    assert "--download-sections" in cmd_clip
    assert "*00:00:10-00:00:20" in cmd_clip


# --------------------------------------------------------------------- runner
def main():
    print(f"MIDAS stress tests  (python {sys.version.split()[0]}, "
          f"data dir {_TMP})")
    failed = 0
    for name, fn in RESULTS:
        t0 = time.monotonic()
        try:
            fn()
            print(f"  PASS  {name}  ({time.monotonic() - t0:.2f}s)")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
