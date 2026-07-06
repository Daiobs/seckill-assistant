"""
watch_dji.py — 大疆官网抢购脚本

大疆官网（store.dji.com/cn）流程：
  1. 打开商品页，检查登录状态
  2. 轮询按钮状态（售罄/预约/立即购买）
  3. 检测到可购买按钮后点击，进入购物车或结算页
  4. 截图通知，等待人工支付（或按配置自动提交）

注意：大疆官网反爬较强，slow_mo 建议适当调高。
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).parent.resolve()
_PROJECT_ROOT = _SCRIPTS_DIR.parent.resolve()
sys.path.insert(0, str(_SCRIPTS_DIR))

from playwright.sync_api import Page, sync_playwright

from notify import (
    notify_error,
    notify_human_takeover,
    notify_purchase_attempt,
    notify_purchase_success,
    send_notification,
)
from runtime_events import emit_event
from utils import (
    ButtonState,
    LoginState,
    SeckillState,
    check_captcha,
    create_browser_context,
    dismiss_cookie_banner,
    find_action_element,
    format_countdown,
    load_config,
    seconds_until_sale,
    setup_logging,
    smart_sleep,
    take_screenshot,
    validate_order_before_submit,
    verify_after_submit,
    visible_unavailable_cta,
    wait_for_login_state,
)

logger = logging.getLogger("seckill.dji")


# ---------------------------------------------------------------------------
# 按钮状态检测（大疆官网专用）
# ---------------------------------------------------------------------------

def detect_button_state_dji(page: Page, selectors: dict[str, str]) -> str:
    """
    检测大疆官网商品页按钮状态。
    大疆官网按钮文本通常为中文，辅以文本匹配。
    """
    def _find_sold_out_by_text(texts: list[str]) -> bool:
        """售罄按钮常为 disabled，不要求 is_enabled。"""
        for text in texts:
            try:
                el = page.get_by_role("button", name=text, exact=False).first
                if el and el.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    # 立即购买
    if find_action_element(
        page,
        selectors,
        "btn_buy_now",
        ["立即购买", "立即抢购", "Buy Now"],
        blocked_texts=["加入购物车", "Add to Cart", "售罄", "Sold Out", "到货通知"],
    ):
        return ButtonState.BUY_NOW

    # 加入购物车
    if find_action_element(
        page,
        selectors,
        "btn_add_to_cart",
        ["加入购物车", "Add to Cart"],
        blocked_texts=["立即购买", "Buy Now", "售罄", "Sold Out", "到货通知"],
    ):
        return ButtonState.ADD_TO_CART

    # 预约/到货通知
    if find_action_element(page, selectors, "btn_appointment", ["到货通知", "预约", "Notify Me"]):
        return ButtonState.APPOINTMENT

    # 售罄/无货
    if visible_unavailable_cta(page, selectors.get("btn_out_of_stock", "")) or \
       _find_sold_out_by_text(["售罄", "暂时缺货", "Sold Out"]):
        return ButtonState.OUT_OF_STOCK

    return ButtonState.UNKNOWN


# ---------------------------------------------------------------------------
# 购买流程（大疆官网）
# ---------------------------------------------------------------------------

def click_buy_button_dji(
    page: Page,
    selectors: dict[str, str],
    btn_state: str,
    dry_run: bool,
    screenshot_dir: str,
) -> bool:
    """点击大疆官网购买按钮"""
    if dry_run:
        logger.info("[DRY-RUN] 检测到按钮状态=%s，跳过实际点击", btn_state)
        take_screenshot(page, screenshot_dir, tag="dji_dry_run_detected")
        return False

    text_map = {
        ButtonState.BUY_NOW: ["立即购买", "Buy Now", "立即抢购"],
        ButtonState.ADD_TO_CART: ["加入购物车", "Add to Cart"],
    }

    texts = text_map.get(btn_state, [])

    take_screenshot(page, screenshot_dir, tag="dji_before_click")

    match = find_action_element(
        page,
        selectors,
        "btn_buy_now" if btn_state == ButtonState.BUY_NOW else "btn_add_to_cart",
        texts,
        blocked_texts=["售罄", "Sold Out", "到货通知", "Notify Me"],
    )
    if match:
        try:
            logger.info(
                '命中购买按钮：platform=dji state=%s selector="%s" text="%s" tag=%s class="%s" id="%s"',
                btn_state,
                match["selector"],
                match["text"],
                match["tag"],
                match["class"],
                match["id"],
            )
            match["element"].click()
            page.wait_for_timeout(2000)
            take_screenshot(page, screenshot_dir, tag="dji_after_click")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("大疆按钮点击失败：%s", exc)

    logger.error("大疆官网：所有按钮点击方式均失败")
    return False


def handle_dji_checkout(
    page: Page,
    cfg: dict[str, Any],
    screenshot_dir: str,
) -> bool:
    """处理大疆官网结算页"""
    purchase_cfg = cfg.get("purchase", {})
    selectors = cfg.get("selectors", {})
    notify_cfg = cfg.get("notify", {})
    product_name = cfg.get("product", {}).get("name", "未知商品")
    auto_submit = purchase_cfg.get("auto_submit_order", False)

    current_url = page.url
    is_cart = "cart" in current_url
    is_checkout = any(kw in current_url for kw in [
        "checkout", "order", "payment",
    ])

    if is_cart:
        logger.info("大疆：已进入购物车页，尝试进入结算：%s", current_url)
        scr_path = take_screenshot(page, screenshot_dir, tag="dji_cart_page")
        emit_event("dji", "checkout", "大疆已进入购物车页", scr_path, {"url": current_url})
        cart_checkout_sel = selectors.get(
            "cart_checkout",
            ".checkout-btn, [data-action='checkout'], button[class*='checkout']",
        )
        for s in [x.strip() for x in cart_checkout_sel.split(",")]:
            try:
                el = page.query_selector(s)
                if el and el.is_visible() and el.is_enabled():
                    logger.info("大疆：点击购物车去结算：%s", s)
                    el.click()
                    page.wait_for_timeout(3000)
                    break
            except Exception as exc:  # noqa: BLE001
                logger.warning("大疆：购物车结算按钮 [%s] 点击失败：%s", s, exc)
        current_url = page.url
        is_checkout = any(kw in current_url for kw in ["checkout", "order", "payment"])

    if not is_checkout:
        logger.info("大疆：当前 URL 不是最终结算页：%s", current_url)
        return False

    logger.info("大疆：已进入结算/购物车页：%s", current_url)
    scr_path = take_screenshot(page, screenshot_dir, tag="dji_checkout_page")
    emit_event("dji", "checkout", "大疆已进入结算页", scr_path, {"url": current_url})
    notify_purchase_success(product_name, scr_path, notify_cfg)

    if not auto_submit:
        logger.info("auto_submit_order=False，停在结算页等待人工支付")
        notify_human_takeover(
            f"大疆官网已进入结算页，请手动完成支付。商品：{product_name}",
            notify_cfg,
        )
        logger.info("脚本已暂停，请在浏览器中手动完成支付。按 Ctrl+C 退出。")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            logger.info("用户手动退出")
        return True

    # 自动提交
    ok, validation_msg = validate_order_before_submit(page, cfg, platform="大疆官网")
    if not ok:
        logger.error("大疆：自动提交前校验失败：%s", validation_msg)
        scr_path = take_screenshot(page, screenshot_dir, tag="dji_submit_blocked")
        emit_event("dji", "need_human", validation_msg, scr_path, {"url": page.url})
        notify_human_takeover(
            f"大疆官网自动提交前校验失败，已暂停：{validation_msg}",
            notify_cfg,
        )
        input(">>> 请人工核对大疆订单页。处理完成后按 Enter 退出自动提交流程...")
        return True

    logger.info("大疆：自动提交前校验通过：%s", validation_msg)
    submit_sel = selectors.get("checkout_submit", "")
    if submit_sel:
        for s in [x.strip() for x in submit_sel.split(",")]:
            try:
                el = page.query_selector(s)
                if el and el.is_visible() and el.is_enabled():
                    logger.info("大疆：自动点击提交订单：%s", s)
                    take_screenshot(page, screenshot_dir, tag="dji_before_submit")
                    emit_event("dji", "checkout", "大疆准备自动提交订单", extra={"url": page.url})
                    el.click()
                    submit_ok, submit_msg = verify_after_submit(page, cfg, platform="大疆官网")
                    after_submit_path = take_screenshot(page, screenshot_dir, tag="dji_after_submit")
                    logger.info("大疆提交后校验结果：%s", submit_msg)
                    if submit_ok:
                        emit_event(
                            "dji",
                            "submitted",
                            submit_msg,
                            after_submit_path,
                            {"url": page.url},
                        )
                        notify_purchase_success(
                            product_name + "（大疆官网已自动提交订单）",
                            after_submit_path,
                            notify_cfg,
                        )
                    else:
                        emit_event(
                            "dji",
                            "need_human",
                            submit_msg,
                            after_submit_path,
                            {"url": page.url},
                        )
                        notify_human_takeover(
                            f"大疆官网自动提交后结果未确认，已暂停：{submit_msg}",
                            notify_cfg,
                        )
                        input(">>> 请人工确认大疆订单状态。处理完成后按 Enter 继续...")
                    return True
            except Exception as exc:  # noqa: BLE001
                logger.error("大疆：自动提交失败：%s", exc)

    scr_path = take_screenshot(page, screenshot_dir, tag="dji_submit_button_not_found")
    emit_event("dji", "need_human", "大疆未找到可点击的提交订单按钮", scr_path, {"url": page.url})
    return True


# ---------------------------------------------------------------------------
# 主监控循环
# ---------------------------------------------------------------------------

def run_watch_loop_dji(cfg: dict[str, Any], dry_run_override: bool = False) -> None:
    product_cfg = cfg.get("product", {})
    schedule_cfg = cfg.get("schedule", {})
    purchase_cfg = cfg.get("purchase", {})
    browser_cfg = cfg.get("browser", {})
    selectors = cfg.get("selectors", {})
    notify_cfg = cfg.get("notify", {})
    log_cfg = cfg.get("logging", {})

    product_name = product_cfg.get("name", "未知商品")
    product_url = product_cfg.get("url", "")
    sale_time = schedule_cfg.get("sale_time", "")
    tz_name = schedule_cfg.get("timezone", "Asia/Shanghai")

    poll_normal = float(schedule_cfg.get("poll_interval_normal", 5.0))
    poll_warmup = float(schedule_cfg.get("poll_interval_warmup", 2.0))
    poll_high = float(schedule_cfg.get("poll_interval_high_freq", 0.3))
    warmup_before = float(schedule_cfg.get("warmup_seconds_before", 300))
    high_freq_before = float(schedule_cfg.get("high_freq_seconds_before", 60))
    max_retry = int(schedule_cfg.get("max_retry_on_error", 5))
    retry_wait = float(schedule_cfg.get("retry_wait_seconds", 3.0))

    dry_run = dry_run_override or purchase_cfg.get("dry_run", True)
    screenshot_dir = str(_PROJECT_ROOT / log_cfg.get("screenshot_dir", "screenshots"))

    if dry_run:
        logger.info("【DRY-RUN 模式】大疆官网，所有购买操作均不会实际执行")

    error_count = 0
    seckill_state = SeckillState.WAITING

    with sync_playwright() as pw:
        context = create_browser_context(pw, browser_cfg, str(_PROJECT_ROOT))
        page = context.new_page()

        timeout_ms = int(browser_cfg.get("timeout_ms", 15000))
        nav_timeout_ms = int(browser_cfg.get("navigation_timeout_ms", 30000))
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(nav_timeout_ms)

        try:
            logger.info("大疆官网：打开商品页：%s", product_url)
            page.goto(product_url, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)  # 大疆官网 JS 渲染较慢
            dismiss_cookie_banner(page, "dji")
            scr_path = take_screenshot(page, screenshot_dir, tag="dji_page_open")
            emit_event("dji", "monitoring", "大疆商品页已打开", scr_path, {"url": page.url})

            # 检查登录
            login_state = wait_for_login_state(page, selectors, "dji", dry_run)
            if login_state != LoginState.LOGGED_IN:
                logger.warning("大疆官网：未检测到登录状态")
                scr_path = take_screenshot(page, screenshot_dir, tag="dji_login_check")
                emit_event("dji", "need_human", "大疆登录态未知或失效", scr_path, {"url": page.url})
                notify_human_takeover(
                    "大疆官网未登录，请在浏览器中手动登录后按 Enter 继续",
                    notify_cfg,
                )
                input(">>> 请在浏览器中完成大疆账号登录，然后按 Enter 继续...")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                scr_path = take_screenshot(page, screenshot_dir, tag="dji_after_login")
                emit_event("dji", "monitoring", "大疆登录处理后继续监控", scr_path, {"url": page.url})

            logger.info("大疆官网：开始监控商品：%s", product_name)
            send_notification(
                "大疆抢购助手已启动",
                f"商品：{product_name}\n开售时间：{sale_time}",
                notify_cfg=notify_cfg,
            )

            while seckill_state not in (SeckillState.DONE, SeckillState.SUCCESS):
                try:
                    secs = seconds_until_sale(sale_time, tz_name) if sale_time else -1

                    if secs > warmup_before:
                        seckill_state = SeckillState.WAITING
                        poll_interval = poll_normal
                        phase_name = "等待"
                    elif secs > high_freq_before:
                        if seckill_state != SeckillState.WARMUP:
                            seckill_state = SeckillState.WARMUP
                            logger.info("大疆：进入预热模式，距开售 %.0f 秒", secs)
                            emit_event("dji", "monitoring", "大疆进入预热模式", extra={"url": page.url})
                        poll_interval = poll_warmup
                        phase_name = "预热"
                    else:
                        if seckill_state not in (SeckillState.HIGH_FREQ, SeckillState.PURCHASING):
                            seckill_state = SeckillState.HIGH_FREQ
                            logger.info("大疆：进入高频轮询，距开售 %.1f 秒", secs)
                            emit_event("dji", "monitoring", "大疆进入高频轮询", extra={"url": page.url})
                        poll_interval = poll_high
                        phase_name = "高频"

                    countdown_str = format_countdown(secs) if sale_time else "无限制"
                    logger.info("[大疆-%s] 距开售：%s | 间隔：%.1fs", phase_name, countdown_str, poll_interval)

                    if seckill_state in (SeckillState.HIGH_FREQ, SeckillState.WARMUP):
                        try:
                            page.reload(wait_until="domcontentloaded")
                            page.wait_for_timeout(1000)
                        except Exception as re:
                            logger.warning("大疆：刷新失败：%s", re)
                            page.goto(product_url, wait_until="domcontentloaded")

                    # 验证码检测
                    if check_captcha(page, selectors, platform="dji"):
                        logger.warning("大疆：检测到验证码")
                        scr_path = take_screenshot(page, screenshot_dir, tag="dji_captcha")
                        emit_event("dji", "need_human", "大疆检测到验证码", scr_path, {"url": page.url})
                        notify_human_takeover("大疆官网检测到验证码，请手动完成后按 Enter 继续", notify_cfg)
                        seckill_state = SeckillState.PAUSED
                        input(">>> 请手动完成验证，然后按 Enter 继续...")
                        seckill_state = SeckillState.HIGH_FREQ
                        continue

                    login_state = wait_for_login_state(page, selectors, "dji", dry_run)
                    if login_state != LoginState.LOGGED_IN:
                        logger.warning("大疆：登录失效，暂停并通知人工接管")
                        scr_path = take_screenshot(page, screenshot_dir, tag="dji_login_expired")
                        emit_event("dji", "need_human", "大疆登录失效或状态未知", scr_path, {"url": page.url})
                        notify_human_takeover(
                            "大疆官网登录失效，请重新登录后按 Enter 继续",
                            notify_cfg,
                        )
                        seckill_state = SeckillState.PAUSED
                        input(">>> 请重新登录大疆账号，然后按 Enter 继续...")
                        page.reload(wait_until="domcontentloaded")
                        page.wait_for_timeout(2000)
                        seckill_state = SeckillState.HIGH_FREQ
                        continue

                    # 按钮状态检测
                    btn_state = detect_button_state_dji(page, selectors)
                    logger.info("大疆按钮状态：%s", btn_state)

                    if btn_state in (ButtonState.BUY_NOW, ButtonState.ADD_TO_CART):
                        logger.info("大疆：检测到可购买按钮！")
                        scr_path = take_screenshot(page, screenshot_dir, tag="dji_btn_available")
                        emit_event(
                            "dji",
                            "stock_found",
                            f"大疆检测到可购买按钮：{btn_state}",
                            scr_path,
                            {"url": page.url},
                        )
                        notify_purchase_attempt(product_name, notify_cfg)

                        seckill_state = SeckillState.PURCHASING
                        clicked = click_buy_button_dji(
                            page, selectors, btn_state, dry_run, screenshot_dir
                        )

                        if dry_run:
                            logger.info("[DRY-RUN] 大疆模拟购买结束")
                            seckill_state = SeckillState.DONE
                            break

                        if not clicked:
                            logger.warning("大疆：点击失败，继续轮询")
                            seckill_state = SeckillState.HIGH_FREQ
                            smart_sleep(poll_interval, logger)
                            continue

                        if handle_dji_checkout(page, cfg, screenshot_dir):
                            seckill_state = SeckillState.SUCCESS
                            break

                        seckill_state = SeckillState.HIGH_FREQ

                    error_count = 0
                    smart_sleep(poll_interval, logger)

                except KeyboardInterrupt:
                    raise
                except Exception as loop_exc:  # noqa: BLE001
                    error_count += 1
                    logger.error("大疆轮询异常（%d/%d）：%s", error_count, max_retry, loop_exc, exc_info=True)
                    scr_path = take_screenshot(page, screenshot_dir, tag=f"dji_error_{error_count}")
                    emit_event(
                        "dji",
                        "error",
                        f"大疆轮询异常：{loop_exc}",
                        scr_path,
                        {"url": page.url, "error_count": error_count},
                    )

                    if error_count >= max_retry:
                        notify_human_takeover(f"大疆连续错误 {max_retry} 次：{loop_exc}", notify_cfg)
                        emit_event(
                            "dji",
                            "need_human",
                            f"大疆连续错误 {max_retry} 次",
                            scr_path,
                            {"url": page.url},
                        )
                        seckill_state = SeckillState.PAUSED
                        input(">>> 请检查后按 Enter 继续，或 Ctrl+C 退出...")
                        error_count = 0
                        seckill_state = SeckillState.HIGH_FREQ
                    else:
                        smart_sleep(retry_wait, logger)

        except KeyboardInterrupt:
            logger.info("大疆脚本手动停止")
            emit_event("dji", "stopped", "大疆脚本已手动停止")
        except Exception as fatal:  # noqa: BLE001
            logger.critical("大疆致命错误：%s", fatal, exc_info=True)
            try:
                scr_path = take_screenshot(page, screenshot_dir, tag="dji_fatal_error")
                emit_event("dji", "error", f"大疆致命错误：{fatal}", scr_path, {"url": page.url})
                notify_error(product_name, str(fatal), notify_cfg)
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass

    logger.info("watch_dji.py 执行结束，最终状态：%s", seckill_state)


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="大疆官网抢购脚本 — DJI Pocket 4")
    parser.add_argument(
        "--config",
        default=str(_PROJECT_ROOT / "config" / "dji.json"),
        help="配置文件路径（默认：config/dji.json）",
    )
    parser.add_argument("--dry-run", action="store_true", default=None)
    parser.add_argument("--no-dry-run", action="store_true", default=False)
    args = parser.parse_args()

    cfg = load_config(args.config)
    log_cfg = cfg.get("logging", {})
    log_dir = str(_PROJECT_ROOT / log_cfg.get("log_dir", "logs"))
    setup_logging(log_dir=log_dir, log_level=log_cfg.get("level", "INFO"), platform="dji")

    dry_run_override = False
    if args.dry_run:
        dry_run_override = True
    elif args.no_dry_run:
        cfg.setdefault("purchase", {})["dry_run"] = False
        logger.warning("关闭 dry-run 模式，将执行实际购买操作！")

    run_watch_loop_dji(cfg, dry_run_override=dry_run_override)


if __name__ == "__main__":
    main()
