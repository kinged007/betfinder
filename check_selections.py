import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, func
from app.db.models import Odds

async def check_normalized_selections():
    engine = create_async_engine('sqlite+aiosqlite:///./betfinder.db')
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        # Get distinct normalized_selection values with counts
        result = await session.execute(
            select(Odds.normalized_selection, func.count(Odds.id))
            .group_by(Odds.normalized_selection)
            .order_by(func.count(Odds.id).desc())
        )
        rows = result.all()
        
        print('\n=== Normalized Selection Values in Database ===')
        print(f'Total distinct values: {len(rows)}\n')
        
        for sel, count in rows[:30]:  # Show top 30
            print(f'{sel:30} : {count:5} records')
        
        if len(rows) > 30:
            print(f'\n... and {len(rows) - 30} more values')
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check_normalized_selections())
