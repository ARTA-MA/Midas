"""FastAPI application: REST + one multiplexed WebSocket for realtime events.

Lifecycle safety: the Flutter shell owns this process. A heartbeat watchdog
kills the engine if the shell disappears without calling /shutdown, so no
orphan process is ever left running.
"""
import asyncio
import os
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .. import __version__, config
from ..core import analyzer, deps, history, logs, studio
from ..core.downloader import manager
from ..events import bus
from ..settings import Settings, load as load_settings, save as save_settings

WATCHDOG_TIMEOUT = 120  # seconds without any heartbeat -> exit


def _exit_now() -> None:
    """Kill any running yt-dlp job trees, then hard-exit the engine so no
    orphan yt-dlp.exe/ffmpeg.exe processes are ever left behind."""
    try:
        manager.stop_all()
    except Exception:
        pass
    os._exit(0)


class AnalyzeRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    mode: str = "single"          # single | playlist
    preview: dict = {}
    overrides: Optional[dict] = None  # per-download quality/format (TASK 6)
    items: Optional[str] = None       # --playlist-items selection (TASK 7)
    section: Optional[dict] = None    # {start_sec, end_sec} clip (TASK 9)
    selected_indices: List[int] = []  # Spotify track picker, 0-based (BUG 5)


# ------------- studio request bodies (TASK 3) -------------


class StudioImportRequest(BaseModel):
    file_path: str                       # local media file to make editable


class CoverRequest(BaseModel):
    image_base64: Optional[str] = None   # omitted = edit the existing cover
    transform: Optional[dict] = None     # {rotate, crop{x,y,width,height},
    #                                       width, height, pad_*}


class ConvertRequest(BaseModel):
    target: str                          # mp3|m4a|flac|opus|mp4|mkv
    bitrate_kbps: Optional[int] = None
    keep_original: bool = True


class SubtitleExtractRequest(BaseModel):
    stream_index: int


class SubtitleSaveRequest(BaseModel):
    content: str
    replace_index: Optional[int] = None  # stream to replace, if any
    language: Optional[str] = None


class SubtitleBurnRequest(BaseModel):
    stream_index: Optional[int] = None   # burn an embedded track...
    content: Optional[str] = None        # ...or edited SRT text
    position: str = "bottom"             # bottom | middle | top
    font_size: int = 24


class TrimRequest(BaseModel):
    segments: List[dict]                 # [{start_sec, end_sec}]
    mode: str = "keep"                   # keep | remove
    keep_original: bool = True
    precise: bool = False                # frame-accurate re-encode


class CropRequest(BaseModel):
    """Crop the picture to a region, given as fractions of the frame."""
    left: float = 0.0
    top: float = 0.0
    right: float = 1.0
    bottom: float = 1.0
    keep_original: bool = True


