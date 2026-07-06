# seckill-assistant · 新品抢购助手

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Playwright](https://img.shields.io/badge/playwright-Chromium-45ba4b.svg)](https://playwright.dev/python/)

**Personal Playwright helper** for timed product checks on JD.com and DJI Store (China).  
基于 **Python** 与 **Playwright** 的本地自动化脚本：可配置开售时间、轮询策略、页面选择器与自动提交前校验。当前本地配置已改为 **Osmo Pocket 4P**，支持京东与大疆官网并行监控。

> **免责声明**：仅供个人学习与自用；禁止商业用途或恶意刷单。请遵守各平台用户协议，勿高频请求；使用本工具导致的账号限制等后果由使用者自行承担。

---

## 目录

- [核心特性](#核心特性)
- [环境准备](#环境准备与安装)
- [配置说明](#配置说明)
- [运行方式](#运行方式)
- [目录结构](#目录结构)
- [开源与贡献](#开源与贡献)
- [常见问题](#常见问题与注意事项)

---

## 核心特性

- **持久化浏览器 Profile**：登录一次后凭证保存在本地 `profiles/`（已加入 `.gitignore`，勿提交）。
- **配置与代码分离**：商品 URL、开售时间、轮询间隔、CSS 选择器等均在 `config/*.json`。
- **多阶段轮询**：等待期 → 预热期 → 高频期（间隔可配）。
- **Dry-Run**：演练检测与日志/截图，不执行真实购买点击。
- **人工接管**：验证码、滑块、登录失效时暂停并通知，终端确认后继续。
- **自动提交校验**：`auto_submit_order: true` 时，会先检查订单页商品关键词与金额上限，再点击提交订单。
- **通知**：桌面、日志、Bark、PushPlus、SMTP、Webhook。

---

## 环境准备与安装

### 1. Python

需要 **Python 3.9+**。

### 2. 获取代码

```bash
git clone https://github.com/jackmac2077-beep/seckill-assistant.git
cd seckill-assistant
```

若你尚未推送到 GitHub，可直接在解压/克隆后的项目根目录继续。

### 3. 依赖

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -r requirements.txt
```

可选（贡献者 / 本地检查）：

```bash
pip install -r requirements-dev.txt
```

### 4. Playwright 浏览器

```bash
playwright install chromium
```

---

## 配置说明

配置文件在 `config/`：`jd.json`、`dji.json`；`pdd.json` 为预留框架。

| 节点 | 参数 | 说明 |
| --- | --- | --- |
| `product` | `url` | 商品详情页链接。 |
| `product` | `required_keywords` | 自动提交前必须出现在订单页的商品关键词。 |
| `schedule` | `sale_time` | 开售时间 `YYYY-MM-DD HH:MM:SS`；留空表示立即进入高频监控。 |
| `schedule` | `poll_interval_*` | 各阶段轮询间隔（秒）；高频建议不低于约 `0.3`。 |
| `selectors` | `btn_buy_now` 等 | 主按钮 CSS，逗号分隔多备选。 |
| `selectors` | `order_total` | 自动提交前用于识别订单金额的选择器。 |
| `selectors` | **`login_check`** | **建议必填**：用于判断登录态的元素；未配置时程序会 **打一次 WARNING** 并默认视为已登录（可能误判）。 |
| `purchase` | `dry_run` | `true` 为演练；`false` 为实战。 |
| `purchase` | `auto_submit_order` | `true` 时自动提交订单；提交前仍会做关键词和金额校验。 |
| `purchase` | `max_order_total_cny` | 订单金额上限，超过会阻止自动提交并通知人工接管。 |
| `notify` | `methods` | 如 `["desktop", "log", "bark"]`。 |
| `browser` | `headless` | 建议 `false` 便于观察与人工验证。 |

---

## 运行方式

统一入口：`scripts/run_sale.py`。

**1. 登录准备**

```bash
python scripts/run_sale.py --platform jd --check-login
python scripts/run_sale.py --platform dji --check-login
```

在浏览器中完成登录后关闭窗口；凭证写入 `profiles/jd_profile/` 与 `profiles/dji_profile/`。

**2. 测试通知（可选）**

```bash
python scripts/run_sale.py --platform jd --test-notify
```

**3. Dry-Run 演练**

```bash
python scripts/run_sale.py --platform jd --dry-run
python scripts/run_sale.py --platform dji --dry-run
python scripts/run_sale.py --platform both --dry-run
```

**4. 实战自动提交（务必确认配置）**

```bash
python scripts/run_sale.py --platform jd --no-dry-run
python scripts/run_sale.py --platform dji --no-dry-run
python scripts/run_sale.py --platform both --no-dry-run
```

默认配置已开启 `auto_submit_order: true` 与 `dry_run: false`，并将金额上限设为 `4500` 元。运行前请确认商品链接、收货地址、支付方式、`require_order_keywords` 与 `max_order_total_cny`。

大疆示例：`--platform dji`，配置文件默认 `config/dji.json`。自定义配置：`--config path/to/custom.json`。并行模式可用 `--jd-config` 与 `--dji-config` 分别指定配置文件。

**VS Code 调试**：在运行和调试面板中选择 `run_sale: JD dry-run` 等配置（见 [`.vscode/launch.json`](.vscode/launch.json)）。

---

## 目录结构

```text
seckill-assistant/
├── .github/              # Issue / PR 模板
├── config/               # JSON 配置
├── scripts/              # 入口与平台逻辑
├── profiles/             # 浏览器数据（本地生成，勿提交）
├── logs/                 # 日志
├── screenshots/          # 截图
├── requirements.txt
├── requirements-dev.txt  # 可选：ruff 等
├── pyproject.toml        # Ruff 等工具配置
├── LICENSE               # MIT
├── CONTRIBUTING.md
├── SECURITY.md
├── CODE_OF_CONDUCT.md
├── CHANGELOG.md
└── README.md
```

---

## 开源与贡献

- 参与贡献请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- 安全相关私下报告见 [SECURITY.md](SECURITY.md)。
- 变更记录：[CHANGELOG.md](CHANGELOG.md)。
- 许可证：[MIT](LICENSE)。

---

## 常见问题与注意事项

1. **验证码与滑块**：不包含自动破解；触发风控后请在浏览器内手动完成，终端按 `Enter` 继续。
2. **自动提交**：当前本地配置会自动提交订单；如需只监控不下单，请改 `purchase.dry_run=true` 或运行时加 `--dry-run`。
3. **大疆站点**：可适当增大 `slow_mo` 或轮询间隔以降低拦截概率。
4. **系统时间**：请开启 NTP，保证本机时间准确。
