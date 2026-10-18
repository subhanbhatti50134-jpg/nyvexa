# Contributing to Nyvexa

Thanks for your interest in improving Nyvexa! Small fixes and focused features
are very welcome.

## Getting Started

1. Fork and clone the repo (see [README](README.md) quick start for
   environment setup, `.env` keys, and running the app on port 8300).
2. Create a branch: `git checkout -b fix/short-description`

## Before Opening a PR

Run the same checks CI would:

```bash
.venv/bin/python -m pytest backend/tests/ -q    # unit tests (mocked LLM, no network)
.venv/bin/ruff check backend/                   # lint (config in ruff.toml)
node --check frontend/js/main.js                # JS syntax
```

All three must pass. Add or extend a test when you change backend behaviour.

## Style Conventions

- **Backend**: FastAPI routers in `backend/routers/`, business logic in
  `backend/services/`, shared config in `backend/core/`. Type hints on new
  functions; keep secrets strictly in `backend/.env` (never hardcode).
- **Frontend**: vanilla ES6 modules (no framework), BEM class names in
  `frontend/css/`, DOM built in `frontend/js/`.
- **Commits**: short imperative summary, like
  `Fix New chat button: loadConversation(null) now resets the session`.

## What Belongs in a PR

- Bug fixes, model/provider updates (NIM/OpenRouter ids change often),
  accessibility improvements, docs.
- Please open an issue first for larger features (new modes, new auth
  providers, storage backends) so the direction can be discussed.

## Code of Conduct

By participating you agree to uphold our
[Code of Conduct](CODE_OF_CONDUCT.md).
