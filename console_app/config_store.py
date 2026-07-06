"""Safe config read/write helpers for the local console."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATHS = {
    "jd": PROJECT_ROOT / "config" / "jd.json",
    "dji": PROJECT_ROOT / "config" / "dji.json",
}
DEFAULT_SALE_TIME = "2026-07-07 20:00:00"

SAFE_FIELD_MAP = {
    "product_url": ("product", "url"),
    "product_required_keywords": ("product", "required_keywords"),
    "sale_time": ("schedule", "sale_time"),
    "dry_run": ("purchase", "dry_run"),
    "auto_submit_order": ("purchase", "auto_submit_order"),
    "require_order_keywords": ("purchase", "require_order_keywords"),
    "max_order_total_cny": ("purchase", "max_order_total_cny"),
    "headless": ("browser", "headless"),
    "slow_mo": ("browser", "slow_mo"),
}


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _as_keyword_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _console_view(platform: str, cfg: dict[str, Any]) -> dict[str, Any]:
    product = cfg.get("product", {})
    schedule = cfg.get("schedule", {})
    purchase = cfg.get("purchase", {})
    browser = cfg.get("browser", {})
    return {
        "platform": platform,
        "product_url": product.get("url", ""),
        "product_required_keywords": product.get("required_keywords", []),
        "sale_time": schedule.get("sale_time") or DEFAULT_SALE_TIME,
        "dry_run": bool(purchase.get("dry_run", True)),
        "auto_submit_order": bool(purchase.get("auto_submit_order", False)),
        "require_order_keywords": purchase.get("require_order_keywords", []),
        "max_order_total_cny": purchase.get("max_order_total_cny"),
        "headless": bool(browser.get("headless", False)),
        "slow_mo": browser.get("slow_mo", 50),
    }


def get_console_config() -> dict[str, Any]:
    """Return only fields the console is allowed to display/edit."""
    platforms = {
        platform: _console_view(platform, _read_json(path))
        for platform, path in CONFIG_PATHS.items()
    }
    return {
        "default_sale_time": DEFAULT_SALE_TIME,
        "platforms": platforms,
    }


def _set_nested(cfg: dict[str, Any], path: tuple[str, str], value: Any) -> None:
    section, key = path
    cfg.setdefault(section, {})[key] = value


def _coerce_field(field: str, value: Any) -> Any:
    if field in ("product_required_keywords", "require_order_keywords"):
        return _as_keyword_list(value)
    if field in ("dry_run", "auto_submit_order", "headless"):
        return bool(value)
    if field == "max_order_total_cny":
        if value in ("", None):
            return None
        return float(value)
    if field == "slow_mo":
        if value in ("", None):
            return 50
        return int(value)
    if field == "sale_time":
        return str(value or DEFAULT_SALE_TIME).strip()
    return str(value).strip()


def update_console_config(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Update only safe console fields while preserving unknown JSON fields.

    Preferred payload shape:
      {"platforms": {"jd": {"product_url": "..."}, "dji": {...}}}
    """
    platforms_payload = payload.get("platforms", payload)
    for platform, updates in platforms_payload.items():
        if platform not in CONFIG_PATHS or not isinstance(updates, dict):
            continue
        cfg_path = CONFIG_PATHS[platform]
        cfg = _read_json(cfg_path)
        for field, value in updates.items():
            nested_path = SAFE_FIELD_MAP.get(field)
            if nested_path is None:
                continue
            _set_nested(cfg, nested_path, _coerce_field(field, value))
        _write_json(cfg_path, cfg)
    return get_console_config()


def validate_live_start(platforms: list[str]) -> tuple[bool, str]:
    """Check required safety fields before live no-dry-run monitoring starts."""
    for platform in platforms:
        cfg = _read_json(CONFIG_PATHS[platform])
        purchase = cfg.get("purchase", {})
        schedule = cfg.get("schedule", {})
        keywords = purchase.get("require_order_keywords", [])
        if not purchase.get("max_order_total_cny"):
            return False, f"{platform} 缺少 max_order_total_cny"
        if not keywords:
            return False, f"{platform} 缺少 require_order_keywords"
        if not schedule.get("sale_time"):
            return False, f"{platform} 缺少 sale_time"
        if cfg.get("browser", {}).get("headless", False):
            return False, f"{platform} 实战模式请保持 headless=false"
    return True, "ok"

