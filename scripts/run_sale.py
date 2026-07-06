"""
run_sale.py — 抢购助手统一入口

用法：
  python scripts/run_sale.py --platform jd
  python scripts/run_sale.py --platform dji
  python scripts/run_sale.py --platform jd --dry-run
  python scripts/run_sale.py --platform jd --no-dry-run
  python scripts/run_sale.py --platform jd --config config/jd_custom.json
  python scripts/run_sale.py --platform jd --check-login   # 仅检查登录状态
  python scripts/run_sale.py --platform jd --test-notify   # 测试通知功能
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import importlib
import logging
import sys
import threading
import traceback
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent.resolve()
_PROJECT_ROOT = _SCRIPTS_DIR.parent.resolve()
sys.path.insert(0, str(_SCRIPTS_DIR))

from utils import format_countdown, load_config, seconds_until_sale, setup_logging

logger = logging.getLogger("seckill.main")

PLATFORM_MAP = {
    "jd": {
        "default_config": "config/jd.json",
        "module": "watch_jd",
        "runner": "run_watch_loop",
        "description": "京东（MVP 核心）",
    },
    "dji": {
        "default_config": "config/dji.json",
        "module": "watch_dji",
        "runner": "run_watch_loop_dji",
        "description": "大疆官网",
    },
    "pdd": {
        "default_config": "config/pdd.json",
        "module": None,
        "runner": None,
        "description": "拼多多（配置框架预留，暂未实现）",
    },
    "both": {
        "default_config": None,
        "module": None,
        "runner": None,
        "description": "京东 + 大疆官网并行监控",
    },
}


def _print_banner(platform: str, cfg: dict, dry_run: bool) -> None:
    """打印启动横幅"""
    product = cfg.get("product", {})
    schedule = cfg.get("schedule", {})
    sale_time = schedule.get("sale_time", "未设置")
    tz = schedule.get("timezone", "Asia/Shanghai")

    secs = seconds_until_sale(sale_time, tz) if sale_time and sale_time != "未设置" else None
    countdown = format_countdown(secs) if secs is not None else "N/A"

    banner = f"""
