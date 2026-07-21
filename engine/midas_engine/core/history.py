"""SQLite download history (thread-safe)."""
import json
import sqlite3
import threading
from typing import List

from .. import config

_lock = threading.Lock()

# Columns added after the first release; applied with ALTER TABLE so old
# databases migrate in place (sqlite ignores nothing - errors mean 'exists').
_MIGRATIONS = (
    ("kind", "TEXT"),
    ("audio_only", "INTEGER"),
    ("overrides", "TEXT"),        # JSON: per-download quality/format override
    ("playlist_items", "TEXT"),   # yt-dlp --playlist-items selection
    ("section", "TEXT"),          # JSON: {start_sec, end_sec} clip range
)

_COLUMNS = ("id", "url", "platform", "title", "thumbnail", "kind",
            "audio_only", "overrides", "playlist_items", "section",
            "file_path", "status", "error", "created_at", "completed_at")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.HISTORY_DB), check_same_thread=False)
    conn.execute("""
CREATE TABLE IF NOT EXISTS downloads (
  id TEXT PRIMARY KEY,
  url TEXT, platform TEXT, title TEXT, thumbnail TEXT,
  kind TEXT, audio_only INTEGER, overrides TEXT, playlist_items TEXT,
  section TEXT,
  file_path TEXT, status TEXT, error TEXT,
  created_at TEXT, completed_at TEXT
)""")
    for column, decl in _MIGRATIONS:
        try:
            conn.execute(
                f"ALTER TABLE downloads ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.execute("CREATE INDEX IF NOT EXISTS idx_downloads_created "
                 "ON downloads(created_at DESC)")
    conn.commit()
    return conn


try:
    _conn = _connect()
except sqlite3.DatabaseError:
    # Corrupt DB (e.g. force-kill mid-write): start fresh, never crash startup.
    try:
        config.HISTORY_DB.unlink(missing_ok=True)
    except OSError:
        pass
    _conn = _connect()


def _to_row(item: dict) -> dict:
    """JSON-encode dict fields and coerce bools so sqlite can bind them."""
    row = {k: item.get(k) for k in _COLUMNS}
    for key in ("overrides", "section"):
        if isinstance(row.get(key), dict):
            row[key] = json.dumps(row[key])
    if row.get("audio_only") is not None:
        row["audio_only"] = int(bool(row["audio_only"]))
    return row


def _from_row(row: dict) -> dict:
    for key in ("overrides", "section"):
        if row.get(key):
            try:
                row[key] = json.loads(row[key])
            except (TypeError, ValueError):
                row[key] = None
    if row.get("audio_only") is not None:
        row["audio_only"] = bool(row["audio_only"])
    return row


def upsert(item: dict) -> None:
    with _lock:
        _conn.execute(
            """INSERT INTO downloads (id,url,platform,title,thumbnail,kind,
                 audio_only,overrides,playlist_items,section,file_path,
                 status,error,created_at,completed_at)
               VALUES (:id,:url,:platform,:title,:thumbnail,:kind,
                 :audio_only,:overrides,:playlist_items,:section,:file_path,
                 :status,:error,:created_at,:completed_at)
               ON CONFLICT(id) DO UPDATE SET
                 title=:title, thumbnail=:thumbnail, kind=:kind,
                 audio_only=:audio_only, overrides=:overrides,
                 playlist_items=:playlist_items, section=:section,
                 file_path=:file_path,
                 status=:status, error=:error, completed_at=:completed_at""",
            _to_row(item))
        _conn.commit()


def list_all(limit: int = 300) -> List[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT " + ",".join(_COLUMNS) + " FROM downloads "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_from_row(dict(zip(_COLUMNS, r))) for r in rows]


def clear() -> None:
    with _lock:
        _conn.execute("DELETE FROM downloads")
        _conn.commit()


def delete(item_id: str) -> None:
    """Remove a single row (per-item delete in the Downloads screen)."""
    with _lock:
        _conn.execute("DELETE FROM downloads WHERE id=?", (item_id,))
        _conn.commit()


def mark_interrupted() -> None:
    """Rows left in a live state by a previous engine run can never finish;
    mark them cancelled so the UI offers Retry instead of a stuck spinner.
    'paused' rows are deliberately left as-is: they resume fine across
    engine restarts (yt-dlp continues from the kept .part files)."""
    with _lock:
        _conn.execute(
            "UPDATE downloads SET status='cancelled' WHERE status IN "
            "('queued','starting','downloading','processing')")
        _conn.commit()
