"""Subprocess runtime manager for the local web console."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from .config_store import validate_live_start
from .status_parser import (
    STATUS_CHECKOUT,
    STATUS_ERROR,
    STATUS_NEEDS_HUMAN,
    STATUS_RUNNING,
    STATUS_STOCK_FOUND,
    STATUS_STOPPED,
    STATUS_SUBMITTED,
    STATUS_WAITING,
    parse_status_from_line,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime_events import emit_event  # noqa: E402

RUNTIME_STATE_PATH = PROJECT_ROOT / ".runtime" / "state.json"
DIAGNOSTICS_DIR = PROJECT_ROOT / ".runtime" / "diagnostics"
PLATFORMS = ("jd", "dji")
AGGREGATE_PRIORITY = (
    STATUS_SUBMITTED,
    STATUS_CHECKOUT,
    STATUS_STOCK_FOUND,
    STATUS_NEEDS_HUMAN,
    STATUS_ERROR,
    STATUS_RUNNING,
    STATUS_WAITING,
    STATUS_STOPPED,
)
EVENT_STATE_MAP = {
    "waiting": STATUS_WAITING,
    "monitoring": STATUS_RUNNING,
    "stock_found": STATUS_STOCK_FOUND,
    "checkout": STATUS_CHECKOUT,
    "submitted": STATUS_SUBMITTED,
    "need_human": STATUS_NEEDS_HUMAN,
    "stopped": STATUS_STOPPED,
    "error": STATUS_ERROR,
}


def _empty_job(platform: str) -> dict[str, Any]:
    return {
        "platform": platform,
        "process": None,
        "run_id": "",
        "pid": None,
        "enabled": False,
        "dry_run": True,
        "status": STATUS_WAITING,
        "started_at": None,
        "last_error": "",
        "logs": deque(maxlen=300),
        "latest_screenshot": "",
        "waiting_for_human": False,
    }


class RuntimeManager:
    """Manage monitor/login subprocesses and expose runtime state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._logs: deque[str] = deque(maxlen=1000)
        self._subscribers: set[queue.Queue[str]] = set()
        self.jobs: dict[str, dict[str, Any]] = {
            platform: _empty_job(platform) for platform in PLATFORMS
        }
        self.login_process: subprocess.Popen[str] | None = None
        self.login_platform = ""
        self.last_error = ""

    def _popen(
        self,
        args: list[str],
        env_extra: dict[str, str] | None = None,
    ) -> subprocess.Popen[str]:
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
        if env_extra:
            kwargs["env"] = {**os.environ, **env_extra}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(args, **kwargs)

    def _append_log(self, line: str, platform: str | None = None) -> None:
        ts = time.strftime("%H:%M:%S")
        clean_line = line.rstrip("\n")
        if platform and not clean_line.startswith(f"[{platform}]"):
            clean_line = f"[{platform}] {clean_line}"
        formatted = f"{ts} {clean_line}" if clean_line else ts
        with self._lock:
            self._logs.append(formatted)
            if platform in self.jobs:
                job = self.jobs[platform]
                job["status"] = parse_status_from_line(clean_line, job["status"])
                job["waiting_for_human"] = job["status"] == STATUS_NEEDS_HUMAN
                job["logs"].append(formatted)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(formatted)
            except queue.Full:
                pass

    def _reader(self, proc: subprocess.Popen[str], kind: str, platform: str = "") -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                self._append_log(line, platform if kind == "monitor" else None)
        finally:
            return_code = proc.wait()
            with self._lock:
                if kind == "monitor" and platform in self.jobs:
                    job = self.jobs[platform]
                    if job.get("process") is proc:
                        if return_code == 0 and job["status"] != STATUS_NEEDS_HUMAN:
                            job["status"] = STATUS_STOPPED
                            job["waiting_for_human"] = False
                        elif return_code != 0:
                            job["status"] = STATUS_ERROR
                            job["waiting_for_human"] = False
                            job["last_error"] = f"{platform} 监控进程退出码 {return_code}"
                            self.last_error = job["last_error"]
                            emit_event(
                                platform,
                                "error",
                                job["last_error"],
                                extra={"run_id": job.get("run_id", ""), "pid": proc.pid},
                            )
                        else:
                            emit_event(
                                platform,
                                "stopped",
                                f"{platform} 监控进程已退出",
                                extra={"run_id": job.get("run_id", ""), "pid": proc.pid},
                            )
                        job["process"] = None
                        job["pid"] = None
                if kind == "login" and self.login_process is proc:
                    if return_code != 0:
                        self.last_error = f"登录检查退出码 {return_code}"
                    self.login_process = None
                    self.login_platform = ""
            self._append_log(
                f"[console] {kind} process exited with code {return_code}",
                platform if kind == "monitor" else None,
            )

    def _running_job(self, platform: str) -> subprocess.Popen[str] | None:
        job = self.jobs[platform]
        proc = job.get("process")
        if proc and proc.poll() is None:
            return proc
        return None

    def start_monitor(
        self,
        jd_enabled: bool,
        dji_enabled: bool,
        dry_run: bool,
        confirm_text: str = "",
    ) -> dict[str, Any]:
        platforms = []
        if jd_enabled:
            platforms.append("jd")
        if dji_enabled:
            platforms.append("dji")
        if not platforms:
            raise RuntimeError("至少启用一个平台")
        with self._lock:
            running = [platform for platform in platforms if self._running_job(platform)]
            if running:
                raise RuntimeError(f"已有监控进程在运行：{', '.join(running)}")

        if not dry_run:
            if confirm_text != "我确认":
                raise RuntimeError("实战模式必须输入“我确认”")
            ok, reason = validate_live_start(platforms)
            if not ok:
                raise RuntimeError(reason)

        for platform in platforms:
            run_id = f"{platform}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
            args = [
                sys.executable,
                "scripts/run_sale.py",
                "--platform",
                platform,
                "--dry-run" if dry_run else "--no-dry-run",
            ]
            proc = self._popen(
                args,
                env_extra={"SECKILL_RUN_ID": run_id, "SECKILL_PLATFORM": platform},
            )
            started_at = time.time()
            with self._lock:
                job = self.jobs[platform]
                job.update(
                    {
                        "process": proc,
                        "run_id": run_id,
                        "pid": proc.pid,
                        "enabled": True,
                        "dry_run": dry_run,
                        "status": STATUS_RUNNING,
                        "started_at": started_at,
                        "last_error": "",
                        "latest_screenshot": "",
                        "waiting_for_human": False,
                    }
                )
                self.last_error = ""
            emit_event(
                platform,
                "monitoring",
                f"{platform} 监控进程已启动",
                extra={"run_id": run_id, "pid": proc.pid},
            )
            self._append_log(f"[console] started monitor: {' '.join(args)}", platform)
            threading.Thread(
                target=self._reader,
                args=(proc, "monitor", platform),
                daemon=True,
            ).start()
        return self.get_status()

    def stop_monitor(self, platform: str = "all") -> dict[str, Any]:
        platforms = self._target_platforms(platform)
        for target in platforms:
            with self._lock:
                proc = self._running_job(target)
            if proc:
                self._terminate_process(proc)
            with self._lock:
                job = self.jobs[target]
                job["process"] = None
                job["pid"] = None
                job["status"] = STATUS_STOPPED
                job["waiting_for_human"] = False
            emit_event(
                target,
                "stopped",
                f"{target} 监控已由控制台停止",
                extra={"run_id": job.get("run_id", ""), "pid": proc.pid if proc else job.get("pid")},
            )
            self._append_log("[console] monitor stopped by user", target)
        return self.get_status()

    def continue_monitor(self, platform: str = "all") -> dict[str, Any]:
        platforms = self._target_platforms(platform)
        sent: list[str] = []
        for target in platforms:
            proc = self._running_job(target)
            if not proc:
                if platform == "all":
                    self._append_log(f"[console] {target} 没有运行中的监控进程，已跳过", target)
                    continue
                raise RuntimeError(f"{target} 没有运行中的监控进程")
            if not proc.stdin:
                if platform == "all":
                    self._append_log(f"[console] {target} 监控进程 stdin 不可写，已跳过", target)
                    continue
                raise RuntimeError(f"{target} 监控进程 stdin 不可写")
            try:
                proc.stdin.write("\n")
                proc.stdin.flush()
            except OSError as exc:
                with self._lock:
                    self.jobs[target]["last_error"] = str(exc)
                    self.last_error = str(exc)
                if platform == "all":
                    self._append_log(f"[console] {target} 继续信号发送失败，已跳过：{exc}", target)
                    continue
                raise RuntimeError(f"{target} 继续信号发送失败：{exc}") from exc
            with self._lock:
                self.jobs[target]["waiting_for_human"] = False
                if self.jobs[target]["status"] == STATUS_NEEDS_HUMAN:
                    self.jobs[target]["status"] = STATUS_RUNNING
            sent.append(target)
            self._append_log(f"[console] 已向 {target} 监控进程发送继续信号", target)
        if not sent:
            raise RuntimeError("没有任何运行中的监控进程可继续")
        return {"sent": sent, **self.get_status()}

    def start_login_check(self, platform: str) -> dict[str, Any]:
        if platform not in PLATFORMS:
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
            self.login_platform = platform
            self._append_log(
                "[console] 登录检查浏览器已打开，请在浏览器中完成登录，完成后点击【完成登录检查】"
            )
            self._append_log(f"[console] started login check: {' '.join(args)}")
            threading.Thread(target=self._reader, args=(proc, "login", platform), daemon=True).start()
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

    def run_diagnose(self, platform: str) -> dict[str, Any]:
        if platform not in PLATFORMS:
            raise RuntimeError("platform 必须是 jd 或 dji")
        args = [sys.executable, "scripts/diagnose_page.py", "--platform", platform]
        self._append_log(f"[console] started diagnose: {' '.join(args)}", platform)
        try:
            result = subprocess.run(
                args,
                cwd=str(PROJECT_ROOT),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{platform} 诊断超时") from exc
        if result.stdout:
            for line in result.stdout.splitlines():
                self._append_log(line, platform)
        if result.stderr:
            for line in result.stderr.splitlines():
                self._append_log(line, platform)
        if result.returncode != 0:
            raise RuntimeError(f"{platform} 诊断失败，退出码 {result.returncode}")
        latest = self.get_latest_diagnosis(platform)
        return {"platform": platform, "diagnosis": latest}

    def get_latest_diagnosis(self, platform: str) -> dict[str, Any]:
        path = DIAGNOSTICS_DIR / f"{platform}_latest.json"
        if not path.exists():
            return {"exists": False}
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data["exists"] = True
        return data

    def _target_platforms(self, platform: str) -> list[str]:
        if platform == "all":
            return list(PLATFORMS)
        if platform not in PLATFORMS:
            raise RuntimeError("platform 必须是 jd、dji 或 all")
        return [platform]

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

    def _load_runtime_state(self) -> dict[str, Any]:
        if not RUNTIME_STATE_PATH.exists():
            return {"jobs": {}}
        try:
            with RUNTIME_STATE_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {"jobs": {}}
        except Exception:  # noqa: BLE001
            return {"jobs": {}}

    def _runtime_job_is_current(
        self,
        runtime_job: dict[str, Any],
        job: dict[str, Any],
        proc: subprocess.Popen[str] | None,
    ) -> bool:
        if not runtime_job:
            return False
        started_at = job.get("started_at")
        if not started_at:
            return False

        runtime_extra = runtime_job.get("extra") if isinstance(runtime_job.get("extra"), dict) else {}
        runtime_run_id = runtime_job.get("run_id") or runtime_extra.get("run_id")
        runtime_pid = runtime_job.get("pid") or runtime_extra.get("pid")
        if runtime_run_id and runtime_run_id == job.get("run_id"):
            return True
        if proc and runtime_pid:
            try:
                if int(runtime_pid) == proc.pid:
                    return True
            except (TypeError, ValueError):
                pass

        updated_at = str(runtime_job.get("updated_at") or "")
        if updated_at:
            try:
                updated_ts = datetime.fromisoformat(updated_at).timestamp()
                return updated_ts >= float(started_at)
            except ValueError:
                return False
        return False

    def _job_view(self, platform: str, runtime_state: dict[str, Any]) -> dict[str, Any]:
        job = self.jobs[platform]
        proc = self._running_job(platform)
        runtime_job = runtime_state.get("jobs", {}).get(platform, {})
        runtime_current = self._runtime_job_is_current(runtime_job, job, proc)
        status = job["status"]
        event_status = EVENT_STATE_MAP.get(str(runtime_job.get("state", "")))
        if runtime_current and event_status:
            status = event_status
        latest_screenshot = (
            runtime_job.get("latest_screenshot") if runtime_current else ""
        ) or job.get("latest_screenshot", "")
        waiting_for_human = status == STATUS_NEEDS_HUMAN or bool(job.get("waiting_for_human"))
        return {
            "platform": platform,
            "enabled": bool(job.get("enabled")),
            "run_id": job.get("run_id", ""),
            "pid": proc.pid if proc else None,
            "status": status,
            "dry_run": bool(job.get("dry_run", True)),
            "waiting_for_human": waiting_for_human,
            "started_at": job.get("started_at"),
            "last_error": job.get("last_error", ""),
            "latest_screenshot": latest_screenshot,
            "logs": list(job.get("logs", []))[-50:],
        }

    def _aggregate_status(self, jobs: dict[str, dict[str, Any]]) -> str:
        statuses = {job["status"] for job in jobs.values()}
        for status in AGGREGATE_PRIORITY:
            if status in statuses:
                return status
        return STATUS_WAITING

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            runtime_state = self._load_runtime_state()
            jobs = {platform: self._job_view(platform, runtime_state) for platform in PLATFORMS}
            login_proc = self.login_process
            aggregate = self._aggregate_status(jobs)
            active = [platform for platform, job in jobs.items() if job["pid"]]
            return {
                "aggregate_status": aggregate,
                "jobs": jobs,
                "status": aggregate,
                "platform": "both" if len(active) == 2 else (active[0] if active else ""),
                "dry_run": all(job["dry_run"] for job in jobs.values()),
                "pid": ",".join(str(jobs[p]["pid"]) for p in active) if active else None,
                "login_check_pid": login_proc.pid if login_proc and login_proc.poll() is None else None,
                "login_check_platform": self.login_platform,
                "started_at": min(
                    (job["started_at"] for job in jobs.values() if job["started_at"]),
                    default=None,
                ),
                "last_error": self.last_error
                or next((job["last_error"] for job in jobs.values() if job["last_error"]), ""),
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
