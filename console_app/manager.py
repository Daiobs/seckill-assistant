"""Subprocess runtime manager for the local web console."""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .config_store import validate_live_start
from .status_parser import (
    STATUS_ERROR,
    STATUS_NEEDS_HUMAN,
    STATUS_RUNNING,
    STATUS_STOPPED,
    STATUS_WAITING,
    parse_status_from_line,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class RuntimeManager:
    """Manage monitor/login subprocesses and expose runtime state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._logs: deque[str] = deque(maxlen=1000)
        self._subscribers: set[queue.Queue[str]] = set()
        self.monitor_process: subprocess.Popen[str] | None = None
        self.login_process: subprocess.Popen[str] | None = None
        self.status = STATUS_WAITING
        self.platform = ""
        self.dry_run = True
        self.started_at: float | None = None
        self.last_error = ""

    def _popen(self, args: list[str]) -> subprocess.Popen[str]:
        kwargs: dict[str, Any] = {
            "cwd": str(PROJECT_ROOT),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(args, **kwargs)

    def _append_log(self, line: str) -> None:
        ts = time.strftime("%H:%M:%S")
        clean_line = line.rstrip("\n")
        formatted = f"{ts} {clean_line}" if clean_line else ts
        with self._lock:
            self._logs.append(formatted)
            self.status = parse_status_from_line(clean_line, self.status)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(formatted)
            except queue.Full:
                pass

    def _reader(self, proc: subprocess.Popen[str], kind: str) -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                self._append_log(line)
        finally:
            return_code = proc.wait()
            with self._lock:
                if kind == "monitor" and self.monitor_process is proc:
                    if return_code == 0 and self.status not in (STATUS_NEEDS_HUMAN,):
                        self.status = STATUS_STOPPED
                    elif return_code != 0:
                        self.status = STATUS_ERROR
                        self.last_error = f"监控进程退出码 {return_code}"
                    self.monitor_process = None
                if kind == "login" and self.login_process is proc:
                    if return_code != 0:
                        self.last_error = f"登录检查退出码 {return_code}"
                        self.status = STATUS_ERROR
                    elif not self.monitor_process:
                        self.status = STATUS_STOPPED
                    self.login_process = None
            self._append_log(f"[console] {kind} process exited with code {return_code}")

    def start_monitor(
        self,
        jd_enabled: bool,
        dji_enabled: bool,
        dry_run: bool,
        confirm_text: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if self.monitor_process and self.monitor_process.poll() is None:
                raise RuntimeError("已有监控进程在运行，请先停止")
            if not jd_enabled and not dji_enabled:
                raise RuntimeError("至少启用一个平台")

            platforms = []
            if jd_enabled:
                platforms.append("jd")
            if dji_enabled:
                platforms.append("dji")

            if not dry_run:
                if confirm_text != "我确认":
                    raise RuntimeError("实战模式必须输入“我确认”")
                ok, reason = validate_live_start(platforms)
                if not ok:
                    raise RuntimeError(reason)

            if jd_enabled and dji_enabled:
                platform_arg = "both"
            elif jd_enabled:
                platform_arg = "jd"
            else:
                platform_arg = "dji"

            args = [
                sys.executable,
                "scripts/run_sale.py",
                "--platform",
                platform_arg,
                "--dry-run" if dry_run else "--no-dry-run",
            ]
            proc = self._popen(args)
            self.monitor_process = proc
            self.platform = platform_arg
            self.dry_run = dry_run
            self.started_at = time.time()
            self.status = STATUS_RUNNING
            self.last_error = ""
            self._append_log(f"[console] started monitor: {' '.join(args)}")
            threading.Thread(target=self._reader, args=(proc, "monitor"), daemon=True).start()
            return self.get_status()

    def stop_monitor(self) -> dict[str, Any]:
        with self._lock:
            proc = self.monitor_process
        if proc and proc.poll() is None:
            self._terminate_process(proc)
        with self._lock:
            self.monitor_process = None
            self.status = STATUS_STOPPED
        self._append_log("[console] monitor stopped by user")
        return self.get_status()

    def start_login_check(self, platform: str) -> dict[str, Any]:
        if platform not in ("jd", "dji"):
            raise RuntimeError("platform 必须是 jd 或 dji")
        with self._lock:
            if self.login_process and self.login_process.poll() is None:
                raise RuntimeError("已有登录检查进程在运行")
            args = [
                sys.executable,
                "scripts/run_sale.py",
                "--platform",
                platform,
                "--check-login",
            ]
            proc = self._popen(args)
            self.login_process = proc
            self.status = STATUS_NEEDS_HUMAN
            self._append_log(
                "[console] 登录检查浏览器已打开，请在浏览器中完成登录，完成后点击【完成登录检查】"
            )
            self._append_log(f"[console] started login check: {' '.join(args)}")
            threading.Thread(target=self._reader, args=(proc, "login"), daemon=True).start()
            return self.get_status()

    def confirm_login_check(self) -> dict[str, Any]:
        with self._lock:
            proc = self.login_process
        if proc and proc.poll() is None and proc.stdin:
            try:
                proc.stdin.write("\n")
                proc.stdin.flush()
                self._append_log("[console] 已向登录检查进程发送 Enter")
            except OSError as exc:
                self.last_error = str(exc)
        else:
            self._append_log("[console] 当前没有等待中的登录检查进程")
        return self.get_status()

    def _terminate_process(self, proc: subprocess.Popen[str]) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            proc = self.monitor_process
            login_proc = self.login_process
            return {
                "status": self.status,
                "platform": self.platform,
                "dry_run": self.dry_run,
                "pid": proc.pid if proc and proc.poll() is None else None,
                "login_check_pid": login_proc.pid if login_proc and login_proc.poll() is None else None,
                "started_at": self.started_at,
                "last_error": self.last_error,
            }

    def get_logs(self, limit: int = 300) -> list[str]:
        with self._lock:
            return list(self._logs)[-limit:]

    def subscribe(self) -> queue.Queue[str]:
        subscriber: queue.Queue[str] = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[str]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)


runtime_manager = RuntimeManager()
