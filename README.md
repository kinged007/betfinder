# BetFinder

[![CI](https://github.com/kinged007/betfinder/actions/workflows/ci.yml/badge.svg)](https://github.com/kinged007/betfinder/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](pyproject.toml)
[![Version](https://img.shields.io/badge/version-0.1.2-blue.svg)](pyproject.toml)

Find and track value bets across bookmakers — FastAPI + SQLAlchemy with odds analysis, a desktop tray app, and Docker deploy.

BetFinder ingests odds from multiple bookmakers (The Odds API, Kalshi, Smarkets, SX Bet and others), normalises them, runs value analysis against configurable presets, and surfaces opportunities on a live dashboard. Bets are tracked through their full lifecycle (pending → open → won/lost/void) with Telegram notifications and optional auto-trade execution.

![Dashboard screenshot](./docs/screenshot.png)
*Screenshot to be added — dashboard at `http://localhost:8000` after `uv run dev`.*

## Install

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/kinged007/betfinder.git && cd betfinder
uv sync
cp sample.env .env   # fill in THE_ODDS_API_KEY etc.
uv run alembic upgrade head
```

Or grab a pre-built executable from [Releases](https://github.com/kinged007/betfinder/releases) (Windows / macOS / Linux) — it bootstraps the settings file and runs in the system tray.

### Docker

```bash
docker compose up -d   # app + Postgres, runs migrations on startup
```

## Usage

```bash
uv run dev    # dev server with reload — http://localhost:8000
uv run prod   # production server (port from .env, default 8123)
uv run build      # PyInstaller tray app (dist in packages/)
uv run installer  # Windows Inno Setup installer
```

- **Dashboard:** http://localhost:8000
- **API docs:** http://localhost:8000/docs
- **Bet History** tracks every trade (manual, pending, open, won/lost/void) with bulk settle/edit.
- **Presets** configure which leagues/bookmakers/markets to monitor and the value threshold.

See `sample.env` for all configuration options and `docs/` for bet lifecycle details.

## How it is tested

```bash
uv sync --all-extras --dev
uv run pytest              # unit + Kalshi integration tests (live API, skipped if no open events)
uv run ruff check .        # lint (CI)
uv run mypy app            # type check (CI, when configured)
```

Kalshi integration tests in `tests/kalshi/` hit the live public market-data API (no auth) and validate end-to-end discovery, standardisation and odds updates against an in-memory SQLite DB. `tests/verify.py` is a manual health-check against a running server (`GET /health`).

CI (`.github/workflows/ci.yml`) runs `pytest` and `ruff check` on every push and pull request to `master`.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Alembic · APScheduler · PyInstaller/pystray (desktop) · Docker · The Odds API · Telegram notifications
