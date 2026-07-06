"""
utils.py — 公共工具函数
提供：日志初始化、配置加载、截图保存、时间计算、浏览器 context 创建
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from playwright.sync_api import (
    BrowserContext,
    Page,
    Playwright,
)

_login_check_missing_warned: list[bool] = [False]

# ---------------------------------------------------------------------------
# 日志初始化
# ---------------------------------------------------------------------------

def setup_logging(
    log_dir: str = "logs",
    log_level: str = "INFO",
    platform: str = "seckill",
) -> logging.Logger:
    """
    初始化日志：同时输出到控制台和按日期命名的日志文件。
    返回根 logger。
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_path / f"{platform}_{date_str}.log"

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger("seckill")
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:  # noqa: BLE001
            pass
    root.setLevel(numeric_level)

    # 控制台 handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(numeric_level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # 文件 handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(numeric_level)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    root.info("日志初始化完成，日志文件：%s", log_file)
    return root


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict[str, Any]:
    """加载 JSON 配置文件，剔除以 _comment 开头的键"""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    # 移除注释键
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def resolve_path(base_dir: str, relative_path: str) -> Path:
    """将配置中的相对路径解析为绝对路径（相对于项目根目录）"""
    base = Path(base_dir).resolve()
    return base / relative_path


# ---------------------------------------------------------------------------
# 截图保存
# ---------------------------------------------------------------------------

def take_screenshot(
    page: Page,
    screenshot_dir: str,
    tag: str = "screenshot",
) -> str:
    """
    对当前页面截图并保存。
    返回截图文件的绝对路径字符串。
    """
    logger = logging.getLogger("seckill.utils")
    scr_path = Path(screenshot_dir)
    scr_path.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = scr_path / f"{tag}_{ts}.png"

    try:
        page.screenshot(path=str(filename), full_page=False)
        logger.info("截图已保存：%s", filename)
    except Exception as exc:  # noqa: BLE001
        logger.error("截图失败：%s", exc)
        return ""
    return str(filename)


# ---------------------------------------------------------------------------
# 时间计算
# ---------------------------------------------------------------------------

