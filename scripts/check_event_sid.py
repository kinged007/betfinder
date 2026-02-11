import asyncio
from app.db.session import get_db
from app.db.models import Odds, Bookmaker
from sqlalchemy import select

async def check_event_sid():
    async for db in get_db():
        # Get SX Bet bookmaker ID
        result = await db.execute(select(Bookmaker).where(Bookmaker.key == "sx_bet"))
        sx_bet = result.scalar_one_or_none()
        
        if not sx_bet:
            print("SX Bet bookmaker not found")
            return
        
        print(f"SX Bet bookmaker ID: {sx_bet.id}")
        
        # Get odds for SX Bet
        result = await db.execute(
            select(Odds.id, Odds.event_sid, Odds.market_sid, Odds.sid, Odds.bookmaker_id)
            .where(Odds.bookmaker_id == sx_bet.id)
            .limit(10)
        )
        odds = result.all()
        
        print(f"\nFound {len(odds)} SX Bet odds records:")
        for odd in odds:
            print(f"  Odds ID: {odd.id}, event_sid: {odd.event_sid}, market_sid: {odd.market_sid}, sid: {odd.sid}")
        
        break

asyncio.run(check_event_sid())
