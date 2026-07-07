"""Minimal self checks for console/runtime reliability guards."""

# ruff: noqa: E402

from __future__ import annotations

import json
import sys
import tempfile
from io import StringIO
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import runtime_events
from utils import verify_after_submit


class _Body:
    def __init__(self, text: str) -> None:
        self.text = text

    def inner_text(self, timeout: int = 0) -> str:
        return self.text


class _Element:
    def __init__(self, visible: bool) -> None:
        self._visible = visible

    def is_visible(self) -> bool:
        return self._visible


class _Page:
    def __init__(self, url: str, text: str, submit_visible: bool = False) -> None:
        self.url = url
        self.text = text
        self.submit_visible = submit_visible

    def wait_for_timeout(self, ms: int) -> None:
        return None

    def wait_for_load_state(self, state: str, timeout: int = 0) -> None:
        return None

    def locator(self, selector: str) -> _Body:
        return _Body(self.text)

    def query_selector(self, selector: str) -> _Element | None:
        if selector == "#submit" and self.submit_visible:
            return _Element(True)
        return None


class _FakeProc:
    _pid = 50000

    def __init__(self, stdin: bool = True) -> None:
        type(self)._pid += 1
        self.pid = type(self)._pid
        self.stdin = StringIO() if stdin else None
        self.stdout: list[str] = []

    def poll(self) -> None:
        return None

    def wait(self) -> int:
        return 0


def _check_runtime_events(tmp_dir: Path) -> None:
    runtime_events.RUNTIME_DIR = tmp_dir
    runtime_events.STATE_DIR = tmp_dir / "state"
    runtime_events.EVENTS_PATH = tmp_dir / "events.jsonl"

    runtime_events.emit_event("jd", "monitoring", "jd ok", extra={"run_id": "jd-run", "pid": 1})
    runtime_events.emit_event("dji", "need_human", "dji ok", extra={"run_id": "dji-run", "pid": 2})

    jd_state = json.loads((tmp_dir / "state" / "jd.json").read_text(encoding="utf-8"))
    dji_state = json.loads((tmp_dir / "state" / "dji.json").read_text(encoding="utf-8"))
    assert jd_state["state"] == "monitoring"
    assert dji_state["state"] == "need_human"
    assert jd_state["run_id"] == "jd-run"
    assert dji_state["run_id"] == "dji-run"
    assert "dji ok" not in (tmp_dir / "state" / "jd.json").read_text(encoding="utf-8")

    bad_path = tmp_dir / "not-a-dir"
    bad_path.write_text("x", encoding="utf-8")
    runtime_events.RUNTIME_DIR = bad_path / "child"
    runtime_events.STATE_DIR = bad_path / "child" / "state"
    runtime_events.EVENTS_PATH = bad_path / "child" / "events.jsonl"
    runtime_events.emit_event("jd", "error", "best effort should not raise")


def _check_manager_merge_and_continue(tmp_dir: Path) -> None:
    import console_app.manager as manager_mod
    from console_app.manager import RuntimeManager

    state_dir = tmp_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "jd.json").write_text(
        json.dumps({"platform": "jd", "state": "monitoring"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (state_dir / "dji.json").write_text(
        json.dumps({"platform": "dji", "state": "need_human"}, ensure_ascii=False),
        encoding="utf-8",
    )
    manager_mod.RUNTIME_STATE_DIR = state_dir
    manager = RuntimeManager()
    merged = manager._load_runtime_state()
    assert merged["jobs"]["jd"]["state"] == "monitoring"
    assert merged["jobs"]["dji"]["state"] == "need_human"

    manager.jobs["dji"]["process"] = _FakeProc()
    manager.jobs["dji"]["pid"] = manager.jobs["dji"]["process"].pid
    result = manager.continue_monitor("all")
    assert result["sent"] == ["dji"]
    assert manager.jobs["dji"]["process"].stdin.getvalue() == "\n"


def _check_verify_after_submit() -> None:
    cfg = {"selectors": {"checkout_submit": "#submit"}}
    cases: list[tuple[_Page, bool]] = [
        (_Page("https://shop.test/order/confirm", "请选择支付方式", False), False),
        (_Page("https://shop.test/checkout/order/confirm", "支付方式 微信", False), False),
        (_Page("https://shop.test/payment", "请选择支付方式", True), False),
        (_Page("https://shop.test/payment", "请选择支付方式", False), True),
        (_Page("https://shop.test/order/confirm", "订单号 12345 立即支付", False), True),
    ]
    for page, expected in cases:
        ok, msg = verify_after_submit(page, cfg, "自测")
        assert ok is expected, (page.url, page.text, ok, msg)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        _check_runtime_events(tmp_dir / "runtime")
        _check_manager_merge_and_continue(tmp_dir / "manager")
        _check_verify_after_submit()
    print("self-check-console-ok")


if __name__ == "__main__":
    main()
