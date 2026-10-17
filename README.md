# Nyvexa

Full-stack AI assistant: streaming chat, autonomous research, tool-using agent, side-by-side model comparison, and per-user knowledge bases — FastAPI + SSE backend, vanilla ES6/BEM frontend, themed around a glowing cosmic orb.

## Features

- **Streaming chat** over Server-Sent Events (`meta`, `research`, `sources`, `token`, `done`)
- **Modes**: `chat`, `research` (plan → search → fetch → synthesize with citations), `deep` (multi-round), `agent` (web_search, fetch_page, calculator, run_python, rag_search), `compare` (two models, two panes)
- **Knowledge base**: upload PDF/TXT/MD/CSV/JSON/Py/HTML, vector + lexical retrieval injected into answers
- **Mandatory auth**: email/password (PBKDF2) or **Google OAuth**; every conversation and file is scoped to its owner
- **Free models**: NVIDIA NIM (`nvapi-…`) and OpenRouter (`:free` models) with automatic cross-provider fallback
- Voice input/readout (browser Web Speech API), Markdown export, usage meter, PWA, SQLite persistence

## Quick start

```bash
# 1. deps
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn httpx openai python-dotenv pypdf beautifulsoup4 ddgs pytest ruff

# 2. keys
cp backend/.env.example backend/.env
# edit backend/.env → at least one of NVIDIA_API_KEY / OPENROUTER_API_KEY

# 3. run (port 8300)
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8300
```

Open http://localhost:8300 → **Sign up** (the first account inherits any pre-existing local history).

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
| `NYVEXA_RATE_LIMIT` | requests/minute/IP |
| `NYVEXA_RESEARCH_MAX_QUERIES`, `NYVEXA_RESEARCH_RESULTS`, `NYVEXA_DEEP_ROUNDS` | research depth |

**Model routing:** ids ending in `:free` go to OpenRouter; prefixes `nvidia/`, `z-ai/`, `meta/`, `mistralai/`, `deepseek-ai/`, `moonshotai/`, `google/` (without `:free`) go to NVIDIA NIM. On failure the chain falls back across both providers automatically.

## Docker

```bash
docker compose up -d --build     # app on :8300, data in the nyvexa-data volume
```

## Development

```bash
.venv/bin/python -m pytest backend/tests/ -q   # 12 tests (mocked LLM/search, no network)
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

All `/api/*` routes require `Authorization: Bearer <token>` except: `/api/auth/{signup,login,google,google/callback,status}`, `/api/health`, `/api/models`.

SSE example:

```bash
TOKEN=$(curl -s -X POST localhost:8300/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"…"}' | jq -r .token)

curl -N -X POST localhost:8300/api/chat \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"hello","mode":"chat"}'
```
