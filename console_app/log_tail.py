"""Utilities for reading recent file logs."""

from __future__ import annotations

from collections import deque
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def latest_log_file() -> Path | None:
    log_dir = PROJECT_ROOT / "logs"
    files = [path for path in log_dir.glob("*.log") if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def tail_latest_log(max_lines: int = 300) -> list[str]:
    path = latest_log_file()
    if path is None:
        return []
    lines: deque[str] = deque(maxlen=max_lines)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                lines.append(line.rstrip("\n"))
    except OSError:
        return []
    return list(lines)

