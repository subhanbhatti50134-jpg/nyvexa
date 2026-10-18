# Security Policy

## Supported Versions

Only the latest commit on `main` (and the deployed Hugging Face Space built
from it) receives security fixes. There are no versioned releases yet.

| Version | Supported |
| --- | --- |
| `main` | ✅ |
| older commits | ❌ |

## Security Posture (current `main`)

Implemented controls (committed in `e7216fe`, covered by `backend/tests/test_security.py`):

- **Authentication**: Bearer-header tokens only; PBKDF2 password hashing at 600k iterations with transparent rehash-on-login; Google OAuth uses a random per-attempt state value stored in an HttpOnly cookie and compared with a constant-time check; auth errors returned to clients are generic (details stay in server logs)
- **Headers**: strict CSP (`script-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`) on frontend pages, plus `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, and HSTS on HTTPS. `/api/*` responses skip CSP (OAuth bridge pages and Swagger UI need inline/CDN scripts)
- **Rate limiting**: 10/min per IP on login/signup (`NYVEXA_AUTH_RATE_LIMIT`); per-account keys on `/api/chat` and `/api/files` (IP fallback for anonymous traffic)
- **Input validation**: 6 MB request body cap (413), `ChatRequest` field length/shape caps with strict model-id and conversation-id validation, single-line/truncated SSE error events, upload content sniffing (PDF magic bytes, NUL-byte and strict UTF-8 checks for text)
- **Outbound isolation**: web fetching rejects non-public targets (loopback, RFC1918, link-local/metadata, CGNAT) and re-validates every redirect hop; `run_python` enforces an AST import allowlist plus no-dunder/no-`open`-attribute rules, `-I` isolated mode, and OS resource limits (6s CPU, 1MB file size, 64 fds)
- **Secrets**: gitignored `backend/.env`, no secret values in logs, error responses, deploy output, or git history (periodic pattern scan of all revisions)

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

## Accepted Risks (known, documented)

These are deliberate tradeoffs; reports improving them are welcome, but they
are not considered vulnerabilities on their own:

- **Email enumeration on signup**: `POST /api/auth/signup` returns `409` for an
  existing email (kept for UX and covered by the existing test suite)
- **CORS `Access-Control-Allow-Origin: *`**: auth uses the `Authorization`
  header (not cookies) and `allow_credentials` is off, so no ambient-credential
  CSRF surface exists
- **Session tokens stored in plaintext** in the SQLite database (rotating the
  scheme would invalidate all sessions)
- **`run_python` sandbox is language-level** (AST allowlist + rlimits, no
  seccomp/container) — deploy in a container for hostile multi-tenant use
- **DNS-rebinding TOCTOU window** in the fetcher between the public-IP check
  and the connection (per-hop redirect validation covers the practical cases)
- **`X-Forwarded-For` is trusted** (`--forwarded-allow-ips='*'`) so behind a
  bare (non-terminating) proxy, rate-limit keys fall back to the proxy IP
