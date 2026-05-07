"""
watch_jd.py — 京东抢购核心脚本（MVP）

流程：
  1. 加载配置，初始化日志
  2. 启动持久化 Profile 浏览器
  3. 打开商品页，检查登录状态
  4. 根据距开售时间进入不同轮询模式
  5. 检测到可购买按钮后进入结算流程
  6. 遇到验证码/登录失效时暂停并通知人工接管
  7. dry_run 模式下在提交订单前停止
  8. auto_submit_order=true 时才自动提交订单
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

# 将 scripts/ 目录加入 sys.path，方便相对导入
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
from utils import (
    ButtonState,
    SeckillState,
    check_captcha,
    check_login_valid,
    create_browser_context,
    format_countdown,
    load_config,
    seconds_until_sale,
    setup_logging,
    smart_sleep,
    take_screenshot,
    visible_unavailable_cta,
)

logger = logging.getLogger("seckill.jd")


# ---------------------------------------------------------------------------
# 按钮状态检测
# ---------------------------------------------------------------------------

def detect_button_state(page: Page, selectors: dict[str, str]) -> str:
    """
    检测商品页按钮状态。
    优先级：BUY_NOW > ADD_TO_CART > APPOINTMENT > PRESALE > OUT_OF_STOCK > COMING_SOON
    """
    def _visible(sel: str) -> bool:
        if not sel:
            return False
        try:
            # 多个选择器用逗号分隔，逐一检测
            for s in [x.strip() for x in sel.split(",")]:
                el = page.query_selector(s)
                if el and el.is_visible() and el.is_enabled():
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    # 立即购买（最高优先级）
    if _visible(selectors.get("btn_buy_now", "")):
        return ButtonState.BUY_NOW

    # 加入购物车
    if _visible(selectors.get("btn_add_to_cart", "")):
        return ButtonState.ADD_TO_CART

    # 预约
    if _visible(selectors.get("btn_appointment", "")):
        return ButtonState.APPOINTMENT

    # 预售
    if _visible(selectors.get("btn_presale", "")):
        return ButtonState.PRESALE

    # 无货 / 即将开售（主 CTA 不可点或非交互无货节点）
    if visible_unavailable_cta(page, selectors.get("btn_out_of_stock", "")):
        return ButtonState.OUT_OF_STOCK

    return ButtonState.UNKNOWN


# ---------------------------------------------------------------------------
# 购买流程
# ---------------------------------------------------------------------------

def click_buy_button(
    page: Page,
    selectors: dict[str, str],
    btn_state: str,
    dry_run: bool,
    screenshot_dir: str,
) -> bool:
    """
    点击购买按钮（立即购买 或 加入购物车）。
    返回 True 表示点击成功并跳转到结算页。
    """
    sel_map = {
        ButtonState.BUY_NOW: selectors.get("btn_buy_now", ""),
        ButtonState.ADD_TO_CART: selectors.get("btn_add_to_cart", ""),
    }
    sel = sel_map.get(btn_state, "")
    if not sel:
        logger.warning("无法找到对应按钮选择器，btn_state=%s", btn_state)
        return False

    if dry_run:
        logger.info("[DRY-RUN] 检测到按钮状态=%s，跳过实际点击", btn_state)
        take_screenshot(page, screenshot_dir, tag="dry_run_detected")
        return False

    # 逐一尝试选择器
    for s in [x.strip() for x in sel.split(",")]:
        try:
            el = page.query_selector(s)
            if el and el.is_visible() and el.is_enabled():
                logger.info("点击按钮：%s (selector: %s)", btn_state, s)
                take_screenshot(page, screenshot_dir, tag="before_click")
                el.click()
                # 等待页面跳转或弹窗
                page.wait_for_timeout(1500)
                take_screenshot(page, screenshot_dir, tag="after_click")
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("点击选择器 [%s] 失败：%s", s, exc)

    logger.error("所有选择器均点击失败")
    return False


def handle_checkout_page(
    page: Page,
    cfg: dict[str, Any],
    screenshot_dir: str,
) -> bool:
    """
    处理结算页面逻辑。
    - 截图并通知用户
    - 若 auto_submit_order=True 则自动提交订单
    - 否则停在结算页等待人工操作
    返回 True 表示已进入结算页。
    """
    purchase_cfg = cfg.get("purchase", {})
    selectors = cfg.get("selectors", {})
    notify_cfg = cfg.get("notify", {})
    product_name = cfg.get("product", {}).get("name", "未知商品")
    auto_submit = purchase_cfg.get("auto_submit_order", False)

    # 判断是否已进入结算页
    current_url = page.url
    is_checkout = any(kw in current_url for kw in [
        "buy.jd.com", "trade.jd.com", "checkout", "order/confirm",
    ])

    if not is_checkout:
        logger.info("当前 URL 不是结算页：%s", current_url)
        return False

    logger.info("已进入结算页：%s", current_url)
    scr_path = take_screenshot(page, screenshot_dir, tag="checkout_page")
    notify_purchase_success(product_name, scr_path, notify_cfg)

    if not auto_submit:
        logger.info("auto_submit_order=False，停在结算页等待人工支付。")
        notify_human_takeover(
            f"已进入结算页，请手动完成支付。商品：{product_name}",
            notify_cfg,
        )
        # 保持浏览器打开，脚本挂起等待用户操作
        logger.info("脚本已暂停，请在浏览器中手动完成支付。按 Ctrl+C 退出。")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            logger.info("用户手动退出")
        return True

    # auto_submit_order=True：自动提交订单
    logger.warning("auto_submit_order=True，将自动提交订单！")
    submit_sel = selectors.get("checkout_submit", "")
    if submit_sel:
        for s in [x.strip() for x in submit_sel.split(",")]:
            try:
                el = page.query_selector(s)
                if el and el.is_visible() and el.is_enabled():
                    logger.info("自动点击提交订单按钮：%s", s)
                    take_screenshot(page, screenshot_dir, tag="before_submit")
                    el.click()
                    page.wait_for_timeout(3000)
                    after_submit_path = take_screenshot(
                        page, screenshot_dir, tag="after_submit"
                    )
                    notify_purchase_success(
                        product_name + "（已自动提交订单）",
                        after_submit_path,
                        notify_cfg,
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                logger.error("自动提交订单失败：%s", exc)
    else:
        logger.error("auto_submit_order=True 但未配置 checkout_submit 选择器")

    return True


# ---------------------------------------------------------------------------
# 主监控循环
# ---------------------------------------------------------------------------

def run_watch_loop(cfg: dict[str, Any], dry_run_override: bool = False) -> None:
    """
    主监控循环。
    """
    # 提取配置
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
        logger.info("=" * 60)
        logger.info("【DRY-RUN 模式】所有购买操作均不会实际执行")
        logger.info("=" * 60)

    error_count = 0
    seckill_state = SeckillState.WAITING

    with sync_playwright() as pw:
        context = create_browser_context(pw, browser_cfg, str(_PROJECT_ROOT))
        page = context.new_page()

        # 设置超时
        timeout_ms = int(browser_cfg.get("timeout_ms", 15000))
        nav_timeout_ms = int(browser_cfg.get("navigation_timeout_ms", 30000))
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(nav_timeout_ms)

        try:
            # ----------------------------------------------------------------
            # 打开商品页
            # ----------------------------------------------------------------
            logger.info("正在打开商品页：%s", product_url)
            page.goto(product_url, wait_until="domcontentloaded")
            take_screenshot(page, screenshot_dir, tag="page_open")

            # ----------------------------------------------------------------
            # 检查登录状态
            # ----------------------------------------------------------------
            if not check_login_valid(page, selectors):
                logger.error("检测到未登录或登录失效！")
                take_screenshot(page, screenshot_dir, tag="login_invalid")
                notify_human_takeover(
                    "未登录或登录失效，请在浏览器中手动登录后按 Enter 继续",
                    notify_cfg,
                )
                input(">>> 请在浏览器中完成登录，然后按 Enter 继续...")
                page.reload(wait_until="domcontentloaded")
                take_screenshot(page, screenshot_dir, tag="after_login")

            logger.info("登录状态正常，开始监控商品：%s", product_name)
            send_notification(
                "抢购助手已启动",
                f"商品：{product_name}\n开售时间：{sale_time}\n模式：{'DRY-RUN' if dry_run else '实战'}",
                notify_cfg=notify_cfg,
            )

            # ----------------------------------------------------------------
            # 主循环
            # ----------------------------------------------------------------
            while seckill_state not in (SeckillState.DONE, SeckillState.SUCCESS):
                try:
                    # 计算距开售时间
                    secs = seconds_until_sale(sale_time, tz_name) if sale_time else -1

                    # 确定当前阶段和轮询间隔
                    if secs > warmup_before:
                        if seckill_state != SeckillState.WAITING:
                            seckill_state = SeckillState.WAITING
                        poll_interval = poll_normal
                        phase_name = "等待"
                    elif secs > high_freq_before:
                        if seckill_state != SeckillState.WARMUP:
                            seckill_state = SeckillState.WARMUP
                            logger.info("进入【预热模式】，距开售 %.0f 秒", secs)
                        poll_interval = poll_warmup
                        phase_name = "预热"
                    else:
                        if seckill_state not in (SeckillState.HIGH_FREQ, SeckillState.PURCHASING):
                            seckill_state = SeckillState.HIGH_FREQ
                            logger.info("进入【高频轮询模式】，距开售 %.1f 秒", secs)
                        poll_interval = poll_high
                        phase_name = "高频"

                    countdown_str = format_countdown(secs) if sale_time else "无限制"
                    logger.info(
                        "[%s] 距开售：%s | 轮询间隔：%.1fs",
                        phase_name, countdown_str, poll_interval,
                    )

                    # --------------------------------------------------------
                    # 刷新页面（高频模式下直接刷新，其他模式下先检测再刷新）
                    # --------------------------------------------------------
                    if seckill_state in (SeckillState.HIGH_FREQ, SeckillState.WARMUP):
                        try:
                            page.reload(wait_until="domcontentloaded")
                        except Exception as reload_exc:
                            logger.warning("页面刷新失败：%s，尝试重新导航", reload_exc)
                            page.goto(product_url, wait_until="domcontentloaded")

                    # --------------------------------------------------------
                    # 检测验证码
                    # --------------------------------------------------------
                    if check_captcha(page, selectors):
                        logger.warning("检测到验证码/滑块，暂停并通知人工接管")
                        take_screenshot(page, screenshot_dir, tag="captcha_detected")
                        notify_human_takeover(
                            "检测到验证码或滑块，请手动完成验证后按 Enter 继续",
                            notify_cfg,
                        )
                        seckill_state = SeckillState.PAUSED
                        input(">>> 请手动完成验证，然后按 Enter 继续...")
                        seckill_state = SeckillState.HIGH_FREQ
                        continue

                    # --------------------------------------------------------
                    # 检测登录状态
                    # --------------------------------------------------------
                    if not check_login_valid(page, selectors):
                        logger.warning("登录失效，暂停并通知人工接管")
                        take_screenshot(page, screenshot_dir, tag="login_expired")
                        notify_human_takeover(
                            "登录失效，请重新登录后按 Enter 继续",
                            notify_cfg,
                        )
                        seckill_state = SeckillState.PAUSED
                        input(">>> 请重新登录，然后按 Enter 继续...")
                        page.reload(wait_until="domcontentloaded")
                        seckill_state = SeckillState.HIGH_FREQ
                        continue

                    # --------------------------------------------------------
                    # 检测按钮状态
                    # --------------------------------------------------------
                    btn_state = detect_button_state(page, selectors)
                    logger.info("按钮状态：%s", btn_state)

                    if btn_state in (ButtonState.BUY_NOW, ButtonState.ADD_TO_CART):
                        logger.info("检测到可购买按钮！状态：%s", btn_state)
                        take_screenshot(page, screenshot_dir, tag="btn_available")
                        notify_purchase_attempt(product_name, notify_cfg)

                        seckill_state = SeckillState.PURCHASING

                        # 点击购买按钮
                        clicked = click_buy_button(
                            page, selectors, btn_state, dry_run, screenshot_dir
                        )

                        if dry_run:
                            logger.info("[DRY-RUN] 模拟购买流程结束，实际未操作")
                            seckill_state = SeckillState.DONE
                            break

                        if not clicked:
                            logger.warning("点击购买按钮失败，将在下次轮询重试")
                            seckill_state = SeckillState.HIGH_FREQ
                            smart_sleep(poll_interval, logger)
                            continue

                        # 处理结算页
                        if handle_checkout_page(page, cfg, screenshot_dir):
                            seckill_state = SeckillState.SUCCESS
                            break

                        # 未跳转到结算页，可能是加入购物车成功，需要手动去结算
                        logger.info("未直接跳转到结算页，当前 URL：%s", page.url)
                        take_screenshot(page, screenshot_dir, tag="after_buy_click")

                        # 尝试前往购物车结算
                        if btn_state == ButtonState.ADD_TO_CART and purchase_cfg.get("cart_flow_fallback", True):
                            logger.info("尝试前往购物车结算...")
                            _go_to_cart_checkout(page, cfg, screenshot_dir)
                            if handle_checkout_page(page, cfg, screenshot_dir):
                                seckill_state = SeckillState.SUCCESS
                                break

                        # 仍未成功，继续轮询
                        seckill_state = SeckillState.HIGH_FREQ

                    elif btn_state == ButtonState.APPOINTMENT:
                        logger.info("当前为预约状态，继续等待开售")
                    elif btn_state == ButtonState.PRESALE:
                        logger.info("当前为预售状态，继续等待")
                    elif btn_state == ButtonState.OUT_OF_STOCK:
                        logger.info("当前无货，继续轮询")
                    else:
                        logger.info("按钮状态未知，继续轮询（可能页面未加载完成）")

                    error_count = 0  # 重置错误计数
                    smart_sleep(poll_interval, logger)

                except KeyboardInterrupt:
                    logger.info("用户手动中断")
                    raise

                except Exception as loop_exc:  # noqa: BLE001
                    error_count += 1
                    logger.error(
                        "轮询异常（第 %d/%d 次）：%s",
                        error_count, max_retry, loop_exc,
                        exc_info=True,
                    )
                    take_screenshot(page, screenshot_dir, tag=f"error_{error_count}")

                    if error_count >= max_retry:
                        logger.critical("连续错误次数达到上限 %d，暂停并通知人工接管", max_retry)
                        notify_human_takeover(
                            f"连续错误 {max_retry} 次，最后错误：{loop_exc}",
                            notify_cfg,
                        )
                        notify_error(product_name, str(loop_exc), notify_cfg)
                        seckill_state = SeckillState.PAUSED
                        input(">>> 请检查浏览器状态，解决问题后按 Enter 继续，或 Ctrl+C 退出...")
                        error_count = 0
                        seckill_state = SeckillState.HIGH_FREQ
                    else:
                        smart_sleep(retry_wait, logger)

        except KeyboardInterrupt:
            logger.info("脚本已手动停止")
        except Exception as fatal_exc:  # noqa: BLE001
            logger.critical("致命错误：%s", fatal_exc, exc_info=True)
            try:
                take_screenshot(page, screenshot_dir, tag="fatal_error")
                notify_error(product_name, str(fatal_exc), notify_cfg)
            except Exception:  # noqa: BLE001
                pass
        finally:
            logger.info("关闭浏览器")
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass

    logger.info("watch_jd.py 执行结束，最终状态：%s", seckill_state)


def _go_to_cart_checkout(
    page: Page,
    cfg: dict[str, Any],
    screenshot_dir: str,
) -> None:
    """从购物车进入结算页"""
    try:
        logger.info("前往购物车：https://cart.jd.com/cart.action")
        page.goto("https://cart.jd.com/cart.action", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        take_screenshot(page, screenshot_dir, tag="cart_page")

        # 全选商品
        select_all = page.query_selector("#select-all, .cart-checkbox-all")
        if select_all and not select_all.is_checked():
            select_all.click()
            page.wait_for_timeout(500)

        # 点击去结算
        checkout_btn = page.query_selector(
            ".btn-checkout, #cart-floatbar .btn-checkout, [class*='checkout']"
        )
        if checkout_btn and checkout_btn.is_visible():
            logger.info("点击购物车去结算按钮")
            checkout_btn.click()
            page.wait_for_timeout(2000)
            take_screenshot(page, screenshot_dir, tag="cart_checkout_clicked")
        else:
            logger.warning("未找到购物车结算按钮")
    except Exception as exc:  # noqa: BLE001
        logger.error("购物车结算流程异常：%s", exc)


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="京东抢购脚本 — DJI Pocket 4",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 正常运行（读取 config/jd.json 中的 dry_run 设置）
  python scripts/watch_jd.py

  # 强制 dry-run 模式（不论配置文件如何）
  python scripts/watch_jd.py --dry-run

  # 指定配置文件
  python scripts/watch_jd.py --config config/jd.json

  # 实战模式（覆盖配置文件中的 dry_run=true）
  python scripts/watch_jd.py --no-dry-run
        """,
    )
    parser.add_argument(
        "--config",
        default=str(_PROJECT_ROOT / "config" / "jd.json"),
        help="配置文件路径（默认：config/jd.json）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="强制启用 dry-run 模式（不实际购买）",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        default=False,
        help="强制关闭 dry-run 模式（覆盖配置文件）",
    )
    args = parser.parse_args()

    # 加载配置
    cfg = load_config(args.config)

    # 初始化日志
    log_cfg = cfg.get("logging", {})
    log_dir = str(_PROJECT_ROOT / log_cfg.get("log_dir", "logs"))
    setup_logging(
        log_dir=log_dir,
        log_level=log_cfg.get("level", "INFO"),
        platform="jd",
    )

    # 处理 dry-run 参数
    dry_run_override = False
    if args.dry_run:
        dry_run_override = True
        logger.info("命令行参数：强制 dry-run 模式")
    elif args.no_dry_run:
        # 覆盖配置文件中的 dry_run
        cfg.setdefault("purchase", {})["dry_run"] = False
        logger.warning("命令行参数：关闭 dry-run 模式，将执行实际购买操作！")

    logger.info("配置文件：%s", args.config)
    logger.info("商品：%s", cfg.get("product", {}).get("name"))
    logger.info("商品 URL：%s", cfg.get("product", {}).get("url"))
    logger.info("开售时间：%s", cfg.get("schedule", {}).get("sale_time"))

    run_watch_loop(cfg, dry_run_override=dry_run_override)


if __name__ == "__main__":
    main()
