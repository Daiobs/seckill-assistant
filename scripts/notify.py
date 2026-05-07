"""
notify.py — 通知模块
支持：桌面通知、日志通知、Bark（iOS）、PushPlus（微信）、SMTP 邮件、Webhook
"""

from __future__ import annotations

import json
import logging
import smtplib
import subprocess
import sys
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from typing import Any

logger = logging.getLogger("seckill.notify")


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

def send_notification(
    title: str,
    body: str,
    level: str = "info",
    notify_cfg: dict[str, Any] | None = None,
) -> None:
    """
    统一通知入口。

    :param title:      通知标题
    :param body:       通知正文
    :param level:      info | warning | error | success
    :param notify_cfg: 配置文件中 notify 节点的字典；为 None 时仅打印日志
    """
    if notify_cfg is None:
        notify_cfg = {"methods": ["log"]}

    methods: list[str] = notify_cfg.get("methods", ["log"])

    for method in methods:
        try:
            if method == "log":
                _notify_log(title, body, level)
            elif method == "desktop":
                _notify_desktop(title, body, level)
            elif method == "bark":
                _notify_bark(title, body, notify_cfg.get("bark_url", ""))
            elif method == "pushplus":
                _notify_pushplus(title, body, notify_cfg.get("pushplus_token", ""))
            elif method == "smtp":
                _notify_smtp(title, body, notify_cfg.get("smtp", {}))
            elif method == "webhook":
                _notify_webhook(title, body, notify_cfg.get("webhook_url", ""))
            else:
                logger.warning("未知通知方式: %s", method)
        except Exception as exc:  # noqa: BLE001
            logger.error("通知方式 [%s] 发送失败: %s", method, exc)


# ---------------------------------------------------------------------------
# 各通知实现
# ---------------------------------------------------------------------------

def _notify_log(title: str, body: str, level: str) -> None:
    """写入日志（始终可用）"""
    msg = f"[通知] {title} | {body}"
    if level in ("error",):
        logger.error(msg)
    elif level in ("warning",):
        logger.warning(msg)
    else:
        logger.info(msg)


def _escape_applescript_string(s: str) -> str:
    """AppleScript 双引号字符串字面量转义（反斜杠优先）。"""
    t = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    return t.replace("\\", "\\\\").replace('"', '\\"')


def _notify_desktop(title: str, body: str, level: str) -> None:
    """
    桌面系统通知。
    - macOS: osascript
    - Linux: notify-send（需 libnotify-bin）
    - Windows: win10toast / plyer（可选依赖）
    """
    platform = sys.platform
    if platform == "darwin":
        t_esc = _escape_applescript_string(title)
        b_esc = _escape_applescript_string(body)
        script = f'display notification "{b_esc}" with title "{t_esc}"'
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    elif platform.startswith("linux"):
        icon = {
            "success": "dialog-information",
            "warning": "dialog-warning",
            "error": "dialog-error",
        }.get(level, "dialog-information")
        subprocess.run(
            ["notify-send", "-i", icon, title, body],
            check=False,
            timeout=5,
        )
    elif platform == "win32":
        try:
            from plyer import notification  # type: ignore[import]
            notification.notify(title=title, message=body, timeout=10)
        except ImportError:
            logger.warning("Windows 桌面通知需要安装 plyer: pip install plyer")


def _notify_bark(title: str, body: str, bark_url: str) -> None:
    """
    Bark iOS 推送。
    bark_url 格式: https://api.day.app/{your_key}
    """
    if not bark_url:
        logger.warning("Bark 通知未配置 bark_url，跳过")
        return
    bark_url = bark_url.rstrip("/")
    encoded_title = urllib.parse.quote(title)
    encoded_body = urllib.parse.quote(body)
    url = f"{bark_url}/{encoded_title}/{encoded_body}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode())
        if result.get("code") != 200:
            raise RuntimeError(f"Bark 返回异常: {result}")
    logger.info("Bark 通知发送成功")


def _notify_pushplus(title: str, body: str, token: str) -> None:
    """
    PushPlus 微信推送。
    token: PushPlus 用户 token
    """
    if not token:
        logger.warning("PushPlus 通知未配置 token，跳过")
        return
    payload = json.dumps({
        "token": token,
        "title": title,
        "content": body,
        "template": "txt",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://www.pushplus.plus/send",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode())
        if result.get("code") != 200:
            raise RuntimeError(f"PushPlus 返回异常: {result}")
    logger.info("PushPlus 通知发送成功")


def _notify_smtp(title: str, body: str, smtp_cfg: dict[str, Any]) -> None:
    """SMTP 邮件通知"""
    if not smtp_cfg.get("enabled", False):
        return
    host = smtp_cfg["host"]
    port = int(smtp_cfg["port"])
    user = smtp_cfg["user"]
    password = smtp_cfg["password"]
    to_addr = smtp_cfg["to"]

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = title
    msg["From"] = user
    msg["To"] = to_addr

    with smtplib.SMTP_SSL(host, port) as server:
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())
    logger.info("SMTP 邮件通知发送成功 -> %s", to_addr)


def _notify_webhook(title: str, body: str, webhook_url: str) -> None:
    """通用 Webhook（POST JSON）"""
    if not webhook_url:
        logger.warning("Webhook 通知未配置 webhook_url，跳过")
        return
    payload = json.dumps({"title": title, "body": body}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        status = resp.status
        if status not in (200, 201, 204):
            raise RuntimeError(f"Webhook 返回 HTTP {status}")
    logger.info("Webhook 通知发送成功")


# ---------------------------------------------------------------------------
# 快捷函数
# ---------------------------------------------------------------------------

def notify_human_takeover(
    reason: str,
    notify_cfg: dict[str, Any] | None = None,
) -> None:
    """需要人工接管时调用"""
    title = "⚠️ 抢购助手需要人工接管"
    body = f"原因：{reason}\n请立即查看浏览器窗口并手动操作。"
    send_notification(title, body, level="warning", notify_cfg=notify_cfg)


def notify_purchase_success(
    product_name: str,
    screenshot_path: str = "",
    notify_cfg: dict[str, Any] | None = None,
) -> None:
    """进入结算页/下单成功时调用"""
    title = "✅ 抢购成功！请尽快完成支付"
    body = f"商品：{product_name}\n已进入结算页面，请立即打开浏览器完成支付！"
    if screenshot_path:
        body += f"\n截图：{screenshot_path}"
    send_notification(title, body, level="success", notify_cfg=notify_cfg)


def notify_purchase_attempt(
    product_name: str,
    notify_cfg: dict[str, Any] | None = None,
) -> None:
    """检测到可购买按钮、准备点击时调用"""
    title = "🛒 检测到可购买按钮，正在尝试下单"
    body = f"商品：{product_name}，正在自动点击购买按钮..."
    send_notification(title, body, level="info", notify_cfg=notify_cfg)


def notify_error(
    product_name: str,
    error: str,
    notify_cfg: dict[str, Any] | None = None,
) -> None:
    """发生异常时调用"""
    title = "❌ 抢购助手发生错误"
    body = f"商品：{product_name}\n错误：{error}"
    send_notification(title, body, level="error", notify_cfg=notify_cfg)
