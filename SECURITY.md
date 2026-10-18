# Security Policy

## Supported Versions

Only the latest commit on `main` (and the deployed Hugging Face Space built
from it) receives security fixes. There are no versioned releases yet.

| Version | Supported |
| --- | --- |
| `main` | ✅ |
| older commits | ❌ |

## Reporting a Vulnerability

Please **do not** open a public issue for security vulnerabilities.

Report them privately via
[GitHub Security Advisories → Report a vulnerability](https://github.com/subhanbhatti50134-jpg/nyvexa/security/advisories/new),
or email **subhanbhatti50134@gmail.com** with:

- what is affected (endpoint, file, configuration)
- steps to reproduce
- potential impact

You will get a response within a few days. Once a fix is merged and deployed,
the issue will be disclosed publicly.

## Scope Notes

- Secrets (`backend/.env`, API keys) are gitignored by design — never commit
  them, and rotate any key that leaks.
- User data (SQLite, uploads) lives in gitignored `data/` (locally) or a
  private HF dataset (on the Space) — reports about data isolation between
  accounts are especially welcome.
