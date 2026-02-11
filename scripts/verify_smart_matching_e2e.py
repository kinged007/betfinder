import asyncio
import sys
import os
from sqlalchemy import select, delete

sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.db.models import League, Mapping, Bookmaker
from app.services.ingester import DataIngester
from app.services.the_odds_api import TheOddsAPIClient
from app.core.config import settings

async def main():
    async with AsyncSessionLocal() as db:
        print("Cleaning up previous SX Bet mappings/leagues...")
        
        # 1. Delete Mappings for sx_bet
        await db.execute(delete(Mapping).where(Mapping.source == "sx_bet"))
        
        # 2. Delete Leagues created by sx_bet (sx_bet_*)
        await db.execute(delete(League).where(League.key.like("sx_bet_%")))
        
        await db.commit()
        print("Cleanup complete.")
        
        # 3. Initialize Ingester
        # Using dummy key for testing, but we need real SX Bet fetching
        client = TheOddsAPIClient(api_key=settings.THE_ODDS_API_KEY)
        ingester = DataIngester(api_client=client)
        
        print("Running sync_sports...")
        # This will fetch from TOA (to ensure base leagues exist) and then SX Bet
        # We need to make sure SX Bet bookmaker is ACTIVE in DB
        
        res = await db.execute(select(Bookmaker).where(Bookmaker.key == "sx_bet"))
        sx = res.scalar_one_or_none()
        if not sx or not sx.active:
            print("ERROR: SX Bet bookmaker not active in DB. Please enable it first.")
            return

        await ingester.sync_sports(db)
        
        # 4. Verify Mapping
        print("Verifying mappings...")
        
        # Check specific known case
        # We expect a mapping for "Netherlands - Eredivisie" -> "soccer_netherlands_eredivisie" (or similar)
        
        # Find the correct internal key for Eredivisie
        l_res = await db.execute(select(League).where(League.title.ilike("%Eredivisie%")))
        eredivisie_leagues = l_res.scalars().all()
        print(f"Internal Eredivisie candidates: {[l.key for l in eredivisie_leagues]}")
        
        # Check mappings
        result = await db.execute(select(Mapping).where(Mapping.source == "sx_bet", Mapping.external_name.ilike("%Eredivisie%")))
        mappings = result.scalars().all()
        
        mapped = False
        for m in mappings:
            print(f"Mapping Found: {m.external_name} -> {m.internal_key}")
            if "sister" not in m.internal_key and "PENDING" not in m.internal_key and "sx_bet" not in m.internal_key:
                print("SUCCESS: Auto-mapped to internal key!")
                mapped = True
            elif m.internal_key == "PENDING":
                 print("PARTIAL: Marked as PENDING (Score too low?)")

        # Check PREMIER LEAGUE (Should match English Premier League, NOT A-League)
        # Assuming internal key is 'soccer_epl'
        res_pl = await db.execute(select(Mapping).where(Mapping.source == "sx_bet", Mapping.external_name == "English Premier League"))
        map_pl = res_pl.scalar_one_or_none()
        if map_pl:
            print(f"PL Mapping: {map_pl.internal_key}")
            if "epl" in map_pl.internal_key:
                print("SUCCESS: PL mapped correctly.")
            else:
                print(f"WARNING: PL mapped to {map_pl.internal_key}")
        else:
            print("INFO: PL not found or mapped.")

        # Check CHAMPIONS LEAGUE (Should match UEFA Champions League)
        # Assuming internal key is 'soccer_uefa_champs_league'
        res_cl = await db.execute(select(Mapping).where(Mapping.source == "sx_bet", Mapping.external_name.ilike("%Champions League%UEFA%")))
        map_cl = res_cl.scalar_one_or_none()
        if map_cl:
             print(f"CL Mapping: {map_cl.internal_key}")
             if "uefa_champs_league" in map_cl.internal_key:
                 print("SUCCESS: CL mapped correctly.")
             else:
                 print(f"WARNING: CL mapped to {map_cl.internal_key}")

if __name__ == "__main__":
    asyncio.run(main())
