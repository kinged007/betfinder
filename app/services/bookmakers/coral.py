
import random
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.enums import BetResult
from app.services.bookmakers.base import APIBookmaker
from app.db.models import Bet, Odds, Market, Event, Bookmaker

class CoralBookmakerSimulator(APIBookmaker):
    name = "coral"
    title = "Coral Simulator"
    base_url = "https://simulated.coral.co.uk" # Fake URL
    requests_per_second = 100.0 # Fast for simulation
    odds_per_second = 100.0
    auth_type = "None"
    
    def __init__(self, key: str, config: Dict[str, Any], db: Optional[Any] = None):
        super().__init__(key, config, db)
        
    async def authorize(self) -> bool:
        """Always return True as requested."""
        return True

    def should_sync_event(self, event_id_str, event_commence_time) -> bool:
        """Always return True as requested."""
        return True

    async def obtain_odds(
        self, 
        sport_key: str, 
        event_ids: List[str], 
        log: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Simulate odds retrieval.
        
        Logic:
        1. Fetch existing odds for these events from other bookmakers (API source).
        2. Apply random variation (-5% to +5%) with a 30% chance.
        """
        try:
            results = []
            if not self.db:
                return results

            # Fetch odds from other bookmakers for the same events to use as base
            # We exclude our own odds to avoid feedback loops if we were persistent, 
            # though here we are generating fresh ones.
            stmt = (
                select(Odds)
                .options(joinedload(Odds.market).joinedload(Market.event))
                .join(Market).join(Event)
                .join(Bookmaker)
                .where(
                    Event.id.in_(event_ids),
                    Bookmaker.key != self.name 
                )
            )
            
            # We might get multiple odds for the same selection from different bookmakers.
            # We'll group them by (event_id, market_key, selection) and pick the first one (or average).
            existing_odds_records = (await self.db.execute(stmt)).scalars().all()
            
            processed_keys = set()

            for odd in existing_odds_records:
                unique_key = (odd.market.event_id, odd.market.key, odd.normalized_selection, odd.point)
                
                if unique_key in processed_keys:
                    continue
                    
                processed_keys.add(unique_key)
                
                # Base price
                current_price = odd.price

                # 30% chance to change the odds
                if random.random() < 0.20:
                    # Change by -5% to +5%
                    change_pct = random.uniform(-0.03, 0.03)
                    new_price = current_price * (1 + change_pct)
                    # Ensure price doesn't go below 1.01
                    new_price = max(1.01, round(new_price, 2))
                else:
                    new_price = current_price

                # Calculate implied probability
                # implied_prob = 1.0 / new_price if new_price > 0 else 0
                
                results.append({
                    "external_event_id": odd.market.event_id,
                    "market_key": odd.market.key,
                    "selection": odd.normalized_selection, # Use normalized selection as selection
                    "price": new_price,
                    # "implied_probability": implied_prob,
                    "point": odd.point,
                    # "bet_limit": 1000.0, # Simulated limit
                    "sid": str(uuid.uuid4()), # Fake ID
                    "market_sid": str(uuid.uuid4()), # Fake ID
                    "event_sid": odd.event_sid or odd.market.event_id
                })

        except Exception as e:
            print(e)
        
        return results

    async def place_bet(self, bet: Bet) -> Dict[str, Any]:
        """
        Simulate placing a bet.
        """
        simulated_id = f"CORAL-SIM-{uuid.uuid4().hex[:8].upper()}"
        
        return {
            "success": True,
            "status": "auto",
            "bet_id": simulated_id,
            "external_id": simulated_id,
            "message": f"Simulated bet placement on Coral. ID: {simulated_id}"
        }

    async def get_order_status(self, external_id: str) -> Dict[str, Any]:
        """
        Simulate order status check.
        Random selection of won/lost/void.
        """
        # Determine a random status
        # We give a higher weight to 'settled' states just to demonstrate capability,
        # but in a real sim maybe we wait for time?
        # User said: "results of placed betscan be a random selection of won/lost/void."
        
        # For this request, I will just return a random final state.
        
        choices = [BetResult.WON.value, BetResult.LOST.value, BetResult.VOID.value]
        roll = random.choice(choices)
        
        payout = 0.0
        # We don't have access to the original stake/price here easily unless we query the bet,
        # but get_bet_settlement in base calls this. 
        # APIBookmaker.get_order_status returns a dict with 'payout'.
        
        # If we can't calculate payout (don't have stake), we leave it 0 or None,
        # and let the base class fallback logic handle it (which uses local bet data).
        # But if we return "won", base class expects payout > 0 or it might calculate it.
        # Base logic: if status=='won' and payout is None/0 -> payout = bet.stake * bet.price.
        # So we can just return payout=0 and status=won, and base handles it.
        
        return {
            "status": roll,
            "payout": 0.0, 
            "external_id": external_id,
            "raw_state": "simulated_finished"
        }
