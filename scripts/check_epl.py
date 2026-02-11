import asyncio
import sys
import os
from sqlalchemy import select

sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.db.models import League

async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(League).where(League.key == 'soccer_epl'))
        l = res.scalar_one_or_none()
        if l:
            print(f"EPL Found: Key='{l.key}', Title='{l.title}'")
        else:
            print("EPL Not Found by key 'soccer_epl'")
            
        # Search by title
        res = await db.execute(select(League).where(League.title.ilike("%Premier League%")))
        matches = res.scalars().all()
        print(f"Matches for 'Premier League': {[m.title + ' (' + m.key + ')' for m in matches]}")

if __name__ == "__main__":
    asyncio.run(main())