╔══════════════════════════════════════════════════════════╗
║           个人新品抢购助手 — DJI Pocket 4                ║
╠══════════════════════════════════════════════════════════╣
║  平台：{PLATFORM_MAP[platform]['description']:<48}║
║  商品：{product.get('name', 'N/A'):<48}║
║  URL ：{product.get('url', 'N/A')[:48]:<48}║
║  开售：{sale_time:<48}║
║  倒计时：{countdown:<46}║
║  模式：{'【DRY-RUN 模拟模式，不会实际购买】' if dry_run else '【实战模式，将执行真实购买操作！】':<44}║
╚══════════════════════════════════════════════════════════╝
"""
    print(banner)
    logger.info("启动平台：%s，商品：%s，开售：%s，dry_run=%s",
                platform, product.get("name"), sale_time, dry_run)


def cmd_check_login(cfg: dict, platform: str) -> None:
    """检查登录状态（不进入监控循环）"""
    from playwright.sync_api import sync_playwright

    from utils import check_login_valid, create_browser_context, take_screenshot

    browser_cfg = cfg.get("browser", {})
    selectors = cfg.get("selectors", {})
    product_url = cfg.get("product", {}).get("url", "")
    screenshot_dir = str(_PROJECT_ROOT / cfg.get("logging", {}).get("screenshot_dir", "screenshots"))

    logger.info("检查登录状态，平台：%s", platform)
    with sync_playwright() as pw:
        context = create_browser_context(pw, browser_cfg, str(_PROJECT_ROOT))
        page = context.new_page()
        try:
            page.goto(product_url, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            take_screenshot(page, screenshot_dir, tag=f"{platform}_login_check")
            is_logged = check_login_valid(page, selectors)
            if is_logged:
                logger.info("✅ 登录状态正常")
                print("✅ 登录状态正常")
            else:
                logger.warning("❌ 未登录或登录失效，请手动登录")
                print("❌ 未登录或登录失效，请在浏览器中手动登录")
                input(">>> 完成登录后按 Enter 关闭浏览器...")
        finally:
            context.close()


def cmd_test_notify(cfg: dict) -> None:
    """测试通知功能"""
    from notify import send_notification
    notify_cfg = cfg.get("notify", {})
    product_name = cfg.get("product", {}).get("name", "测试商品")

    logger.info("发送测试通知...")
    send_notification(
        title="🧪 抢购助手通知测试",
        body=f"商品：{product_name}\n这是一条测试通知，如果你收到了，说明通知配置正常。",
        level="info",
        notify_cfg=notify_cfg,
    )
    print("✅ 测试通知已发送，请检查各通知渠道")


def cmd_diagnose(platform: str, config_path: str | None = None) -> None:
    """Run a one-shot page diagnosis."""
    from diagnose_page import diagnose

    diagnose(platform, Path(config_path) if config_path else None)


def _resolve_config_path(platform: str, override: str | None = None) -> str:
    if override:
        return override
    default_config = PLATFORM_MAP[platform]["default_config"]
    if default_config is None:
        raise ValueError(f"平台 {platform} 没有单一默认配置")
    return str(_PROJECT_ROOT / default_config)


def _load_platform_config(platform: str, override: str | None = None) -> dict:
    return load_config(_resolve_config_path(platform, override))


def _run_platform_runner(
    platform: str,
    cfg: dict,
    dry_run_override: bool,
    errors: list[str],
) -> None:
    try:
        platform_info = PLATFORM_MAP[platform]
        module = importlib.import_module(platform_info["module"])
        runner_func = getattr(module, platform_info["runner"])
        logger.info("启动平台 [%s] 监控循环", platform)
        runner_func(cfg, dry_run_override=dry_run_override)
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        logger.error("平台 [%s] 监控线程异常：%s\n%s", platform, exc, tb)
        errors.append(f"{platform}: {exc}")


def run_both_platforms(args: argparse.Namespace) -> None:
    """并行运行京东与大疆官网监控。"""
    jd_cfg = _load_platform_config("jd", args.jd_config)
    dji_cfg = _load_platform_config("dji", args.dji_config)

    if args.no_dry_run:
        jd_cfg.setdefault("purchase", {})["dry_run"] = False
        dji_cfg.setdefault("purchase", {})["dry_run"] = False
        logger.warning("⚠️  --no-dry-run 已指定，两个平台都将执行真实购买操作！")

    dry_run_override = bool(args.dry_run)
    _print_banner("jd", jd_cfg, dry_run_override or jd_cfg.get("purchase", {}).get("dry_run", True))
    _print_banner("dji", dji_cfg, dry_run_override or dji_cfg.get("purchase", {}).get("dry_run", True))

    if args.check_login:
        cmd_check_login(jd_cfg, "jd")
        cmd_check_login(dji_cfg, "dji")
        return

    if args.test_notify:
        cmd_test_notify(jd_cfg)
        cmd_test_notify(dji_cfg)
        return

    if args.diagnose:
        cmd_diagnose("jd", args.jd_config)
        cmd_diagnose("dji", args.dji_config)
        return

    errors: list[str] = []
    threads = [
        threading.Thread(
            target=_run_platform_runner,
            args=("jd", jd_cfg, dry_run_override, errors),
            name="seckill-jd",
        ),
        threading.Thread(
            target=_run_platform_runner,
            args=("dji", dji_cfg, dry_run_override, errors),
            name="seckill-dji",
        ),
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if errors:
        raise RuntimeError("; ".join(errors))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="个人新品抢购助手 — DJI Pocket 4",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
平台选项：
  jd    京东（MVP，推荐）
  dji   大疆官网
  both  京东 + 大疆官网并行监控
  pdd   拼多多（配置框架预留，暂未实现）

示例：
  # 京东 dry-run 测试
  python scripts/run_sale.py --platform jd --dry-run

  # 京东实战（确认配置无误后使用）
  python scripts/run_sale.py --platform jd --no-dry-run

  # 大疆官网 dry-run
  python scripts/run_sale.py --platform dji --dry-run

  # 京东 + 大疆官网并行实战
  python scripts/run_sale.py --platform both --no-dry-run

  # 检查京东登录状态
  python scripts/run_sale.py --platform jd --check-login

  # 测试通知
  python scripts/run_sale.py --platform jd --test-notify

  # 使用自定义配置文件
  python scripts/run_sale.py --platform jd --config config/jd_custom.json
        """,
    )
    parser.add_argument(
        "--platform", "-p",
        choices=list(PLATFORM_MAP.keys()),
        default="jd",
        help="目标平台（默认：jd）",
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="单平台配置文件路径（不指定则使用平台默认配置）",
    )
    parser.add_argument(
        "--jd-config",
        default=None,
        help="both 模式下京东配置文件路径",
    )
    parser.add_argument(
        "--dji-config",
        default=None,
        help="both 模式下大疆官网配置文件路径",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="强制 dry-run 模式（不实际购买）",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        default=False,
        help="强制关闭 dry-run（覆盖配置文件，将执行真实购买！）",
    )
    parser.add_argument(
        "--check-login",
        action="store_true",
        default=False,
        help="仅检查登录状态，不进入监控循环",
    )
    parser.add_argument(
        "--test-notify",
        action="store_true",
        default=False,
        help="发送测试通知并退出",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        default=False,
        help="诊断商品页选择器并退出",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别（覆盖配置文件）",
    )

    args = parser.parse_args()
    platform = args.platform

    if platform == "both":
        log_level = args.log_level or "INFO"
        setup_logging(log_dir=str(_PROJECT_ROOT / "logs"), log_level=log_level, platform="both")
        run_both_platforms(args)
        return

    # 确定配置文件路径
    config_path = _resolve_config_path(platform, args.config)

    # 加载配置
    try:
        cfg = load_config(config_path)
    except FileNotFoundError as e:
        print(f"❌ 配置文件不存在：{e}")
        sys.exit(1)

    # 初始化日志
    log_cfg = cfg.get("logging", {})
    log_level = args.log_level or log_cfg.get("level", "INFO")
    log_dir = str(_PROJECT_ROOT / log_cfg.get("log_dir", "logs"))
    setup_logging(log_dir=log_dir, log_level=log_level, platform=platform)

    # 处理 dry-run 标志
    dry_run_override = False
    if args.dry_run:
        dry_run_override = True
    elif args.no_dry_run:
        cfg.setdefault("purchase", {})["dry_run"] = False
        logger.warning("⚠️  --no-dry-run 已指定，将执行真实购买操作！")

    effective_dry_run = dry_run_override or cfg.get("purchase", {}).get("dry_run", True)

    # 打印横幅
    _print_banner(platform, cfg, effective_dry_run)

    # 特殊命令
    if args.check_login:
        cmd_check_login(cfg, platform)
        return

    if args.test_notify:
        cmd_test_notify(cfg)
        return

    if args.diagnose:
        cmd_diagnose(platform, config_path)
        return

    # 检查平台是否已实现
    platform_info = PLATFORM_MAP[platform]
    if platform_info["module"] is None:
        logger.error("平台 [%s] 尚未实现，请使用 jd 或 dji", platform)
        print(f"❌ 平台 [{platform}] 尚未实现。当前支持：jd、dji")
        sys.exit(1)

    # 动态导入并运行对应平台的监控循环
    module = importlib.import_module(platform_info["module"])
    runner_func = getattr(module, platform_info["runner"])

    logger.info("启动平台 [%s] 监控循环", platform)
    runner_func(cfg, dry_run_override=dry_run_override)


if __name__ == "__main__":
    main()
