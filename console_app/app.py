"""FastAPI app for the local seckill console."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config_store import PROJECT_ROOT, get_console_config, update_console_config
from .manager import runtime_manager

STATIC_DIR = Path(__file__).resolve().parent / "static"
SCREENSHOT_DIR = PROJECT_ROOT / "screenshots"

app = FastAPI(title="Pocket 4P 本地抢购控制台")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    return runtime_manager.get_status()


@app.get("/api/config")
def api_get_config() -> dict[str, Any]:
    return get_console_config()


@app.post("/api/config")
async def api_update_config(payload: dict[str, Any]) -> dict[str, Any]:
    return update_console_config(payload)


@app.post("/api/login-check")
async def api_login_check(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return runtime_manager.start_login_check(str(payload.get("platform", "")))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/login-check/confirm")
async def api_login_check_confirm() -> dict[str, Any]:
    return runtime_manager.confirm_login_check()


@app.post("/api/start")
async def api_start(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return runtime_manager.start_monitor(
            jd_enabled=bool(payload.get("jd_enabled", False)),
            dji_enabled=bool(payload.get("dji_enabled", False)),
            dry_run=bool(payload.get("dry_run", True)),
            confirm_text=str(payload.get("confirm_text", "")),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/stop")
async def api_stop() -> dict[str, Any]:
    return runtime_manager.stop_monitor()


@app.get("/api/logs")
def api_logs() -> dict[str, Any]:
    return {"logs": runtime_manager.get_logs(300)}


@app.get("/api/logs/stream")
def api_logs_stream() -> StreamingResponse:
    def event_stream():
        subscriber = runtime_manager.subscribe()
        try:
            for line in runtime_manager.get_logs(300):
                yield f"data: {json.dumps(line, ensure_ascii=False)}\n\n"
            while True:
                try:
                    line = subscriber.get(timeout=15)
                    yield f"data: {json.dumps(line, ensure_ascii=False)}\n\n"
                except Exception:
                    yield ": ping\n\n"
        finally:
            runtime_manager.unsubscribe(subscriber)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/screenshot/latest")
def api_latest_screenshot():
    files = [path for path in SCREENSHOT_DIR.glob("*.png") if path.is_file()]
    if not files:
        return JSONResponse({"detail": "no screenshot"}, status_code=404)
    latest = max(files, key=lambda path: path.stat().st_mtime)
    headers = {"Cache-Control": "no-store", "X-Screenshot-Mtime": str(latest.stat().st_mtime)}
    return FileResponse(latest, media_type="image/png", headers=headers, filename=latest.name)


@app.get("/api/screenshot/latest-meta")
def api_latest_screenshot_meta() -> dict[str, Any]:
    files = [path for path in SCREENSHOT_DIR.glob("*.png") if path.is_file()]
    if not files:
        return {"exists": False}
    latest = max(files, key=lambda path: path.stat().st_mtime)
    return {
        "exists": True,
        "name": latest.name,
        "mtime": latest.stat().st_mtime,
        "mtime_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest.stat().st_mtime)),
    }
