"""
Integration tests for Kalshi bookmaker.

All tests make REAL calls to the live Kalshi public API.
No mocking – the tests validate actual API responses, parsing, and DB operations.
"""

import pytest
import logging
from unittest.mock import patch, MagicMock
from typing import Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Bookmaker, Odds, Market, Event, Mapping
from app.services.bookmakers.kalshi import KalshiBookmaker
from app.services.bookmakers.base import BookmakerFactory
from app.services.ingester import DataIngester
from app.services.the_odds_api import TheOddsAPIClient

logger = logging.getLogger(__name__)

# Skip all tests in module if no live event could be found
pytestmark = pytest.mark.asyncio

class TestKalshiIntegrationEndToEnd:
    """
    End-to-End integration test for Kalshi bookmaker.
    Validates:
      1. Event Discovery (fetch_league_odds & matching)
      2. Data Standardization (ingester processing OddsEvents)
      3. Odds Update Efficiency (obtain_odds using stored SIDs)
    """

    async def test_kalshi_bookmaker_registration(self):
        """Verify the class has required attributes and is registered."""
        assert KalshiBookmaker.name == "kalshi"
        assert KalshiBookmaker.title
        assert "kalshi" in BookmakerFactory.get_registered_keys()

    async def test_kalshi_full_lifecycle(
        self, kalshi: KalshiBookmaker, seeded_db: AsyncSession, live_event: Dict[str, Any]
    ):
        """
        Runs the full realistic lifecycle over the real Kalshi API testing 
        discovery, parsing, injection into DB, and efficient batched updating.
        """
        # We start with the seeded database which contains a matching Event but NO Odds.
        bk = (await seeded_db.execute(select(Bookmaker).where(Bookmaker.key == "kalshi"))).scalar_one()
        odds_count = len((await seeded_db.execute(select(Odds).where(Odds.bookmaker_id == bk.id))).scalars().all())
        assert odds_count == 0, "Expected DB to have no Odds initially"

        # =========================================================================
        # 1. EVENT DISCOVERY
        # =========================================================================
        # We spy on `make_request` to verify efficient API usage.
        with patch.object(kalshi, "make_request", wraps=kalshi.make_request) as spy_req:
            # fetch_league_odds will query Kalshi /events endpoint for the NBA series
            odds_events = await kalshi.fetch_league_odds("basketball_nba")
        
        assert len(odds_events) > 0, "No events returned from Kalshi"
        
        # Verify it used the events endpoint
        endpoints_called = [str(call.args[1]) for call in spy_req.call_args_list]
        assert any("/events" in ep for ep in endpoints_called), f"Expected call to /events, got {endpoints_called}"

        # Ensure the live_event is among the retrieved odds_events
        target_event = next((e for e in odds_events if e.id == live_event["event_ticker"]), None)
        assert target_event is not None, f"Seeded event {live_event['event_ticker']} not found in fetched events."
        
        # Sync the seeded DB event's commence_time to EXACTLY match target_event
        # to prevent SQLite timezone/microsecond string comparison quirks
        event_db = (await seeded_db.execute(select(Event).where(Event.id == "live_test_event_001"))).scalar_one()
        event_db.commence_time = target_event.commence_time
        await seeded_db.commit()
        
        # Verify standardizing in OddsEvent
        assert target_event.sport_key == "basketball_nba"
        assert len(target_event.bookmakers) == 1
        bk_data = target_event.bookmakers[0]
        assert bk_data.key == "kalshi"
        
        # =========================================================================
        # 2. DATA STANDARDIZATION & DB INSERTION (Ingester)
        # =========================================================================
        # Pass the event through the DataIngester
        api_client_mock = MagicMock(spec=TheOddsAPIClient)
        ingester = DataIngester(api_client_mock)
        
        # We process the target event to test DB mappings
        await ingester._process_odds_data(seeded_db, [target_event])
        
        # Verify Bookmaker, Market, and Odds exist
        all_odds = (await seeded_db.execute(select(Odds).where(Odds.bookmaker_id == bk.id))).scalars().all()
        assert len(all_odds) > 0, "Ingester failed to create Odds rows"
        
        # Verify normalization & format for our specific mapped event
        markets = (await seeded_db.execute(select(Market).where(Market.event_id == event_db.id))).scalars().all()
        assert len(markets) > 0, "Ingester failed to map Markets to the internal Event (fuzzy match failed?)"
        
        market_ids = [m.id for m in markets]
        event_odds = [o for o in all_odds if o.market_id in market_ids]
        assert len(event_odds) > 0, "No odds linked to the specific mapped event"
        
        valid_selections = {"home", "away", "draw", "over", "under"}
        for odd in event_odds:
            assert odd.event_sid == live_event["event_ticker"]
            assert odd.market_sid == live_event["event_ticker"]
            assert odd.sid.startswith(live_event["event_ticker"]) # Kalshi market ticker
            assert odd.normalized_selection in valid_selections
            assert 1.0 < odd.price < 100.0, f"Price {odd.price} should be decimal odds (not cents or < 1)"
        
        # =========================================================================
        # 3. ODDS UPDATE EFFICIENCY (Fast Path)
        # =========================================================================
        # Now that SIDs are stored, obtaining odds for this event should use the batch /markets endpoint
        with patch.object(kalshi, "make_request", wraps=kalshi.make_request) as spy_req_update:
            flat_odds = await kalshi.obtain_odds(league_key="basketball_nba", event_ids=[event_db.id])
            
        assert len(flat_odds) > 0, "Fast-path obtain_odds returned empty list"
        
        # Verify it used the batched /markets endpoint exclusively
        endpoints_called = [str(call.args[1]) for call in spy_req_update.call_args_list]
        assert all("/markets" in ep for ep in endpoints_called), f"Expected only /markets calls, got {endpoints_called}"
        
        # Make sure market batch request occurred exactly once (max BATCH_SIZE=100)
        markets_calls = [ep for ep in endpoints_called if "/markets" in ep]
        assert len(markets_calls) == 1, f"Expected exactly 1 batch call, but got {len(markets_calls)}"
        
        # Verify returned data maps to internal event and possesses properly shaped fields
        for odd in flat_odds:
            assert odd["external_event_id"] == event_db.id
            assert odd["event_sid"] == live_event["event_ticker"]
            assert odd["market_sid"] == live_event["event_ticker"]
            assert odd["sid"].startswith(live_event["event_ticker"])
            assert 1.0 < odd["price"] < 100.0

    async def test_kalshi_batch_fetch_efficiency(
        self, kalshi: KalshiBookmaker, seeded_db: AsyncSession, live_event_pair: list
    ):
        """
        Tests the batch capability of obtain_odds directly.
        Uses two open events, seeds them, ingest initial state, and verify batch limit.
        """
        from app.services.ingester import DataIngester
        api_client_mock = MagicMock(spec=TheOddsAPIClient)
        ingester = DataIngester(api_client_mock)
        
        # 1. Seed the two events into the DB explicitly (acting like prior runs)
        # ----------------------------------------------------------------------
        # Remove the preset one first to just test these two clean
        for ev in live_event_pair:
            # We add them manually so we control their IDs
            event_instance = Event(
                id=f"batch_event_{ev['event_ticker']}",
                sport_key="basketball",
                league_key="basketball_nba",
                commence_time=ev["commence_time"],
                home_team=ev["home_team"],
                away_team=ev["away_team"],
                active=True,
            )
            seeded_db.add(event_instance)
        await seeded_db.commit()

        # 2. Ingest the data via discovery to map to the IDs created above 
        # ----------------------------------------------------------------------
        odds_events = await kalshi.fetch_league_odds("basketball_nba")
        # Filter for only our active pair to prevent testing against the massive league block
        target_event_ids = [ev["event_ticker"] for ev in live_event_pair]
        target_odds_events = [oe for oe in odds_events if oe.id in target_event_ids]
        
        # Sync the seeded DB events diverge by microseconds due to different API calls
        for oe in target_odds_events:
            event_instance = (await seeded_db.execute(select(Event).where(Event.id == f"batch_event_{oe.id}"))).scalar_one()
            event_instance.commence_time = oe.commence_time
        await seeded_db.commit()

        await ingester._process_odds_data(seeded_db, target_odds_events)

        # 3. Query the DB mapping to test the actual fast-sync update path
        # ----------------------------------------------------------------------
        event_ids_for_test = [f"batch_event_{ticker}" for ticker in target_event_ids]
        
        with patch.object(kalshi, "make_request", wraps=kalshi.make_request) as spy:
            flat_odds = await kalshi.obtain_odds(league_key="basketball_nba", event_ids=event_ids_for_test)
            
        # Assert returned odds cover both requested events
        returned_event_ids = {o["external_event_id"] for o in flat_odds}
        for eid in event_ids_for_test:
            assert eid in returned_event_ids, f"Event '{eid}' missing from batch obtain_odds result."

        # Verify only 1 API Call was made specifically to /markets API (It batched the tickers together)
        markets_calls = [
            call for call in spy.call_args_list
            if "/markets" in str(call.args[1] if call.args else "")
        ]
        assert len(markets_calls) == 1, (
            f"Expected exactly 1 batch /markets call for {len(event_ids_for_test)} events, "
            f"got {len(markets_calls)}"
        )
