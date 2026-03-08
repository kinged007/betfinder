"""
Shared fixtures for Kalshi bookmaker tests.

All fixtures use REAL Kalshi API calls (no mocking).
The Kalshi market-data API is public and requires no authentication.

Approach:
  1. Hit the live Kalshi API to get an actual open NBA game event.
  2. Seed the in-memory SQLite DB with matching Sport / League / Event rows.
  3. Tests then run the full KalshiBookmaker code against that real data.

If no NBA events are currently open (off-season), the `live_event` fixture
marks all tests as skipped automatically.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.db.base import Base
from app.db.models import Sport, League, Event, Bookmaker, Market, Odds, Preset, Mapping
from app.services.bookmakers.kalshi import KalshiBookmaker
from app.services.bookmakers.kalshi_market_types import KalshiMarketType
from app.services.bookmakers.implementations import *

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
# Series used for tests – NBA game winners have the most consistent liquidity.
TEST_SERIES = "KXNBAGAME"
TEST_LEAGUE = "basketball_nba"


# ──────────────────────────────────────────────────────────────────────────────
# Database fixture – fresh in-memory SQLite per test
# ──────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ──────────────────────────────────────────────────────────────────────────────
# Live Kalshi event – fetched once per module (avoids redundant API calls)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def live_event() -> Dict[str, Any]:
    """
    Fetch the first open NBA game event from the real Kalshi API.

    Returns a dict with:
      event_ticker, home_team, away_team, commence_time, markets (raw list)

    Skips all tests in the module if no open events are found.
    """
    url = f"{KALSHI_BASE}/events"
    params = {
        "series_ticker": TEST_SERIES,
        "status": "open",
        "limit": 1,
        "with_nested_markets": "true",
    }
    with httpx.Client(timeout=15) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()

    events = resp.json().get("events", [])
    if not events:
        pytest.skip(f"No open Kalshi events for series '{TEST_SERIES}' – skipping")

    event = events[0]
    title = event.get("title", "")
    home_team, away_team = KalshiMarketType.extract_teams_from_event_title(title)

    if not home_team or not away_team:
        pytest.skip(f"Cannot parse teams from title '{title}' – skipping")

    expiration_str = (
        event.get("expected_expiration_time") or event.get("close_time", "")
    )
    if expiration_str:
        expiration_dt = datetime.fromisoformat(expiration_str.replace("Z", "+00:00"))
        commence_time = expiration_dt - timedelta(hours=3)
    else:
        commence_time = datetime.now(timezone.utc) + timedelta(hours=24)

    return {
        "event_ticker":  event["event_ticker"],
        "series_ticker": event["series_ticker"],
        "title":         title,
        "home_team":     home_team,
        "away_team":     away_team,
        "commence_time": commence_time,
        "markets":       event.get("markets", []),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Seeded DB – seeds rows matching the live Kalshi event
# ──────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seeded_db(db: AsyncSession, live_event: Dict[str, Any]) -> AsyncSession:
    """
    Seed the DB with sport, league, event, bookmaker, and preset rows matching
    the live Kalshi NBA event so fuzzy-matching and ingester tests work end-to-end.
    """
    sport = Sport(key="basketball", group="Basketball", title="Basketball", active=True)
    db.add(sport)

    league = League(
        key=TEST_LEAGUE,
        group="Basketball",
        title="NBA",
        active=True,
        sport_key="basketball",
    )
    db.add(league)

    event = Event(
        id="live_test_event_001",
        sport_key="basketball",
        league_key=TEST_LEAGUE,
        commence_time=live_event["commence_time"],
        home_team=live_event["home_team"],
        away_team=live_event["away_team"],
        active=True,
    )
    db.add(event)

    bookmaker = Bookmaker(
        key="kalshi",
        title="Kalshi",
        active=True,
        model_type="api",
        config={},
    )
    db.add(bookmaker)

    preset = Preset(
        name="test_preset",
        active=True,
        sports=["basketball"],
        bookmakers=["kalshi"],
        leagues=[TEST_LEAGUE],
        markets=["h2h"],
    )
    db.add(preset)

    await db.commit()
    await db.refresh(bookmaker)
    return db


# ──────────────────────────────────────────────────────────────────────────────
# KalshiBookmaker instance bound to the seeded DB
# ──────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def kalshi(seeded_db: AsyncSession) -> KalshiBookmaker:
    return KalshiBookmaker("kalshi", {}, seeded_db)


# ──────────────────────────────────────────────────────────────────────────────
# Second live event pair for batch tests
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def live_event_pair() -> list:
    """
    Fetch two open NBA events for batch-fetch tests.
    Skips if fewer than two events are available.
    """
    url = f"{KALSHI_BASE}/events"
    params = {
        "series_ticker": TEST_SERIES,
        "status": "open",
        "limit": 2,
        "with_nested_markets": "true",
    }
    with httpx.Client(timeout=15) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()

    events = resp.json().get("events", [])
    if len(events) < 2:
        pytest.skip("Fewer than 2 open NBA events available – skipping batch test")

    parsed = []
    for ev in events:
        title = ev.get("title", "")
        home, away = KalshiMarketType.extract_teams_from_event_title(title)
        if not home or not away:
            continue
        exp_str = ev.get("expected_expiration_time") or ev.get("close_time", "")
        if exp_str:
            exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            commence_time = exp_dt - timedelta(hours=3)
        else:
            commence_time = datetime.now(timezone.utc) + timedelta(hours=24)
        parsed.append({
            "event_ticker":  ev["event_ticker"],
            "home_team":     home,
            "away_team":     away,
            "commence_time": commence_time,
            "markets":       ev.get("markets", []),
        })
    if len(parsed) < 2:
        pytest.skip("Could not parse teams for 2 events – skipping batch test")
    return parsed
