# Contributing

感谢你有兴趣参与 **seckill-assistant** 的开发。欢迎通过 Issue / Pull Request 贡献代码、文档或配置经验。

## How to contribute

1. **Fork** 本仓库（将下文 URL 中的 `<YOUR_USERNAME>` 换成你的 GitHub 用户名），从默认分支创建功能分支（例如 `fix/selector-docs`）。

   ```bash
   git clone https://github.com/<YOUR_USERNAME>/seckill-assistant.git
   ```
2. 在本地创建虚拟环境并安装依赖：

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   playwright install chromium
   ```

3. 修改代码或文档后，尽量运行静态检查（若已安装 Ruff）：

   ```bash
   ruff check scripts
   ```

4. 提交时请使用**清晰的中文或英文** commit message，说明「改了什么、为什么」。
5. 打开 **Pull Request**，在描述中说明变更范围、如何验证（例如 `python scripts/run_sale.py --platform jd --dry-run`）。

## Guidelines

- **遵守平台规则与用户协议**：不要提交验证码破解、绕过风控、或明显用于恶意刷单的功能；与本项目 README 中的用途声明保持一致。
- **最小改动**：一次 PR 聚焦单一主题，避免无关格式化或大范围重排。
- **配置与代码分离**：平台相关的选择器、URL 等优先放在 `config/*.json` 或文档中说明，避免硬编码到脚本。
- **隐私**：不要提交 `profiles/`、`logs/`、截图中的账号信息；不要向仓库提交真实 Bark token、SMTP 密码等。

## Code of conduct

参与讨论与评审时请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## Questions

不确定是否适合贡献时，可先开 **Issue** 简要描述想法，维护者会一起对齐方向。
