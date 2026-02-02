
import asyncio
import sys
import os
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.ingester import DataIngester
from app.db.models import Odds, League, Bookmaker, Market, Event

async def verify_ingestion():
    # Mock DB Session
    mock_db = MagicMock()
    
    # Mock async methods
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    
    # Mock Repository gets
    # Mock League get
    mock_league = League(key="soccer_epl", sport_key="soccer")
    mock_db.get = AsyncMock()
    mock_db.get.side_effect = lambda model, key: mock_league if model == League else None
    
    # Mock execution results for checks
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None 
    mock_db.execute.return_value = mock_result

    # Mock API Client
    mock_api = MagicMock()
    
    # Sample Data with new fields
    sample_odds_data = [
        {
            "id": "event_123",
            "sport_key": "soccer_epl",
            "commence_time": "2026-01-30T10:00:00Z",
            "home_team": "Team A",
            "away_team": "Team B",
            "bookmakers": [
                {
                    "key": "bookie_1",
                    "title": "Bookie One",
                    "last_update": "2026-01-29T20:00:00Z",
                    "sid": "bk_event_sid_1",
                    "link": "http://bookie.com/event",
                    "markets": [
                        {
                            "key": "h2h",
                            "sid": "bk_market_sid_1",
                            "link": "http://bookie.com/market",
                            "limit": 500.0,
                            "outcomes": [
                                {
                                    "name": "Team A",
                                    "price": 1.5,
                                    "sid": "bk_outcome_sid_1",
                                    "link": "http://bookie.com/outcome",
                                    "limit": 100.0
                                },
                                {
                                    "name": "Team B",
                                    "price": 2.5,
                                    # Missing SID, Link, Limit -> Should fallback
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    ]

    ingester = DataIngester(api_client=mock_api)
    
    # Intercept db.add to captue Odds objects
    added_objects = []
    def capture_add(obj):
        added_objects.append(obj)
    mock_db.add.side_effect = capture_add
    
    # Run processing
    print("Running _process_odds_data...")
    await ingester._process_odds_data(mock_db, sample_odds_data)
    
    # Inspect Odds objects
    print(f"Captured {len(added_objects)} objects sent to db.add()")
    
    odds_objects = [obj for obj in added_objects if isinstance(obj, Odds)]
    print(f"Found {len(odds_objects)} Odds objects.")
    
    for odd in odds_objects:
        print(f"\nSelection: {odd.selection}")
        print(f"  Price: {odd.price}")
        print(f"  Event SID: {odd.event_sid}")
        print(f"  Market SID: {odd.market_sid}")
        print(f"  Outcome SID: {odd.sid}")
        print(f"  Bet Limit: {odd.bet_limit}")
        print(f"  URL: {odd.url}")
        
        if odd.selection == "Team A":
            assert odd.event_sid == "bk_event_sid_1", "Event SID mismatch"
            assert odd.market_sid == "bk_market_sid_1", "Market SID mismatch"
            assert odd.sid == "bk_outcome_sid_1", "Outcome SID mismatch"
            assert odd.bet_limit == 100.0, "Bet Limit mismatch (should be outcome limit)"
            assert odd.url == "http://bookie.com/outcome", "URL mismatch (should be outcome link)"
            print("  -> PASSED Team A checks")
            
        elif odd.selection == "Team B":
            assert odd.event_sid == "bk_event_sid_1", "Event SID mismatch"
            assert odd.market_sid == "bk_market_sid_1", "Market SID mismatch"
            assert odd.sid is None, "Outcome SID should be None"
            assert odd.bet_limit == 500.0, "Bet Limit mismatch (should fallback to market limit)"
            assert odd.url == "http://bookie.com/market", "URL mismatch (should fallback to market link)"
            print("  -> PASSED Team B checks (Fallback logic)")

if __name__ == "__main__":
    asyncio.run(verify_ingestion())
