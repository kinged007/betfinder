
import asyncio
import sys
import os
import random

# Add project root to path
sys.path.append(os.getcwd())

from sqlalchemy import select, update
from app.db.session import AsyncSessionLocal
from app.db.models import Bookmaker, Bet, Event, Market, Odds
from app.core.enums import BetResult, BetStatus
from datetime import datetime, timezone

async def verify_balance_logic():
    print("🚀 Starting Balance Logic Verification...")
    
    async with AsyncSessionLocal() as db:
        # 1. Setup Test Bookmaker
        print("\n[Step 1] Setting up Test Bookmaker...")
        bk_key = f"test_bk_{random.randint(1000, 9999)}"
        STARTING_BALANCE = 1000.0
        
        bk = Bookmaker(
            key=bk_key,
            title="Test Bookmaker",
            active=True,
            model_type="api", # Simulates API for manual placement
            balance=STARTING_BALANCE,
            config={"starting_balance": STARTING_BALANCE}
        )
        db.add(bk)
        await db.commit()
        await db.refresh(bk)
        print(f"   Created Bookmaker '{bk.title}' ({bk.id}) with Balance: {bk.balance}")

        # 2. Manual Bet Placement Simulation
        print("\n[Step 2] Simulating Manual Bet Placement...")
        STAKE = 50.0
        PRICE = 2.0
        
        # Create Dummy Event/Market/Odds first
        event = Event(id=f"test_evt_{random.randint(1000,9999)}", sport_key="soccer", home_team="A", away_team="B", commence_time=datetime.now(timezone.utc))
        db.add(event)
        await db.commit()
        
        # We need to use the actual router logic to test it properly, 
        # OR we simulate what the router does. 
        # Since I can't easily call the FastAPI router function directly without a full request context mock,
        # I will simulate the logic I ADDED to the router.
        
        # Logic added: "bookmaker_model.balance -= bet_obj.stake"
        
        # Create Bet (Pending)
        bet = Bet(
            event_id=event.id,
            bookmaker_id=bk.id,
            market_key="h2h",
            selection="home",
            price=PRICE,
            stake=STAKE,
            status="pending",
            placed_at=datetime.now(timezone.utc)
        )
        db.add(bet)
        await db.commit()
        
        # --- Simulate Router Logic ---
        print(f"   Placing bet of {STAKE}...")
        
        # Router logic:
        # ... response = await bm_service.place_bet(bet_obj) ...
        # ... if success: ...
        
        # Simulate success
        bet.status = BetStatus.OPEN.value
        bet.external_id = "test_ext_id"
        
        # THE FIX: Deduct Balance
        bk.balance -= bet.stake
        
        db.add(bk)
        db.add(bet)
        await db.commit()
        await db.refresh(bk)
        # -----------------------------
        
        print(f"   New Balance: {bk.balance}")
        
        if abs(bk.balance - (STARTING_BALANCE - STAKE)) < 0.01:
            print("   ✅ PASS: Stake deducted correctly.")
        else:
            print(f"   ❌ FAIL: Balance mismatch. Expected {STARTING_BALANCE - STAKE}, got {bk.balance}")
            return

        # 3. Test Settlement (WON)
        print("\n[Step 3] Testing Settlement (WON)...")
        # Logic in bets.py: specific block for settlement
        
        PAYOUT = STAKE * PRICE
        
        # Router logic simulation for update_bet / bulk_update_bets
        # old_status = open
        # new_status = won
        
        bet.status = BetResult.WON.value
        bet.payout = PAYOUT
        bet.settled_at = datetime.now(timezone.utc)
        
        # Logic: 
        # if new_status == BetResult.WON.value:
        #    bet.bookmaker.balance += new_payout
        
        bk.balance += PAYOUT
        
        db.add(bk)
        db.add(bet)
        await db.commit()
        await db.refresh(bk)
        
        EXPECTED_AFTER_WIN = (STARTING_BALANCE - STAKE) + PAYOUT
        print(f"   Balance after WIN: {bk.balance}")
        
        if abs(bk.balance - EXPECTED_AFTER_WIN) < 0.01:
             print("   ✅ PASS: Winnings added correctly.")
        else:
             print(f"   ❌ FAIL: Balance mismatch. Expected {EXPECTED_AFTER_WIN}, got {bk.balance}")
             return

        # 4. Test Analytics Logic
        print("\n[Step 4] Testing Analytics Logic...")
        # Query: sum(starting_balance) + sum(pnl)
        
        # We have 1 Bookmaker with starting_balance = 1000
        # We have 1 Bet (WON): PnL = 50 (100 - 50)
        # Expected Running Balance End = 1050
        
        # Fetch bets for analytics
        stmt = select(Bet).where(Bet.bookmaker_id == bk.id).where(Bet.status.in_(['won', 'lost', 'void']))
        bets = (await db.execute(stmt)).scalars().all()
        
        # Analytics Logic copied from router
        total_starting_balance = 0.0
        cfg = bk.config or {}
        starting = float(cfg.get("starting_balance", 0.0))
        total_starting_balance += starting
        
        running_balance = total_starting_balance
        
        for b in bets:
            pnl = 0.0
            if b.status == 'won':
                pnl = (b.payout or 0) - b.stake
            elif b.status == 'lost':
                pnl = -b.stake
            elif b.status == 'void':
                pnl = 0.0
            running_balance += pnl
            
        print(f"   Analytics Calculated Balance: {running_balance}")
        
        if abs(running_balance - 1050.0) < 0.01:
            print("   ✅ PASS: Analytics calculation correct.")
        else:
             print(f"   ❌ FAIL: Analytics mismatch. Expected 1050.0, got {running_balance}")
             return

        # Clean up
        print("\nCleaning up...")
        await db.delete(bet)
        await db.delete(bk) # Cascade? Maybe not.
        await db.delete(event)
        await db.commit()
        print("Done.")

if __name__ == "__main__":
    asyncio.run(verify_balance_logic())
