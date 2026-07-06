"""Structured runtime events for the web console."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
EVENTS_PATH = RUNTIME_DIR / "events.jsonl"
STATE_PATH = RUNTIME_DIR / "state.json"


def _read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"jobs": {}}
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"jobs": {}}
    except Exception:  # noqa: BLE001
        return {"jobs": {}}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp_path.replace(path)


def emit_event(
    platform: str,
    state: str,
    message: str = "",
    screenshot: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append an event and update the latest per-platform runtime state."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": time.time(),
        "platform": platform,
        "state": state,
        "message": message,
        "screenshot": screenshot,
        "extra": extra or {},
    }
    with EVENTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

    current = _read_state()
    jobs = current.setdefault("jobs", {})
    job = jobs.setdefault(platform, {})
    job.update(
        {
            "platform": platform,
            "state": state,
            "message": message,
            "updated_at": event["ts"],
        }
    )
    if screenshot:
        job["latest_screenshot"] = screenshot
    if extra:
        job["extra"] = extra
    _write_json(STATE_PATH, current)
    return event


def read_runtime_state() -> dict[str, Any]:
    """Read the last structured state, returning an empty state on failure."""
    return _read_state()
