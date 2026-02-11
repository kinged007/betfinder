
import asyncio
import sys
import os
from sqlalchemy import select, and_

# Add project root to path
sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.services.bookmakers.base import BookmakerFactory
from app.db.models import Bookmaker, Event, Odds, Market

async def main():
    print("Verifying SX Bet ID Mapping in obtain_odds...")
    
    async with AsyncSessionLocal() as db:
        # 1. Get SX Bet config
        res = await db.execute(select(Bookmaker).where(Bookmaker.key == "sx_bet"))
        bk = res.scalar_one_or_none()
        if not bk:
            print("SX Bet not found in DB")
            return

        # 2. Find an Event that has SX Bet odds (so we know it has a mapping)
        # We look for Odds where bookmaker is sx_bet and event_sid is not null
        stmt = (
            select(Event.id, Odds.event_sid, Event.league_key)
            .select_from(Event)
            .join(Market, Market.event_id == Event.id)
            .join(Odds, Odds.market_id == Market.id)
            .where(
                Odds.bookmaker_id == bk.id,
                Odds.event_sid.isnot(None),
                Event.league_key.isnot(None)
            )
            .limit(1)
        )
        res = await db.execute(stmt)
        row = res.first()
        
        if not row:
            print("No existing SX Bet odds found in DB to test with. Run ingestion first?")
            return
            
        params = {
            "uuid": str(row.id),
            "sx_id": row.event_sid,
            "league": row.league_key
        }
        print(f"Testing with Event UUID: {params['uuid']}")
        print(f"Expected SX ID: {params['sx_id']}")
        print(f"League: {params['league']}")
        
        # 3. Instantiate Service
        sx_service = BookmakerFactory.get_bookmaker("sx_bet", bk.config, db)
        
        # 4. Call obtain_odds with UUID
        print(f"\nCalling obtain_odds('{params['league']}', event_ids=['{params['uuid']}'])...")
        odds = await sx_service.obtain_odds(params['league'], event_ids=[params['uuid']])
        
        print(f"Result count: {len(odds)}")
        if odds:
            first = odds[0]
            print(f"First entry external_event_id: {first.get('external_event_id')}")
            print(f"Match? {first.get('external_event_id') == params['uuid']}")
            
            if first.get('external_event_id') == params['uuid']:
                print("SUCCESS: obtain_odds returned correct UUID!")
            else:
                print(f"FAILURE: Expected {params['uuid']}, got {first.get('external_event_id')}")
        else:
            print("No odds returned. (Might be no active markets, or mapping failed silently)")

if __name__ == "__main__":
    asyncio.run(main())
