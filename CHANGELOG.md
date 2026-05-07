# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Open-source documentation: `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, GitHub Issue/PR templates, `CHANGELOG.md`.
- `requirements-dev.txt` and `pyproject.toml` (Ruff configuration) for contributors.
- VS Code launch configurations for common `run_sale.py` workflows (`debugpy`).
- One-time log warning when `selectors.login_check` is missing in config (`utils.check_login_valid`).

### Changed

- README restructured for GitHub conventions (badges, TOC, clone URL placeholder, `login_check` documentation, community links).

### Fixed

- (Earlier) JD auto-submit notification now passes the post-submit screenshot path instead of the screenshot directory.
- (Earlier) Stricter out-of-stock detection and JD selector tuning; macOS desktop notification AppleScript escaping; idempotent logging setup.
