#!/usr/bin/env python3
"""
MIDAS Doctor
============
Collects everything about this PC that Midas depends on, tests every layer
(vendor tools, network, browser cookies, engine, settings, history DB),
applies safe automatic fixes, and writes a full report to your Desktop:

    MIDAS-diagnostic-report.txt

Run it by double-clicking diagnose.bat in the Midas folder.
Uses only the Python standard library so it runs on the portable Python
that ships with Midas. Fully portable: touches nothing outside the Midas
folder except the report file on your Desktop.
"""

from __future__ import annotations

import ctypes
import datetime
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import traceback
from pathlib import Path

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
DATA = ROOT / "data"
ENGINE_SRC = ROOT / "engine" / "midas_engine"
SETTINGS_FILE = DATA / "settings.json"
COOKIES_TXT = DATA / "cookies.txt"
HISTORY_DB = DATA / "history.sqlite3"
DEV_PORT = 8765

# A tiny, ancient, always-public video ("Me at the zoo").
TEST_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
BROWSERS = ["chrome", "edge", "brave", "opera", "vivaldi", "firefox"]
CHROMIUM = {"chrome", "edge", "brave", "opera", "vivaldi"}
BROWSER_PROCS = {
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "brave": "brave.exe",
    "opera": "opera.exe",
    "vivaldi": "vivaldi.exe",
    "firefox": "firefox.exe",
}

report = []   # type: list
issues = []   # type: list  # (severity, text)
fixes = []    # type: list


def out(line: str = "") -> None:
    report.append(line)


def section(title: str) -> None:
    out()
    out("=" * 70)
    out(title)
    out("=" * 70)


def issue(severity: str, text: str) -> None:
    issues.append((severity, text))
    out(f"  [{severity}] {text}")


def fixed(text: str) -> None:
    fixes.append(text)
    out(f"  [FIXED] {text}")


def progress(text: str) -> None:
    print(f"  ... {text}", flush=True)


