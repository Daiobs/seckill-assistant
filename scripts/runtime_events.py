"""Structured runtime events for the web console."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
STATE_DIR = RUNTIME_DIR / "state"
EVENTS_PATH = RUNTIME_DIR / "events.jsonl"
PLATFORMS = ("jd", "dji")

logger = logging.getLogger("seckill.runtime_events")


def _read_platform_state(platform: str) -> dict[str, Any]:
    path = STATE_DIR / f"{platform}.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 runtime state 失败：platform=%s error=%s", platform, exc)
        return {}


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        raise


def _event_extra(platform: str, extra: dict[str, Any] | None) -> dict[str, Any]:
    event_extra = dict(extra or {})
    if os.environ.get("SECKILL_RUN_ID") and "run_id" not in event_extra:
        event_extra["run_id"] = os.environ["SECKILL_RUN_ID"]
    if os.environ.get("SECKILL_PLATFORM") == platform and "pid" not in event_extra:
        event_extra["pid"] = os.getpid()
    return event_extra


def emit_event(
    platform: str,
    state: str,
    message: str = "",
    screenshot: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append an event and update per-platform runtime state, best-effort only."""
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    event_extra = _event_extra(platform, extra)
    event = {
        "ts": ts,
        "platform": platform,
        "state": state,
        "message": message,
        "screenshot": screenshot,
        "extra": event_extra,
    }
    if "run_id" in event_extra:
        event["run_id"] = event_extra["run_id"]
    if "pid" in event_extra:
        event["pid"] = event_extra["pid"]

    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        with EVENTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入 runtime events.jsonl 失败：platform=%s error=%s", platform, exc)

    try:
        job = {
            "platform": platform,
            "state": state,
            "message": message,
            "updated_at": ts,
        }
        if screenshot:
            job["latest_screenshot"] = screenshot
        if event_extra:
            job["extra"] = event_extra
        if "run_id" in event_extra:
            job["run_id"] = event_extra["run_id"]
        if "pid" in event_extra:
            job["pid"] = event_extra["pid"]
        _write_json_atomic(STATE_DIR / f"{platform}.json", job)
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入 runtime state 失败：platform=%s error=%s", platform, exc)

    return event


def read_runtime_state() -> dict[str, Any]:
    """Read merged per-platform runtime state."""
    jobs = {
        platform: state
        for platform in PLATFORMS
        if (state := _read_platform_state(platform))
    }
    return {"jobs": jobs}
