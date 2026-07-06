"""Parse coarse runtime status from existing CLI log lines."""

from __future__ import annotations

STATUS_WAITING = "等待"
STATUS_RUNNING = "监控中"
STATUS_STOCK_FOUND = "发现库存"
STATUS_CHECKOUT = "结算页"
STATUS_SUBMITTED = "已提交"
STATUS_NEEDS_HUMAN = "需要人工处理"
STATUS_STOPPED = "已停止"
STATUS_ERROR = "异常"


def parse_status_from_line(line: str, current_status: str = STATUS_WAITING) -> str:
    """Return the next status inferred from one log line."""
    text = line.lower()

    if any(
        keyword in line
        for keyword in (
            "验证码",
            "滑块",
            "登录失效",
            "人工接管",
            "校验失败",
            "未能识别订单金额",
        )
    ):
        return STATUS_NEEDS_HUMAN

    if any(keyword in line for keyword in ("已自动提交订单", "提交成功", "进入支付")):
        return STATUS_SUBMITTED

    if any(keyword in line for keyword in ("已进入结算页", "购物车页")):
        return STATUS_CHECKOUT
    if any(keyword in text for keyword in ("checkout_page", "cart_page")):
        return STATUS_CHECKOUT

    if "检测到可购买按钮" in line or "btn_available" in text:
        return STATUS_STOCK_FOUND

    if any(
        keyword in line
        for keyword in (
            "抢购助手已启动",
            "开始监控",
            "进入【预热模式】",
            "进入【高频轮询模式】",
            "高频轮询",
        )
    ):
        return STATUS_RUNNING

    if "dry-run" in text and "模式" in line:
        return STATUS_RUNNING

    return current_status