def seconds_until_sale(sale_time_str: str, tz_name: str = "Asia/Shanghai") -> float:
    """
    计算距离开售时间还有多少秒（负值表示已过开售时间）。
    sale_time_str 格式：'2025-12-01 10:00:00'
    """
    tz = ZoneInfo(tz_name)
    sale_dt = datetime.strptime(sale_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
    now_dt = datetime.now(tz=tz)
    return (sale_dt - now_dt).total_seconds()


def format_countdown(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS 字符串"""
    if seconds < 0:
        return "已过开售时间"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# 浏览器 Context 创建（持久化 Profile）
# ---------------------------------------------------------------------------

def create_browser_context(
    playwright: Playwright,
    browser_cfg: dict[str, Any],
    project_root: str,
) -> BrowserContext:
    """
    使用持久化 profile 创建 Chromium BrowserContext。
    profile_dir 相对于 project_root。
    """
    logger = logging.getLogger("seckill.utils")

    profile_dir = resolve_path(project_root, browser_cfg["profile_dir"])
    profile_dir.mkdir(parents=True, exist_ok=True)

    viewport = browser_cfg.get("viewport", {"width": 1280, "height": 800})
    user_agent = browser_cfg.get("user_agent", "")
    headless = browser_cfg.get("headless", False)
    slow_mo = browser_cfg.get("slow_mo", 50)

    logger.info("使用持久化 Profile：%s", profile_dir)
    logger.info("headless=%s, slow_mo=%s", headless, slow_mo)

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        slow_mo=slow_mo,
        viewport=viewport,
        user_agent=user_agent if user_agent else None,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
        ignore_default_args=["--enable-automation"],
    )

    # 注入反检测脚本
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
        window.chrome = { runtime: {} };
    """)

    return context


# ---------------------------------------------------------------------------
# 商品页「无货/售罄」辅助（避免全局 [disabled] 误报）
# ---------------------------------------------------------------------------

def visible_unavailable_cta(page: Page, sel: str) -> bool:
    """
    选择器是否匹配到「主操作区不可用」的可见节点。
    - button / a / input：必须不可点（通常 disabled），避免误标任意可见 disabled 控件为无货。
    - 其他标签（如 div.sold-out）：仅由选择器本身表达无货语义，可见即算匹配。
    """
    if not sel:
        return False
    try:
        for s in [x.strip() for x in sel.split(",")]:
            if not s:
                continue
            el = page.query_selector(s)
            if not el or not el.is_visible():
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            if tag in ("button", "a", "input"):
                if not el.is_enabled():
                    return True
            else:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ---------------------------------------------------------------------------
# 检测验证码 / 登录失效
# ---------------------------------------------------------------------------

def check_captcha(page: Page, selectors: dict[str, str]) -> bool:
    """
    检测页面是否出现验证码或滑块。
    返回 True 表示检测到，需要人工接管。
    """
    captcha_sel = selectors.get("captcha_container", "")
    if not captcha_sel:
        return False
    try:
        el = page.query_selector(captcha_sel)
        if el and el.is_visible():
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def check_login_valid(page: Page, selectors: dict[str, str]) -> bool:
    """
    检测当前登录状态是否有效。
    返回 True 表示已登录，False 表示未登录或登录失效。
    """
    login_sel = selectors.get("login_check", "")
    if not login_sel:
        if not _login_check_missing_warned[0]:
            logging.getLogger("seckill.utils").warning(
                "selectors.login_check 未配置：无法检测登录态，将默认视为已登录。"
                " 请在 config/*.json 的 selectors 中配置 login_check（见 README）。"
            )
            _login_check_missing_warned[0] = True
        return True
    try:
        el = page.query_selector(login_sel)
        if el:
            text = el.inner_text().strip()
            # 京东：未登录时显示"请登录"，已登录时显示昵称
            if "请登录" in text or "登录" == text:
                return False
            return True
    except Exception:  # noqa: BLE001
        pass
    return True


# ---------------------------------------------------------------------------
# 自动提交前的订单校验
# ---------------------------------------------------------------------------

def _page_text(page: Page) -> str:
    """读取页面可见文本；失败时返回空字符串。"""
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:  # noqa: BLE001
        return ""


def _parse_cny_amount(text: str) -> float | None:
    """从一段文本中提取人民币金额。"""
    normalized = text.replace(",", "").replace("，", "")
    match = re.search(r"(?:¥|￥|CNY|RMB)?\s*([0-9]{2,6}(?:\.[0-9]{1,2})?)", normalized)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def extract_order_total(page: Page, selectors: dict[str, str]) -> float | None:
    """
    尝试从结算页提取订单应付金额。
    优先使用配置中的 order_total 选择器；失败后在页面文本中查找应付/合计附近的金额。
    """
    order_total_sel = selectors.get("order_total", "")
    if order_total_sel:
        for sel in [x.strip() for x in order_total_sel.split(",")]:
            if not sel:
                continue
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    amount = _parse_cny_amount(el.inner_text())
                    if amount is not None:
                        return amount
            except Exception:  # noqa: BLE001
                pass

    text = _page_text(page)
    if not text:
        return None

    patterns = [
        r"(?:应付|实付|需支付|待支付|订单总额|商品总额|合计|总计)[^\n\r¥￥0-9]{0,20}(?:¥|￥)?\s*([0-9]{2,6}(?:\.[0-9]{1,2})?)",
        r"(?:¥|￥)\s*([0-9]{2,6}(?:\.[0-9]{1,2})?)",
    ]
    amounts: list[float] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            try:
                amounts.append(float(match.group(1).replace(",", "")))
            except ValueError:
                continue
        if amounts:
            break

    return max(amounts) if amounts else None


def validate_order_before_submit(
    page: Page,
    cfg: dict[str, Any],
    platform: str,
) -> tuple[bool, str]:
    """
    自动提交订单前做硬性校验。
    通过时返回 (True, message)，失败时返回 (False, reason)。
    """
    purchase_cfg = cfg.get("purchase", {})
    selectors = cfg.get("selectors", {})
    product_cfg = cfg.get("product", {})

    body_text = _page_text(page)
    body_text_lower = body_text.lower()
    required_keywords = purchase_cfg.get("require_order_keywords")
    if required_keywords is None:
        required_keywords = product_cfg.get("required_keywords", [])

    missing = [
        keyword for keyword in required_keywords
        if str(keyword).lower() not in body_text_lower
    ]
    if missing:
        return False, f"{platform} 订单页缺少商品关键词：{', '.join(map(str, missing))}"

    total = extract_order_total(page, selectors)
    max_total = purchase_cfg.get("max_order_total_cny")
    require_total_detected = purchase_cfg.get("require_total_detected", True)

    if require_total_detected and total is None:
        return False, f"{platform} 未能识别订单金额，已阻止自动提交"

    if total is not None and max_total is not None and total > float(max_total):
        return False, f"{platform} 订单金额 {total:.2f} 超过上限 {float(max_total):.2f}"

    if total is None:
        return True, f"{platform} 商品关键词校验通过，未配置金额强制识别"
    return True, f"{platform} 商品关键词校验通过，订单金额 {total:.2f}"


# ---------------------------------------------------------------------------
# 等待工具
# ---------------------------------------------------------------------------

def smart_sleep(seconds: float, logger: logging.Logger | None = None) -> None:
    """
    可中断的 sleep，每 0.1 秒检查一次，便于 Ctrl+C 响应。
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(min(0.1, end - time.monotonic()))


# ---------------------------------------------------------------------------
# 按钮状态枚举
# ---------------------------------------------------------------------------

class ButtonState:
    UNKNOWN = "unknown"
    OUT_OF_STOCK = "out_of_stock"
    APPOINTMENT = "appointment"       # 预约/到货通知
    PRESALE = "presale"               # 预售
    ADD_TO_CART = "add_to_cart"       # 加入购物车
    BUY_NOW = "buy_now"               # 立即购买（可直接下单）
    COMING_SOON = "coming_soon"       # 即将开售


class SeckillState:
    WAITING = "waiting"               # 等待开售时间
    WARMUP = "warmup"                 # 预热模式（开售前 warmup_seconds_before 秒）
    HIGH_FREQ = "high_freq"           # 高频轮询（开售前 high_freq_seconds_before 秒）
    PURCHASING = "purchasing"         # 正在购买流程中
    SUCCESS = "success"               # 进入结算页，等待人工支付
    PAUSED = "paused"                 # 暂停，等待人工接管
    DONE = "done"                     # 任务结束
