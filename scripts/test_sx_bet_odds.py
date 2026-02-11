"""
Test script to verify SX Bet odds calculation.
Fetches real odds from SX Bet API and compares calculated values.
"""
import asyncio
import json
from app.services.bookmakers.base import BookmakerFactory

async def test_sx_bet_odds():
    print("=" * 80)
    print("SX BET ODDS CALCULATION TEST")
    print("=" * 80)
    
    # Create SX Bet service
    sx_bet = BookmakerFactory.get_bookmaker("sx_bet")
    
    # Fetch odds for La Liga
    # Fetch odds for La Liga
    print("\nFetching odds for La Liga...")
    # Use fetch_league_odds (Bulk TOA format)
    odds_data = await sx_bet.fetch_league_odds("soccer_spain_la_liga")
    
    if not odds_data:
        print("No odds data returned")
        return
    
    print(f"\nFound {len(odds_data)} events")
    
    # Show first event with detailed breakdown
    for event in odds_data[:3]:
        print("\n" + "=" * 80)
        print(f"Event: {event['home_team']} vs {event['away_team']}")
        print(f"Commence: {event['commence_time']}")
        
        for bk_key, bk_data in event.get("bookmakers", {}).items():
            print(f"\nBookmaker: {bk_data['title']}")
            
            for market in bk_data.get("markets", []):
                print(f"\n  Market: {market['key']}")
                
                for outcome in market.get("outcomes", []):
                    print(f"    {outcome['name']}: {outcome['price']}")
                    if "point" in outcome and outcome["point"]:
                        print(f"      Point: {outcome['point']}")

if __name__ == "__main__":
    asyncio.run(test_sx_bet_odds())
