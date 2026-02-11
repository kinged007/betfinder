import asyncio
import sys
import os

sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.services.ingester import DataIngester
from app.services.the_odds_api import TheOddsAPIClient
from app.db.models import League, Mapping, Sport
from sqlalchemy import select, delete

async def main():
    print("Testing Smart Matching Logic...")
    async with AsyncSessionLocal() as db:
        # 1. Setup Mock Data
        # Create 'basketball_nba' league (Generic TOA)
        # Create 'SX Bet NBA' league data (External)
        
        # Ensure Sport
        sport_key = "basketball_nba" # generic group for matching?
        # Actually group is "Basketball" usually.
        # TOA key: basketball_nba, group: Basketball, title: NBA
        
        # Clean up test data
        await db.execute(delete(Mapping).where(Mapping.source == "sx_bet"))
        await db.commit()
        
        # Ensure generic NBA exists
        res = await db.execute(select(League).where(League.key == "basketball_nba"))
        nba = res.scalar_one_or_none()
        if not nba:
            print("Creating generic 'basketball_nba' league for test...")
            # Need ID for Sport first
            await db.merge(Sport(key="basketball", title="Basketball", group="Basketball", active=True))
            nba = League(key="basketball_nba", title="NBA", group="Basketball", active=True, sport_key="basketball")
            db.add(nba)
            await db.commit()
        
        # 2. Instantiate Ingester
        # mocking client as we won't call API
        ingester = DataIngester(api_client=TheOddsAPIClient())
        
        # 3. Simulate SX Bet Data (which uses "NBA" title, matching generic)
        sx_data_match = [{
            "key": "sx_bet_1", # SX Key
            "group": "Basketball",
            "title": "NBA", # Exact match title
            "active": True,
            "has_outrights": False,
            "details": {"league_id": "1"}
        }]
        
        print("\nProcessing SX Bet data (Expected Match)...")
        await ingester._process_sports_data(db, sx_data_match, source="sx_bet")
        
        # Check Mapping
        res = await db.execute(select(Mapping).where(Mapping.external_key == "1", Mapping.source == "sx_bet"))
        mapping = res.scalar_one_or_none()
        
        if mapping:
            print(f"SUCCESS: Mapping created! {mapping.external_key} -> {mapping.internal_key}")
            if mapping.internal_key == "basketball_nba":
                print("  -> Correctly mapped to generic NBA.")
            else:
                print(f"  -> FAILED: Mapped to wrong key {mapping.internal_key}")
        else:
            print("FAILED: No mapping created for NBA match.")
            
        # 4. Simulate SX Bet Data (Unmatched)
        sx_data_unknown = [{
            "key": "sx_bet_999",
            "group": "Basketball",
            "title": "Alien Basketball League",
            "active": True,
            "has_outrights": False, 
            "details": {"league_id": "999"}
        }]
        
        print("\nProcessing SX Bet data (Expected No Match)...")
        await ingester._process_sports_data(db, sx_data_unknown, source="sx_bet")

        # Check Mapping for PENDING
        res = await db.execute(select(Mapping).where(Mapping.external_key == "999", Mapping.source == "sx_bet"))
        mapping_unk = res.scalar_one_or_none()
        
        if mapping_unk:
             print(f"SUCCESS: Mapping created! {mapping_unk.external_key} -> {mapping_unk.internal_key}")
             if mapping_unk.internal_key == "PENDING":
                 print("  -> Correctly marked as PENDING.")
             else:
                 print(f"  -> FAILED: Should be PENDING but got {mapping_unk.internal_key}")
        else:
             print("FAILED: No mapping created for unknown league.")

        # Verify League NOT created
        res = await db.execute(select(League).where(League.title == "Alien Basketball League"))
        alien_league = res.scalar_one_or_none()
        if not alien_league:
            print("SUCCESS: Alien League NOT created in League table.")
        else:
            print(f"FAILED: Alien League washer created! Key: {alien_league.key}")

if __name__ == "__main__":
    asyncio.run(main())
