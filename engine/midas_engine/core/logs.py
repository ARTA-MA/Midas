"""In-memory ring buffer of app log lines, streamed live to the UI.

The Flutter shell shows these in the optional developer log panel at the
bottom of the Home screen (Settings > Developers > Show logs).
"""
import threading
import time
from collections import deque
from typing import List

from ..events import bus

_MAX_LINES = 500
_lines: deque = deque(maxlen=_MAX_LINES)
_lock = threading.Lock()


def log(message: str, level: str = "info", source: str = "app") -> None:
    """Record a log line and broadcast it as a `log.line` event."""
    entry = {"ts": time.strftime("%H:%M:%S"), "level": level,
             "source": source, "message": str(message)[:600]}
    with _lock:
        _lines.append(entry)
    bus.publish({"type": "log.line", **entry})


def get_all() -> List[dict]:
    with _lock:
        return list(_lines)
