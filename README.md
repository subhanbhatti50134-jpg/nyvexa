# Nyvexa

Full-stack AI assistant: streaming chat, autonomous research, tool-using agent, side-by-side model comparison, and per-user knowledge bases — FastAPI + SSE backend, vanilla ES6/BEM frontend, premium light-SaaS interface (ivory/navy, Inter, no neon).

## Features

- **Streaming chat** over Server-Sent Events (`meta`, `research`, `sources`, `token`, `done`)
- **Modes**: `chat`, `research` (plan → search → fetch → synthesize with citations), `deep` (multi-round), `agent` (web_search, fetch_page, calculator, run_python, rag_search), `compare` (two models, two panes)
- **Knowledge base**: upload PDF/TXT/MD/CSV/JSON/Py/HTML, vector + lexical retrieval injected into answers
- **Mandatory auth**: email/password (PBKDF2, 600k iterations with rehash-on-login) or **Google OAuth** (per-attempt state validation); every conversation and file is scoped to its owner
- **Free models**: NVIDIA NIM (`nvapi-…`) and OpenRouter (`:free` models) with automatic cross-provider fallback
- **Light SaaS UI**: ivory `#F7F6F2` / navy `#102747` / blue `#1769C2` palette, header settings menu (auto-read replies, export, clear) and profile menu, simple brand marks, PWA
- Voice input/readout (browser Web Speech API) with **auto-read replies** (Settings → Auto-read), Markdown export, usage meter, SQLite persistence

## Quick start (any laptop)

```bash
# 0. prerequisites: git + Python 3.11+ (https://python.org)

# 1. get the code (repo is public)
git clone https://github.com/subhanbhatti50134-jpg/nyvexa.git
cd nyvexa

# 2. deps
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

# 3. keys — secrets are NOT in the repo; recreate the file
cp backend/.env.example backend/.env
# edit backend/.env → at least one of NVIDIA_API_KEY / OPENROUTER_API_KEY
# (optionally GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET for the Google button)

# 4. run (port 8300)
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8300
```

Open http://localhost:8300 → **Sign up** (the first account inherits any pre-existing local history).

Windows (PowerShell): replace `.venv/bin/` with `.venv\Scripts\`, and start with
`py -m venv .venv` then `.venv\Scripts\python -m uvicorn backend.main:app --port 8300`.

Data (users, conversations, uploads) lives in `data/` next to the app — it is
gitignored, so a fresh clone starts with an empty database.

## Google login (optional)

1. [Google Cloud Console](https://console.cloud.google.com/apis/credentials) → new project → **OAuth consent screen** (External, add yourself as a test user)
2. **Create Credentials → OAuth client ID → Web application**, authorized redirect URI:
   ```
   http://localhost:8300/api/auth/google/callback
   ```
3. Put the values in `backend/.env` and restart:
   ```
   GOOGLE_CLIENT_ID=….apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-…
   ```
4. Verify: `curl -s localhost:8300/api/auth/status` → `"google_enabled": true`

The redirect URI is derived from the request origin automatically — for production, register `https://<your-public-host>/api/auth/google/callback` alongside the localhost one in Google Cloud Console (Credentials → your OAuth client), then publish the consent screen.

## Configuration (`backend/.env`)

| Variable | Purpose |
|---|---|
| `NVIDIA_API_KEY` | NVIDIA NIM free tier — `https://build.nvidia.com/` |
| `OPENROUTER_API_KEY` | OpenRouter free `:free` models — `https://openrouter.ai/keys` |
| `NYVEXA_DEFAULT_MODEL` / `NYVEXA_FALLBACK_MODEL` | default + fallback model ids |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | enables the Google button (redirect URI derived from request origin) |
| `GOOGLE_REDIRECT_URI` | optional — pin the Google redirect URI instead of deriving it |
| `NYVEXA_DATA_DIR` | SQLite + uploads directory (default `./data`) |
| `NYVEXA_RATE_LIMIT` | requests/minute/IP (chat & files; keyed per-account when authenticated) |
| `NYVEXA_AUTH_RATE_LIMIT` | login/signup requests/minute/IP (default 10) |
| `NYVEXA_ROUTER_TIMEOUT` | auto-mode LLM tie-break budget in seconds (default 3 — the heuristic router is instant) |
| `NYVEXA_RESEARCH_PLAN_TIMEOUT` | research query-planner budget in seconds (default 6; search starts in parallel) |
| `NYVEXA_HISTORY_CHAR_BUDGET` | max characters of chat history prefilled per request (default 30000, newest kept) |
| `NYVEXA_CONTROL_TIMEOUT` | per-attempt cap for non-streaming control-plane LLM calls (default 25) |
| `NYVEXA_RESEARCH_MAX_QUERIES`, `NYVEXA_RESEARCH_RESULTS`, `NYVEXA_DEEP_ROUNDS` | research depth |

