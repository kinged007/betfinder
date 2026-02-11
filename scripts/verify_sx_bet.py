import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.services.bookmakers.base import BookmakerFactory
from app.db.models import Bookmaker
from sqlalchemy import select

async def main():
    print("Starting verification of SXBetBookmaker... VERSION 2")
    
    async with AsyncSessionLocal() as db:
        # 1. Ensure Bookmaker exists in DB
        res = await db.execute(select(Bookmaker).where(Bookmaker.key == "sx_bet"))
        bk_model = res.scalar_one_or_none()
        
        if not bk_model:
            print("Creating SX Bet Bookmaker entry in DB...")
            bk_model = Bookmaker(
                key="sx_bet",
                title="SX.Bet",
                model_type="api",
                active=True,
                config={"use_testnet": False, "currency": "USDC"}
            )
            db.add(bk_model)
            await db.commit()
            await db.refresh(bk_model)
        else:
            print("SX Bet Bookmaker already exists in DB.")
            if not bk_model.active:
                print("activating...")
                bk_model.active = True
                await db.commit()

        # 2. Instantiate from Factory
        try:
            sx_service = BookmakerFactory.get_bookmaker("sx_bet", bk_model.config, db)
            print(f"Instantiated: {sx_service.title}")
        except Exception as e:
            print(f"Failed to instantiate: {e}")
            return

        # 3. Test Connection (Fetch Sports)
        print("\nTesting Connection (obtain_sports)...")
        sports = await sx_service.obtain_sports()
        print(f"Fetched {len(sports)} sports/leagues.")
        
        if sports:
            print(f"Sample: {sports[0]}")
            
            # 4. Test Fetch Odds (using first league)
            # Try to find a league with events?
            # Let's try a few
            for league in sports[:5]:
                print(f"Checking {league['key']} ({league['title']})...")
                events = await sx_service.fetch_events(league['key'])
                if events:
                    print(f"Found {len(events)} events in {league['title']}.")
                    
                    # DEBUG: Raw API Check
                    league_id = league['details']['league_id']
                    # Use str(league_id) to be safe
                    league_id = str(league_id)
                    
                    print(f"DEBUG: Fetching markets for league {league_id}...")
                    res_m = await sx_service.make_request("GET", "/markets/active", params={"leagueId": league_id, "onlyMainLine": "true"})
                    markets = res_m.json().get("data", {}).get("markets", [])
                    print(f"DEBUG: Found {len(markets)} active main-line markets.")
                    
                    if markets:
                        print(f"DEBUG: Sample Market Keys: {list(markets[0].keys())}")
                        print(f"DEBUG: Sample Market Object: {markets[0]}")
                        
                        print(f"DEBUG: Fetching odds for league {league_id}...")
                        res_o = await sx_service.make_request("GET", "/orders/odds/best", params={
                            "leagueIds": league_id, # Trying singular and plural just in case
                            "baseToken": sx_service.base_token
                        })
                        odds_data = res_o.json().get("data", {}).get("bestOdds", [])
                        print(f"DEBUG: Found {len(odds_data)} odds entries.")
                        if odds_data:
                            print(f"Sample Odds keys: {list(odds_data[0].keys())}")
                            print(f"Sample Odds: {odds_data[0]}")
                    
                    # Now fetch odds via method (Bulk)
                    print(f"Testing fetch_league_odds (Bulk)...")
                    odds = await sx_service.fetch_league_odds(league['key'])
                    print(f"Fetched generic odds data for {league['title']}: {len(odds)} active events with odds.")
                    
                    if odds:
                        print(f"Sample Event: {odds[0]['id']} - {odds[0]['home_team']} vs {odds[0]['away_team']}")
                        
                        # Test obtain_odds (Flat/Updates)
                        print(f"Testing obtain_odds (Flat Update for single event)...")
                        ev_id = odds[0]['id']
                        flat_odds = await sx_service.obtain_odds(league['key'], event_ids=[ev_id])
                        print(f"Fetched flat odds for event {ev_id}: {len(flat_odds)} entries.")
                        if flat_odds:
                             print(f"Sample Flat Odd: {flat_odds[0]}")
                        else:
                             print("No flat odds returned (maybe filtering issue?)")
                            
                        break
            else:
                print("No active events found in first 5 leagues.")

        else:
            print("No sports fetched. Check connection/API.")

if __name__ == "__main__":
    asyncio.run(main())
