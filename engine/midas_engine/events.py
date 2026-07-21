"""In-process event bus fanning out to every connected WebSocket client.

Event shape: {"type": str, ...payload}. Types used across the app:
  download.progress / download.state / deps.progress / deps.state / queue.changed
"""
import asyncio
import json
import threading
from typing import Any, Dict, Set


class EventBus:
    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: Dict[str, Any]) -> None:
        """Thread-safe publish (worker threads call this)."""
        if self._loop is None:
            return
        payload = json.dumps(event, default=str)
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            def _put(q=q):
                if q.full():
                    try:
                        q.get_nowait()  # drop oldest, never block downloads
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(payload)
            try:
                self._loop.call_soon_threadsafe(_put)
            except RuntimeError:
                # Loop already closed (engine shutting down) - drop the event
                # instead of crashing the worker thread.
                pass


bus = EventBus()