**Model routing:** ids ending in `:free` go to OpenRouter; prefixes `nvidia/`, `z-ai/`, `meta/`, `mistralai/`, `deepseek-ai/`, `moonshotai/`, `google/` (without `:free`) go to NVIDIA NIM. On failure the chain falls back across both providers automatically.

## Security

Hardening implemented on top of the base app (see [SECURITY.md](SECURITY.md) for the full policy):

- **Auth**: Bearer-header tokens only (`?token=` query auth removed), PBKDF2 (600k, transparent rehash on login), OAuth state = random per-attempt value in an HttpOnly cookie, generic auth error messages
- **Headers**: strict CSP (`script-src 'self'`) on the frontend, `X-Content-Type-Options`, `X-Frame-Options`/`frame-ancestors 'none'`, `Referrer-Policy`, `Permissions-Policy`, COOP, HSTS on HTTPS; `/api/*` exempt (OAuth bridge + Swagger docs)
- **Rate limits**: per-IP on login/signup, per-account on chat/files, per-IP elsewhere
- **Input**: request-size cap (6 MB → 413), `ChatRequest` field caps + strict model-id validation, sanitized SSE error events, upload content sniffing (PDF magic, NUL-byte/UTF-8 checks)
- **Provider isolation**: SSRF-guarded web fetching (public-IP-only with per-hop redirect validation), AST-allowlisted + rlimit-sandboxed `run_python` (6s CPU, 1MB files)
- **Regression coverage**: 41 security tests inside the suite (headers/CSP, tokens, OAuth state, validation, rate limits, body limit, SSRF, sandbox, sniffing, rehash)

## Docker

```bash
docker compose up -d --build     # app on :8300, data in the nyvexa-data volume
```

## Deploy (Hugging Face Spaces)

The app runs as a FastAPI script inside a **ZeroGPU** Space (free tier; no GPU
quota used — all inference is remote via NVIDIA NIM / OpenRouter):

```bash
export HF_TOKEN=hf_…                    # HF token with write access
.venv/bin/python scripts/deploy_hf.py   # uploads code, env, README; waits for RUNNING
```

- The Space's users/conversations DB persists in the private dataset
  `subhan134/nyvexa-data` — pulled at boot, pushed every 10 minutes and on
  shutdown (`backend/services/hf_sync.py`).
- A keepalive thread pings the Space's own `/api/health` every 15 minutes so
  it never sleeps (`backend/services/keepalive.py`).
- `app.py` is the Space entrypoint (ZeroGPU startup-report handshake); local
  development still runs `backend.main:app` directly.

## Development

```bash
.venv/bin/python -m pytest backend/tests/ -q   # 72 tests (API + security + routing/perf; mocked LLM/search, no network)
.venv/bin/ruff check backend/                  # lint (config in ruff.toml)
node --check frontend/js/main.js               # JS syntax
```

### Layout

```
backend/
  main.py            app, middleware, static
  db.py              SQLite: users, sessions, conversations, RAG (+ legacy migration)
  core/              config (providers), auth (sessions/OAuth), rate_limit, context
  routers/           auth, chat (SSE), history, files
  services/          llm (multi-provider), research, agent, tools, rag, websearch, fetcher
frontend/
  index.html         app shell + auth overlay
  css/               theme, layout, chat, sidebar (BEM)
  js/                main (auth gate), chat, history, streaming (SSE), api, voice, markdown
```

### API surface

All `/api/*` routes require `Authorization: Bearer <token>` except: `/api/auth/{signup,login,google,google/callback,status}`, `/api/health`, `/api/models`. Query-string tokens (`?token=`) are intentionally not accepted.

SSE example:

```bash
TOKEN=$(curl -s -X POST localhost:8300/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"…"}' | jq -r .token)

curl -N -X POST localhost:8300/api/chat \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"hello","mode":"chat"}'
```

## Community

- [Code of Conduct](CODE_OF_CONDUCT.md) — how we expect everyone to behave
- [Contributing](CONTRIBUTING.md) — setup, checks, and PR conventions
- [Security Policy](SECURITY.md) — how to report vulnerabilities privately

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Muhammad Subhan.