def create_app(watchdog: bool = True) -> FastAPI:
    state = {"last_heartbeat": time.monotonic()}

    def beat() -> None:
        state["last_heartbeat"] = time.monotonic()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        bus.attach_loop(asyncio.get_running_loop())
        manager.start()
        logs.log(f"Midas engine {__version__} ready.", source="engine")
        if watchdog:
            async def _watch() -> None:
                while True:
                    await asyncio.sleep(10)
                    if time.monotonic() - state["last_heartbeat"] > WATCHDOG_TIMEOUT:
                        _exit_now()
            asyncio.create_task(_watch())
        yield

    app = FastAPI(title="Midas Engine", version=__version__,
                  lifespan=_lifespan)

    # ------------- lifecycle -------------

    @app.get("/health")
    async def health() -> dict:
        beat()
        return {"ok": True, "version": __version__}

    @app.post("/heartbeat")
    async def heartbeat() -> dict:
        beat()
        return {"ok": True}

    @app.post("/shutdown")
    async def shutdown() -> dict:
        threading.Timer(0.3, _exit_now).start()
        return {"ok": True}

    # ------------- analysis -------------

    @app.post("/analyze")
    async def analyze(req: AnalyzeRequest) -> dict:
        beat()
        return await asyncio.to_thread(analyzer.analyze, req.url)

    # ------------- downloads -------------

    @app.get("/downloads")
    async def downloads() -> dict:
        return {"active": manager.snapshot(), "history": history.list_all()}

    @app.post("/downloads")
    async def create_download(req: DownloadRequest) -> dict:
        beat()
        try:
            items = await asyncio.to_thread(
                manager.add, req.url, req.mode, req.preview,
                req.overrides, req.items, req.section,
                req.selected_indices)
            return {"ok": True, "items": items}
        except Exception as exc:
            return {"ok": False, "message": str(exc)[:200]}

    # Queue-level pause/resume must be declared before the {item_id} routes
    # so "pause-all" is never captured as an item id.

    @app.post("/downloads/pause-all")
    async def pause_all() -> dict:
        beat()
        return {"ok": True, "count": await asyncio.to_thread(
            manager.pause_all)}

    @app.post("/downloads/resume-all")
    async def resume_all() -> dict:
        beat()
        return {"ok": True, "count": await asyncio.to_thread(
            manager.resume_all)}

    @app.delete("/downloads/{item_id}")
    async def cancel_download(item_id: str) -> dict:
        return {"ok": manager.cancel(item_id)}

    @app.post("/downloads/{item_id}/retry")
    async def retry_download(item_id: str) -> dict:
        return {"ok": manager.retry(item_id)}

    @app.post("/downloads/{item_id}/pause")
    async def pause_download(item_id: str) -> dict:
        return {"ok": manager.pause(item_id)}

    @app.post("/downloads/{item_id}/resume")
    async def resume_download(item_id: str) -> dict:
        return {"ok": manager.resume(item_id)}

    @app.post("/downloads/{item_id}/open-folder")
    async def open_folder(item_id: str) -> dict:
        item = manager.items.get(item_id)
        path = item.file_path if item else None
        if not path:
            for row in history.list_all():
                if row["id"] == item_id and row.get("file_path"):
                    path = row["file_path"]
                    break
        if not path or not Path(path).exists():
            return {"ok": False}
        if config.IS_WINDOWS:
            subprocess.Popen(["explorer", "/select,", path])
        return {"ok": True}

    @app.post("/history/clear")
    async def clear_history() -> dict:
        history.clear()
        return {"ok": True}

    @app.delete("/history/{item_id}")
    async def delete_history_item(item_id: str) -> dict:
        """Remove one finished row instead of wiping the whole history."""
        history.delete(item_id)
        # Drop the in-memory copy too (finished items only) so the row
        # doesn't resurface on the next /downloads poll.
        item = manager.items.get(item_id)
        if item is not None and getattr(item, "status", "") in (
                "completed", "error", "cancelled"):
            manager.items.pop(item_id, None)
        return {"ok": True}

    # ------------- studio (TASK 3) -------------

    async def _studio(fn, *args) -> dict:
        """Run a blocking studio operation off the event loop, mapping
        StudioError to a short friendly message (never a traceback)."""
        beat()
        try:
            result = await asyncio.to_thread(fn, *args)
            return {"ok": True, **(result or {})}
        except studio.StudioError as exc:
            return {"ok": False, "message": str(exc)[:200]}
        except Exception as exc:
            logs.log(f"Studio operation failed: {type(exc).__name__}: "
                     f"{str(exc)[:200]}", level="error", source="studio")
            return {"ok": False,
                    "message": "Something went wrong during this edit."}

    @app.get("/studio/items")
    async def studio_items() -> dict:
        return await _studio(studio.list_items)

    @app.post("/studio/import")
    async def studio_import(req: StudioImportRequest) -> dict:
        return await _studio(studio.import_local_file, req.file_path)

    @app.get("/studio/{item_id}/cover")
    async def studio_cover(item_id: str) -> Response:
        beat()
        try:
            data, mime = await asyncio.to_thread(studio.get_cover, item_id)
            return Response(content=data, media_type=mime)
        except studio.StudioError:
            return Response(status_code=404)
        except Exception:
            return Response(status_code=404)

    @app.post("/studio/{item_id}/cover")
    async def studio_set_cover(item_id: str, req: CoverRequest) -> dict:
        return await _studio(studio.set_cover, item_id, req.image_base64,
                             req.transform)

    @app.post("/studio/{item_id}/convert")
    async def studio_convert(item_id: str, req: ConvertRequest) -> dict:
        return await _studio(studio.convert, item_id, req.target,
                             req.bitrate_kbps, req.keep_original)

    @app.get("/studio/{item_id}/subtitles")
    async def studio_subtitles(item_id: str) -> dict:
        return await _studio(studio.list_subtitles, item_id)

    @app.post("/studio/{item_id}/subtitles/extract")
    async def studio_subtitles_extract(item_id: str,
                                       req: SubtitleExtractRequest) -> dict:
        return await _studio(studio.extract_subtitle, item_id,
                             req.stream_index)

    @app.put("/studio/{item_id}/subtitles")
    async def studio_subtitles_save(item_id: str,
                                    req: SubtitleSaveRequest) -> dict:
        return await _studio(studio.save_subtitle, item_id, req.content,
                             req.replace_index, req.language)

    @app.delete("/studio/{item_id}/subtitles/{stream_index}")
    async def studio_subtitles_delete(item_id: str,
                                      stream_index: int) -> dict:
        return await _studio(studio.delete_subtitle, item_id, stream_index)

    @app.post("/studio/{item_id}/subtitles/burn")
    async def studio_subtitles_burn(item_id: str,
                                    req: SubtitleBurnRequest) -> dict:
        return await _studio(studio.burn_subtitle, item_id, req.stream_index,
                             req.content, req.position, req.font_size)

    @app.post("/studio/{item_id}/trim")
    async def studio_trim(item_id: str, req: TrimRequest) -> dict:
        return await _studio(studio.trim, item_id, req.segments, req.mode,
                             req.keep_original, req.precise)

    @app.get("/studio/{item_id}/frame")
    async def studio_frame(item_id: str, t: float = 0.0) -> Response:
        beat()
        try:
            data, mime = await asyncio.to_thread(studio.get_frame,
                                                 item_id, t)
            return Response(content=data, media_type=mime)
        except studio.StudioError:
            return Response(status_code=404)
        except Exception:
            return Response(status_code=404)

    @app.post("/studio/{item_id}/crop")
    async def studio_crop(item_id: str, req: CropRequest) -> dict:
        return await _studio(studio.crop, item_id, req.left, req.top,
                             req.right, req.bottom, req.keep_original)

    # ------------- settings -------------

    @app.get("/settings")
    async def get_settings() -> Settings:
        return load_settings()

    @app.put("/settings")
    async def put_settings(new: Settings) -> Settings:
        return save_settings(new)

    # ------------- dependencies -------------

    @app.get("/deps")
    async def deps_status() -> dict:
        beat()
        return await asyncio.to_thread(deps.status)

    @app.post("/deps/{name}/install")
    async def deps_install(name: str) -> dict:
        beat()
        return await asyncio.to_thread(deps.install, name)

    # ------------- developer logs -------------

    @app.get("/logs")
    async def get_logs() -> list:
        return logs.get_all()

    # ------------- realtime events -------------

    @app.websocket("/events")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        q = bus.subscribe()

        async def _sender() -> None:
            while True:
                await ws.send_text(await q.get())

        sender = asyncio.create_task(_sender())
        try:
            while True:
                await ws.receive_text()  # any client message = heartbeat
                beat()
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            bus.unsubscribe(q)

    return app