def run(cmd, timeout=60):
    """Run a command hidden, UTF-8-safe. Returns (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=NO_WINDOW,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"(timed out after {timeout}s)"
    except FileNotFoundError:
        return -2, "", "(executable not found)"
    except Exception as exc:  # noqa: BLE001
        return -3, "", f"({type(exc).__name__}: {exc})"


def desktop_dir() -> Path:
    """Real Desktop folder (handles OneDrive-redirected Desktops)."""
    if IS_WIN:
        try:
            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_ulong),
                    ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            desktop_guid = GUID(
                0xB4BFCC3A, 0xDB2C, 0x424C,
                (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41),
            )
            psz = ctypes.c_wchar_p()
            res = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(desktop_guid), 0, None, ctypes.byref(psz))
            if res == 0 and psz.value:
                path = Path(psz.value)
                ctypes.windll.ole32.CoTaskMemFree(psz)
                if path.is_dir():
                    return path
        except Exception:  # noqa: BLE001
            pass
    candidate = Path.home() / "Desktop"
    return candidate if candidate.is_dir() else Path.home()


def resolve_tool(name: str):
    """vendor-first tool resolution, mirroring engine config.resolve_tool."""
    exe = VENDOR / f"{name}.exe"
    if exe.is_file():
        return exe
    found = shutil.which(name)
    return Path(found) if found else None


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def process_running(image_name: str) -> bool:
    if not IS_WIN:
        return False
    rc, stdout, _ = run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"], timeout=20)
    return rc == 0 and image_name.lower() in stdout.lower()


def tail(text: str, n: int = 6) -> list:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-n:]


def atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- sections

def check_system() -> None:
    section("1. SYSTEM")
    out(f"  Windows        : {platform.platform()}")
    out(f"  Architecture   : {platform.machine()}")
    out(f"  Python         : {sys.version.split()[0]}  ({sys.executable})")
    out(f"  Midas folder   : {ROOT}")
    out(f"  Time           : {datetime.datetime.now().isoformat(timespec='seconds')}")
    if " " in str(ROOT):
        out("  Note           : install path contains spaces (supported, but"
            " worth knowing).")
    if not IS_WIN:
        issue("WARN", "Not running on Windows - some checks are skipped.")


def check_layout() -> None:
    section("2. MIDAS LAYOUT")
    for folder in (VENDOR, DATA, DATA / "logs"):
        out(f"  {folder.relative_to(ROOT)}\\ exists: {folder.is_dir()}")
    if VENDOR.is_dir():
        for f in sorted(VENDOR.iterdir()):
            if f.is_file():
                out(f"    vendor\\{f.name}  ({f.stat().st_size:,} bytes)")
    for tool in ("yt-dlp", "ffmpeg", "ffprobe", "deno"):
        path = resolve_tool(tool)
        if path is None:
            sev = "HIGH" if tool in ("yt-dlp", "ffmpeg") else "WARN"
            issue(sev, f"{tool} not found in vendor\\ or on PATH. Install it"
                       " from Midas Settings > Dependencies.")
        else:
            out(f"  {tool:<8}: {path}")


def check_source_freshness() -> None:
    section("3. ENGINE SOURCE VERSION")
    markers = {
        ENGINE_SRC / "core" / "analyzer.py": "_DPAPI_MESSAGE",
        ENGINE_SRC / "core" / "downloader.py": "_DPAPI_MESSAGE",
    }
    stale = []
    for path, marker in markers.items():
        try:
            fresh = marker in path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            fresh = False
        out(f"  {path.name}: {'up to date' if fresh else 'OUTDATED or missing'}")
        if not fresh:
            stale.append(path.name)
    if stale:
        issue("HIGH", "Engine source is missing the latest cookie/DPAPI fixes"
                      f" ({', '.join(stale)}). Re-extract the newest zip over"
                      " this folder, then restart run_dev.bat.")


def load_settings_dict():
    if not SETTINGS_FILE.is_file():
        return None, "missing"
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"corrupt ({type(exc).__name__})"


def check_settings():
    section("4. SETTINGS")
    settings, err = load_settings_dict()
    if err == "missing":
        out("  settings.json missing - engine will create defaults on start.")
        return {}
    if settings is None:
        backup = SETTINGS_FILE.with_name(
            f"settings.corrupt-{datetime.datetime.now():%Y%m%d-%H%M%S}.json")
        try:
            os.replace(SETTINGS_FILE, backup)
            fixed(f"settings.json was {err}; moved it to {backup.name} so the"
                  " engine regenerates clean defaults.")
        except OSError:
            issue("HIGH", f"settings.json is {err} and could not be moved"
                          " aside. Delete data\\settings.json manually.")
        return {}
    for key in sorted(settings):
        out(f"  {key} = {settings[key]!r}")
    out(f"  data\\cookies.txt present: {COOKIES_TXT.is_file()}"
        + (f" ({COOKIES_TXT.stat().st_size:,} bytes)" if COOKIES_TXT.is_file() else ""))
    return settings


def check_tools() -> None:
    section("5. TOOL VERSIONS")
    checks = [
        ("yt-dlp", ["--version"]),
        ("ffmpeg", ["-version"]),
        ("ffprobe", ["-version"]),
        ("deno", ["--version"]),
    ]
    for name, args in checks:
        path = resolve_tool(name)
        if path is None:
            out(f"  {name}: NOT FOUND")
            continue
        rc, stdout, stderr = run([path] + args, timeout=30)
        first = (stdout or stderr).splitlines()[0].strip() if (stdout or stderr) else ""
        out(f"  {name}: rc={rc}  {first[:120]}")
        if rc != 0:
            issue("HIGH", f"{name} exists but failed its version check"
                          " - the file may be corrupt. Reinstall it from"
                          " Settings > Dependencies.")


def check_chrome_version() -> None:
    out()
    out("  Installed browser versions (best effort):")
    roots = [os.environ.get("ProgramFiles"),
             os.environ.get("ProgramFiles(x86)"),
             os.environ.get("LocalAppData")]
    layouts = {
        "Chrome": "Google/Chrome/Application",
        "Edge": "Microsoft/Edge/Application",
        "Brave": "BraveSoftware/Brave-Browser/Application",
    }
    for label, rel in layouts.items():
        versions = []
        for base in roots:
            if not base:
                continue
            app = Path(base) / rel
            if app.is_dir():
                versions += [p.name for p in app.iterdir()
                             if re.match(r"\d+\.", p.name)]
        if versions:
            newest = sorted(versions, key=lambda v: [int(x) for x in v.split(".")])[-1]
            out(f"    {label}: {newest}")
            if label == "Chrome" and int(newest.split(".")[0]) >= 127:
                out("      -> Chrome 127+ uses app-bound cookie encryption;"
                    " external tools (yt-dlp) CANNOT decrypt its cookies."
                    " This is yt-dlp issue #10927, not a Midas bug.")


def check_network() -> None:
    section("6. NETWORK")
    import urllib.request
    targets = [
        ("YouTube", "https://www.youtube.com/generate_204"),
        ("Google", "https://www.google.com/generate_204"),
        ("GitHub", "https://api.github.com"),
    ]
    failures = 0
    for label, url in targets:
        progress(f"checking {label}...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "midas-doctor"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                out(f"  {label:<8}: OK (HTTP {resp.status})")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            out(f"  {label:<8}: FAILED ({type(exc).__name__}: {exc})")
    if failures == len(targets):
        issue("CRITICAL", "No HTTPS connectivity at all. Check your internet,"
                          " VPN, or firewall before anything else.")
    elif failures:
        issue("WARN", "Some sites are unreachable - a VPN/DNS/firewall issue"
                      " can cause site-specific download failures.")


def check_engine_state() -> None:
    section("7. ENGINE STATE")
    listening = port_in_use(DEV_PORT)
    out(f"  Something listening on 127.0.0.1:{DEV_PORT}: {listening}")
    for image in ("midas-engine.exe", "yt-dlp.exe"):
        running = process_running(image)
        out(f"  {image} running: {running}")
        if image == "midas-engine.exe" and running and not listening:
            issue("HIGH", "A midas-engine.exe process is running but not"
                          " serving the dev port - likely a stale/orphan"
                          " engine from an old session. Close Midas, then end"
                          " midas-engine.exe in Task Manager and relaunch.")
    if listening:
        out("  Note: an engine is live. If you re-extracted new source code"
            " AFTER this engine started, it is still running the OLD code -"
            " close Midas / press Ctrl+C in run_dev and start it again.")
    return None


def check_browser_processes() -> None:
    out()
    out("  Browser processes currently running:")
    for name, image in BROWSER_PROCS.items():
        if process_running(image):
            out(f"    {name} ({image}) is RUNNING")


def cookie_matrix(settings: dict) -> dict:
    """Try yt-dlp cookie extraction with every browser. Returns results."""
    section("8. COOKIE TESTS (the core of your current problem)")
    ytdlp = resolve_tool("yt-dlp")
    results = {}
    if ytdlp is None:
        issue("HIGH", "yt-dlp missing - cookie tests skipped.")
        return results

    out(f"  Test video: {TEST_VIDEO}")
    out(f"  Selected in settings: cookies_from_browser ='"
        f"{settings.get('cookies_from_browser', '')}'")
    out()

    # Baseline: no cookies at all.
    progress("testing without cookies (baseline)...")
    rc, stdout, stderr = run(
        [ytdlp, "--simulate", "--skip-download", "--print", "title",
         "--no-warnings", TEST_VIDEO], timeout=90)
    baseline_ok = rc == 0 and stdout.strip()
    out(f"  no cookies      : {'OK - ' + stdout.strip()[:60] if baseline_ok else 'FAILED'}")
    if not baseline_ok:
        for ln in tail(stderr, 3):
            out(f"                    {ln[:150]}")
        low = stderr.lower()
        if "not a bot" in low or "sign in" in low:
            out("                    -> YouTube is bot-checking this network;"
                " working cookies are REQUIRED on this PC/IP.")
    results[""] = bool(baseline_ok)

    # cookies.txt, if present.
    if COOKIES_TXT.is_file():
        progress("testing data\\cookies.txt...")
        rc, stdout, stderr = run(
            [ytdlp, "--cookies", COOKIES_TXT, "--simulate", "--skip-download",
             "--print", "title", "--no-warnings", TEST_VIDEO], timeout=90)
        ok = rc == 0 and stdout.strip()
        out(f"  data\\cookies.txt: {'OK' if ok else 'FAILED'}")
        if not ok:
            for ln in tail(stderr, 3):
                out(f"                    {ln[:150]}")
            issue("WARN", "data\\cookies.txt exists but doesn't work; it may"
                          " be expired. Re-export it and replace the file.")
        results["cookies.txt"] = bool(ok)

    # Every browser.
    for browser in BROWSERS:
        progress(f"testing --cookies-from-browser {browser}...")
        rc, stdout, stderr = run(
            [ytdlp, "--cookies-from-browser", browser, "--simulate",
             "--skip-download", "--print", "title", "--no-warnings",
             TEST_VIDEO], timeout=90)
        low = stderr.lower()
        if rc == 0 and stdout.strip():
            verdict = "OK"
            results[browser] = True
        elif "could not find" in low and "cookies" in low:
            verdict = "not installed / no profile"
            results[browser] = None
        elif "dpapi" in low or "failed to decrypt" in low:
            verdict = "DPAPI DECRYPT FAILED (cookies unreadable)"
            results[browser] = False
        elif rc == -1:
            verdict = "timed out"
            results[browser] = False
        else:
            verdict = "failed"
            results[browser] = False
        out(f"  {browser:<15} : {verdict}")
        if results.get(browser) is False:
            for ln in tail(stderr, 2):
                out(f"                    {ln[:150]}")
    return results


def apply_cookie_fix(settings: dict, results: dict) -> None:
    section("9. AUTOMATIC FIXES")
    if not settings or not results:
        out("  (skipped - no settings or no cookie test results)")
        return
    selected = settings.get("cookies_from_browser", "") or ""
    selected_ok = results.get(selected) is True if selected else None
    working = [b for b in BROWSERS if results.get(b) is True]

    if COOKIES_TXT.is_file() and results.get("cookies.txt") is True:
        out("  data\\cookies.txt works and always takes priority - no settings"
            " change needed.")
        return

    if selected and selected_ok:
        out(f"  Selected browser '{selected}' works. No fix needed.")
        return

    if selected and not selected_ok and working:
        best = "firefox" if "firefox" in working else working[0]
        settings["cookies_from_browser"] = best
        try:
            atomic_write_json(SETTINGS_FILE, settings)
            fixed(f"cookies_from_browser switched from '{selected}' to"
                  f" '{best}' (the only browser whose cookies this PC can"
                  " actually decrypt). Restart Midas to apply.")
        except OSError as exc:
            issue("HIGH", f"Couldn't update settings.json ({exc}). Set"
                          f" 'Cookies from browser' to '{best}' manually in"
                          " Settings.")
        return

    if selected and not selected_ok and not working:
        if results.get("") is True:
            settings["cookies_from_browser"] = ""
            try:
                atomic_write_json(SETTINGS_FILE, settings)
                fixed(f"cookies_from_browser '{selected}' can't be decrypted"
                      " on this PC and no other browser works either -"
                      " cleared the setting so public links download again."
                      " Restart Midas to apply.")
            except OSError as exc:
                issue("HIGH", f"Couldn't update settings.json ({exc})."
                              " Clear 'Cookies from browser' manually.")
        issue("HIGH",
              "No browser on this PC has cookies that yt-dlp can read"
              " (Chrome/Edge/Brave 127+ block it by design). For"
              " login-required or bot-checked content: install Firefox, log"
              " in there once, and select 'firefox' in Midas Settings - OR"
              " export cookies with the 'Get cookies.txt LOCALLY' browser"
              " extension and save the file as data\\cookies.txt inside the"
              " Midas folder.")
        return

    if not selected:
        if working:
            out(f"  No browser selected in settings; '{working[0]}' would work"
                " if you ever need logged-in content.")
        else:
            out("  No browser selected and none decryptable - fine for public"
                " links; use data\\cookies.txt for logged-in content.")


def check_history_db() -> None:
    section("10. HISTORY DATABASE")
    if not HISTORY_DB.is_file():
        out("  history.sqlite3 missing - engine will create it. OK.")
        return
    try:
        conn = sqlite3.connect(f"file:{HISTORY_DB.as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
        verdict = row[0] if row else "unknown"
        out(f"  integrity_check: {verdict}")
        if verdict != "ok":
            backup = HISTORY_DB.with_name(
                f"history.corrupt-{datetime.datetime.now():%Y%m%d-%H%M%S}.sqlite3")
            if port_in_use(DEV_PORT):
                issue("HIGH", "history.sqlite3 is corrupt but the engine is"
                              " running. Close Midas, then delete or rename"
                              " data\\history.sqlite3.")
            else:
                os.replace(HISTORY_DB, backup)
                fixed(f"Corrupt history DB moved to {backup.name}; the engine"
                      " will create a fresh one.")
    except Exception as exc:  # noqa: BLE001
        issue("WARN", f"Couldn't check history DB ({type(exc).__name__}:"
                      f" {exc}). It may be locked by a running engine - that"
                      " is normal while Midas is open.")


def clean_temp_files() -> None:
    section("11. LEFTOVER TEMP FILES")
    if not DATA.is_dir():
        out("  data\\ missing - nothing to clean.")
        return
    stale = [p for p in DATA.iterdir() if p.is_file() and (
        (p.name.startswith("files_") and p.suffix == ".txt")
        or p.name.endswith(".ffmeta.txt")
        or p.suffix == ".tmp")]
    if not stale:
        out("  None found. Clean.")
        return
    if port_in_use(DEV_PORT):
        out(f"  {len(stale)} temp file(s) found, but the engine is running -"
            " skipping cleanup (they may belong to active downloads).")
        return
    removed = 0
    for p in stale:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    if removed:
        fixed(f"Removed {removed} leftover temp file(s) from data\\.")


def dump_recent_logs() -> None:
    section("12. RECENT ENGINE LOG")
    logs_dir = DATA / "logs"
    if not logs_dir.is_dir():
        out("  No logs folder yet.")
        return
    files = sorted((p for p in logs_dir.iterdir() if p.is_file()),
                   key=lambda p: p.stat().st_mtime)
    if not files:
        out("  No log files yet.")
        return
    newest = files[-1]
    out(f"  {newest.name} (last 40 lines):")
    try:
        lines = newest.read_text(encoding="utf-8", errors="replace").splitlines()
        for ln in lines[-40:]:
            out(f"    {ln[:200]}")
    except OSError as exc:
        out(f"  Couldn't read log: {exc}")


def reproduce_analyze(settings: dict) -> None:
    """Run the exact same yt-dlp call Midas's analyzer makes."""
    section("13. EXACT ANALYZER REPRODUCTION")
    ytdlp = resolve_tool("yt-dlp")
    if ytdlp is None:
        out("  yt-dlp missing - skipped.")
        return
    cookie_args = []
    if COOKIES_TXT.is_file():
        cookie_args = ["--cookies", str(COOKIES_TXT)]
    elif settings.get("cookies_from_browser"):
        cookie_args = ["--cookies-from-browser",
                       settings["cookies_from_browser"]]
    progress("reproducing the analyzer call...")
    cmd = [ytdlp, "-J", "--no-warnings", "--flat-playlist"] + cookie_args + [TEST_VIDEO]
    out("  Command: yt-dlp -J --no-warnings --flat-playlist "
        + " ".join(str(a) for a in cookie_args) + f" {TEST_VIDEO}")
    rc, stdout, stderr = run(cmd, timeout=120)
    if rc == 0 and stdout.strip():
        try:
            title = json.loads(stdout).get("title", "?")
            out(f"  RESULT: SUCCESS - analyzed '{title}'. The analyze pipeline"
                " works with your current (possibly just-fixed) settings.")
            return
        except Exception:  # noqa: BLE001
            out("  RESULT: yt-dlp returned unreadable JSON.")
    out(f"  RESULT: FAILED (rc={rc}). Raw error tail:")
    for ln in tail(stderr, 6):
        out(f"    {ln[:200]}")


