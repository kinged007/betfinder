# BetFinder — Agent Guide

## Purpose

BetFinder finds and tracks value bets across bookmakers. FastAPI + SQLAlchemy (async) + Alembic, with a PyInstaller/pystray desktop tray app and Docker deploy. Odds are ingested from The Odds API, Kalshi, Smarkets, SX Bet and others, normalised, analysed for value, and surfaced on a live dashboard.

## How to run checks

```bash
uv sync --all-extras --dev
uv run pytest -q              # tests (Kalshi integration hits live API; skipped if no open events)
uv run ruff check .           # lint (CI runs this)
uv run mypy app               # type check when configured
uv run dev                    # dev server http://localhost:8000 — /health for smoke check
uv run alembic upgrade head   # migrations (Postgres-compatible; SQLite only for dev/tests)
```

Manual health check against a running server: `uv run python tests/verify.py` (expects `GET /health` 200).

## Branches and PRs

- Do not push directly to `master`. Open a branch and a PR for every change.
- Keep commits attributed to `kinged007 <599286+kinged007@users.noreply.github.com>`.
- CI (`ci.yml`) must pass (pytest + ruff) before merge.

## Skills and workflow

- Reuse existing services under `app/services/` and routers under `app/routers/` rather than adding parallel ones.
- Migrations live in `alembic/versions/` — must be Postgres-compatible.

## Permissions

- Keep this repo self-contained. Do not add network calls in tests; mock external services.
- Never commit secrets or `.env` files. `sample.env` holds placeholders only.
