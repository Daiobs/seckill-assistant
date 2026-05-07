# Security Policy

## Supported versions

Security-sensitive fixes are applied to the **latest commit on the default branch**. This project does not maintain separate LTS release lines.

## Reporting a vulnerability

Please **do not** file a public issue for undisclosed security problems (for example, unsafe handling of secrets, or a flaw that could harm users who run the tool).

Preferred channels:

1. **[GitHub Security Advisories](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)** — use **Security → Report a vulnerability** on this repository, if the feature is enabled.
2. **Private message to maintainers** — e.g. email listed on a maintainer’s GitHub profile, after you identify them from commit history or `CODEOWNERS`.

Include:

- Short description and suspected impact
- Steps to reproduce (if applicable)
- Commit SHA or version you tested (if known)

Maintainers will try to acknowledge within a few days. This is a volunteer project; fix timelines vary.

## Scope and expectations

- The tool runs **local** Playwright automation and stores browser profiles under `profiles/` (see `.gitignore`). Misconfiguration or leaked credentials in **your** JSON config are primarily your responsibility; still, we welcome reports if the **default** project behavior is unsafe.
- Automating third-party e‑commerce sites may violate those sites’ terms of use; that is a **compliance / ToS** topic for end users, not necessarily a software vulnerability in this repository.

Thank you for helping keep the community safe.