def main() -> int:
    print()
    print("MIDAS Doctor")
    print("------------")
    print("Testing your PC. This can take a few minutes; nothing is")
    print("installed or changed outside the Midas folder.")
    print()

    out("MIDAS DIAGNOSTIC REPORT")
    out(f"Generated: {datetime.datetime.now().isoformat(timespec='seconds')}")
    out(f"Midas folder: {ROOT}")

    settings = {}
    steps = [
        ("System info", check_system),
        ("Folder layout", check_layout),
        ("Source version", check_source_freshness),
    ]
    for label, fn in steps:
        progress(label)
        try:
            fn()
        except Exception:  # noqa: BLE001
            out(f"  !! {label} crashed:")
            for ln in traceback.format_exc().splitlines():
                out(f"     {ln}")

    try:
        progress("Settings")
        settings = check_settings() or {}
    except Exception:  # noqa: BLE001
        out("  !! Settings check crashed:")
        for ln in traceback.format_exc().splitlines():
            out(f"     {ln}")

    results = {}
    later = [
        ("Tool versions", check_tools),
        ("Browser versions", check_chrome_version),
        ("Network", check_network),
        ("Engine state", check_engine_state),
        ("Browser processes", check_browser_processes),
    ]
    for label, fn in later:
        progress(label)
        try:
            fn()
        except Exception:  # noqa: BLE001
            out(f"  !! {label} crashed:")
            for ln in traceback.format_exc().splitlines():
                out(f"     {ln}")

    try:
        results = cookie_matrix(settings)
    except Exception:  # noqa: BLE001
        out("  !! Cookie tests crashed:")
        for ln in traceback.format_exc().splitlines():
            out(f"     {ln}")

    for label, fn in [
        ("Automatic fixes", lambda: apply_cookie_fix(settings, results)),
        ("History DB", check_history_db),
        ("Temp cleanup", clean_temp_files),
        ("Recent logs", dump_recent_logs),
        ("Analyzer reproduction", lambda: reproduce_analyze(settings)),
    ]:
        progress(label)
        try:
            fn()
        except Exception:  # noqa: BLE001
            out(f"  !! {label} crashed:")
            for ln in traceback.format_exc().splitlines():
                out(f"     {ln}")

    # ------------------------------------------------------------- summary
    section("SUMMARY")
    if fixes:
        out("  Fixes applied automatically:")
        for f in fixes:
            out(f"    - {f}")
    else:
        out("  No automatic fixes were needed.")
    out()
    if issues:
        out("  Issues that need your attention:")
        for sev, text in issues:
            out(f"    [{sev}] {text}")
    else:
        out("  No outstanding issues found.")

    text = "\n".join(report) + "\n"
    desktop = desktop_dir()
    report_path = desktop / "MIDAS-diagnostic-report.txt"
    try:
        report_path.write_text(text, encoding="utf-8")
    except OSError:
        report_path = DATA / "MIDAS-diagnostic-report.txt"
        DATA.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text, encoding="utf-8")
    # Keep a copy next to the app too.
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "MIDAS-diagnostic-report.txt").write_text(text, encoding="utf-8")
    except OSError:
        pass

    print()
    print("Done!")
    print(f"Report saved to: {report_path}")
    if fixes:
        print()
        print("Fixes applied automatically:")
        for f in fixes:
            print(f"  - {f}")
        print("Restart Midas (close it and run run_dev.bat again) to apply.")
    if issues:
        print()
        print("Things that still need you:")
        for sev, t in issues:
            print(f"  [{sev}] {t}")
    print()
    print("Send the report file to your assistant to get targeted fixes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
