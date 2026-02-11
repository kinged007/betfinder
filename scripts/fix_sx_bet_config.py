import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.db.models import Bookmaker
from sqlalchemy import select

async def main():
    print("Updating SX Bet configuration...")
    
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Bookmaker).where(Bookmaker.key == "sx_bet"))
        bk = res.scalar_one_or_none()
        
        if not bk:
            print("SX Bet bookmaker not found!")
            return

        print(f"Current Config: {bk.config}")
        
        # Update config
        new_config = bk.config.copy()
        new_config["use_testnet"] = False
        bk.config = new_config
        
        db.add(bk)
        await db.commit()
        await db.refresh(bk)
        
        print(f"Updated Config: {bk.config}")
        print("SX Bet now configured for Mainnet (api.sx.bet).")

if __name__ == "__main__":
    asyncio.run(main())
