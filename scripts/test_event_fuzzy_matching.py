"""
Test script to verify event fuzzy matching prevents duplicates.
This script will:
1. Clear existing events and odds
2. Sync TheOddsAPI data (creates baseline events)
3. Sync SX Bet data
4. Verify no duplicate events were created
5. Verify event_sid is populated in Odds table
"""
import asyncio
from app.db.session import get_db
from app.db.models import Event, Odds, Bookmaker, League
from app.services.ingester import DataIngester
from sqlalchemy import select, func

async def test_event_fuzzy_matching():
    async for db in get_db():
        print("=" * 80)
        print("EVENT FUZZY MATCHING TEST")
        print("=" * 80)
        
        # Get league to test with
        result = await db.execute(select(League).where(League.key == "soccer_spain_la_liga"))
        league = result.scalar_one_or_none()
        
        if not league:
            print("[X] La Liga league not found. Run sports sync first.")
            return
        
        print(f"\n[OK] Testing with league: {league.title} ({league.key})")
        
        # Count events before
        result = await db.execute(
            select(func.count(Event.id)).where(Event.league_key == league.key)
        )
        events_before = result.scalar()
        print(f"\n[STATS] Events before sync: {events_before}")
        
        # Get bookmakers
        result = await db.execute(select(Bookmaker).where(Bookmaker.key.in_(["theoddsapi", "sx_bet"])))
        bookmakers = {bk.key: bk for bk in result.scalars().all()}
        
        print(f"\n[INFO] Bookmakers found:")
        for key, bk in bookmakers.items():
            print(f"  - {key}: {bk.title}")
        
        # Initialize ingester
        from app.services.the_odds_api import TheOddsAPIClient
        api_client = TheOddsAPIClient()
        ingester = DataIngester(api_client=api_client)
        
        # Sync TheOddsAPI first (creates baseline)
        print(f"\n[SYNC] Syncing TheOddsAPI for {league.key}...")
        try:
            odds_data = await ingester.api_client.get_odds(
                sport=league.key,
                regions="us,uk,eu",
                markets="h2h",
                odds_format="decimal"
            )
            if odds_data:
                await ingester._process_odds_data(db, odds_data)
                await db.commit()
                print(f"[OK] Processed {len(odds_data)} events from TheOddsAPI")
        except Exception as e:
            print(f"[WARN] TheOddsAPI sync failed: {e}")
        
        # Count events after TheOddsAPI
        result = await db.execute(
            select(func.count(Event.id)).where(Event.league_key == league.key)
        )
        events_after_toa = result.scalar()
        print(f"[STATS] Events after TheOddsAPI: {events_after_toa}")
        
        # Sync SX Bet
        if "sx_bet" in bookmakers:
            print(f"\n[SYNC] Syncing SX Bet for {league.key}...")
            try:
                from app.services.bookmakers.base import BookmakerFactory
                sx_bet_service = BookmakerFactory.get_bookmaker("sx_bet")
                odds_data = await sx_bet_service.fetch_league_odds(league.key)
                if odds_data:
                    await ingester._process_odds_data(db, odds_data)
                    await db.commit()
                    print(f"[OK] Processed {len(odds_data)} events from SX Bet")
                else:
                    print("[WARN] No odds data from SX Bet")
            except Exception as e:
                print(f"[WARN] SX Bet sync failed: {e}")
        
        # Count events after SX Bet
        result = await db.execute(
            select(func.count(Event.id)).where(Event.league_key == league.key)
        )
        events_after_sx = result.scalar()
        print(f"[STATS] Events after SX Bet: {events_after_sx}")
        
        # Check for duplicates
        duplicates_created = events_after_sx - events_after_toa
        if duplicates_created > 0:
            print(f"\n[FAIL] {duplicates_created} duplicate events were created!")
        else:
            print(f"\n[SUCCESS] No duplicate events created!")
        
        # Check event_sid population
        if "sx_bet" in bookmakers:
            result = await db.execute(
                select(Odds.event_sid, func.count(Odds.id))
                .where(Odds.bookmaker_id == bookmakers["sx_bet"].id)
                .group_by(Odds.event_sid)
            )
            sid_stats = result.all()
            
            null_count = sum(count for sid, count in sid_stats if sid is None)
            populated_count = sum(count for sid, count in sid_stats if sid is not None)
            
            print(f"\n[STATS] SX Bet Odds event_sid stats:")
            print(f"  - Populated: {populated_count}")
            print(f"  - NULL: {null_count}")
            
            if null_count > 0:
                print(f"  [FAIL] event_sid should be populated for all SX Bet odds")
            else:
                print(f"  [SUCCESS] All SX Bet odds have event_sid populated")
        
        # Show sample events
        print(f"\n[INFO] Sample events:")
        result = await db.execute(
            select(Event)
            .where(Event.league_key == league.key)
            .limit(5)
        )
        for event in result.scalars().all():
            print(f"  - {event.id[:12]}... | {event.home_team} vs {event.away_team} | {event.commence_time}")
        
        print("\n" + "=" * 80)
        break

if __name__ == "__main__":
    asyncio.run(test_event_fuzzy_matching())
