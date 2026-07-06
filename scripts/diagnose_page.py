"""Diagnose current product page selectors for a platform."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

_SCRIPTS_DIR = Path(__file__).parent.resolve()
_PROJECT_ROOT = _SCRIPTS_DIR.parent.resolve()
sys.path.insert(0, str(_SCRIPTS_DIR))

from utils import (  # noqa: E402
    check_captcha,
    create_browser_context,
    detect_login_state,
    dismiss_cookie_banner,
    find_action_element,
    load_config,
    setup_logging,
    take_screenshot,
    visible_unavailable_cta,
)

LOGGER = logging.getLogger("seckill.diagnose")
CONFIG_PATHS = {
    "jd": _PROJECT_ROOT / "config" / "jd.json",
    "dji": _PROJECT_ROOT / "config" / "dji.json",
}
DIAGNOSTICS_DIR = _PROJECT_ROOT / ".runtime" / "diagnostics"


def _selector_list(selector_text: str) -> list[str]:
    return [item.strip() for item in selector_text.split(",") if item.strip()]


def _candidate(page: Page, selector: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        elements = page.query_selector_all(selector)[:10]
    except Exception as exc:  # noqa: BLE001
        return [{"selector": selector, "error": str(exc)}]
    for el in elements:
        try:
            attrs = el.evaluate(
                """e => ({
                    tag: e.tagName.toLowerCase(),
                    id: e.id || "",
                    className: typeof e.className === "string" ? e.className : "",
                    disabled: !!e.disabled
                })"""
            )
            records.append(
                {
                    "selector": selector,
                    "text": el.evaluate("e => (e.innerText || e.textContent || '').trim()")[:200],
                    "visible": el.is_visible(),
                    "enabled": el.is_enabled(),
                    "tag": attrs.get("tag", ""),
                    "id": attrs.get("id", ""),
                    "class": attrs.get("className", ""),
                    "bbox": el.bounding_box(),
                    "disabled": bool(attrs.get("disabled", False)),
                }
            )
        except Exception as exc:  # noqa: BLE001
            records.append({"selector": selector, "error": str(exc)})
    return records


def _candidates(page: Page, selectors: dict[str, str], key: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for selector in _selector_list(selectors.get(key, "")):
        records.extend(_candidate(page, selector))
    return records


def _cookie_popup_exists(page: Page) -> bool:
    checks = (
        "Accept All Cookies",
        "Accept All",
        "同意",
        "接受",
        "我知道了",
    )
    try:
        for text in checks:
            locator = page.get_by_text(text, exact=False).first
            if locator and locator.is_visible():
                return True
        return bool(page.query_selector("[class*='cookie'], [id*='cookie']"))
    except Exception:  # noqa: BLE001
        return False


def diagnose(platform: str, config_path: Path | None = None) -> dict[str, Any]:
    if platform not in CONFIG_PATHS:
        raise ValueError("platform 必须是 jd 或 dji")
    cfg = load_config(str(config_path or CONFIG_PATHS[platform]))
    log_cfg = cfg.get("logging", {})
    setup_logging(
        log_dir=str(_PROJECT_ROOT / log_cfg.get("log_dir", "logs")),
        log_level=log_cfg.get("level", "INFO"),
        platform=f"{platform}_diagnose",
    )
    product_url = cfg.get("product", {}).get("url", "")
    selectors = cfg.get("selectors", {})
    screenshot_dir = str(_PROJECT_ROOT / log_cfg.get("screenshot_dir", "screenshots"))

    with sync_playwright() as pw:
        context = create_browser_context(pw, cfg.get("browser", {}), str(_PROJECT_ROOT))
        page = context.new_page()
        try:
            page.goto(product_url, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            cookie_popup_exists = _cookie_popup_exists(page)
            dismiss_cookie_banner(page, platform, screenshot_dir)
            screenshot = take_screenshot(page, screenshot_dir, tag=f"{platform}_diagnose")
            buy_texts = ["立即购买", "立即抢购", "马上抢", "购买", "Buy Now"]
            add_texts = ["加入购物车", "加购物车", "Add to Cart"]
            buy_match = find_action_element(page, selectors, "btn_buy_now", buy_texts)
            add_match = find_action_element(page, selectors, "btn_add_to_cart", add_texts)
            result = {
                "platform": platform,
                "generated_at": time.time(),
                "url": page.url,
                "title": page.title(),
                "login_state": detect_login_state(page, selectors, platform),
                "captcha_detected": check_captcha(page, selectors, platform),
                "cookie_popup_exists": cookie_popup_exists,
                "cookie_popup_checked": True,
                "latest_screenshot": screenshot,
                "buy_button_match": _match_view(buy_match),
                "add_cart_match": _match_view(add_match),
                "buy_button_candidates": _candidates(page, selectors, "btn_buy_now"),
                "add_cart_candidates": _candidates(page, selectors, "btn_add_to_cart"),
                "sold_out_candidates": _candidates(page, selectors, "btn_out_of_stock"),
                "sold_out_detected": visible_unavailable_cta(
                    page, selectors.get("btn_out_of_stock", "")
                ),
            }
        finally:
            context.close()

    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DIAGNOSTICS_DIR / f"{platform}_latest.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    LOGGER.info("诊断完成：%s", out_path)
    print(json.dumps({"path": str(out_path), "platform": platform}, ensure_ascii=False))
    return result


def _match_view(match: dict[str, Any] | None) -> dict[str, Any] | None:
    if not match:
        return None
    return {
        key: value
        for key, value in match.items()
        if key in ("selector", "text", "tag", "id", "class", "bbox", "disabled")
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="诊断商品页选择器和页面状态")
    parser.add_argument("--platform", choices=["jd", "dji"], required=True)
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    diagnose(args.platform, Path(args.config) if args.config else None)


if __name__ == "__main__":
    main()
