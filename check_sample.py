import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.db.models import Odds, Market, Event

async def check_sample_odds():
    engine = create_async_engine('sqlite+aiosqlite:///./betfinder.db')
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        # Get sample odds with market info
        result = await session.execute(
            select(Odds, Market, Event)
            .join(Odds.market)
            .join(Market.event)
            .limit(20)
        )
        rows = result.all()
        
        print('\n=== Sample Odds Records ===\n')
        print(f'{"Market":<10} {"Selection":<25} {"Normalized":<25} {"Home Team":<20} {"Away Team":<20}')
        print('-' * 110)
        
        for odd, market, event in rows:
            print(f'{market.key:<10} {odd.selection:<25} {odd.normalized_selection:<25} {event.home_team:<20} {event.away_team:<20}')
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check_sample_odds())
