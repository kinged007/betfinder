
from datetime import timedelta
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Event, Market, Odds, Bookmaker

logger = logging.getLogger(__name__)

class OddsAnalysisService:
    @staticmethod
    async def calculate_benchmark_values(db: AsyncSession):
        """
        This function calculates the benchmark values for each market in the database.
        
        1. Select events where commence_time is in the future.
        2. Identify Pinnacle as the benchmark bookmaker.
        3. For each market:
           - Calculate Margin and Fair (True) Odds from Pinnacle.
           - Propagate these values to other bookmakers in the same market.
        """
        logger.info("Starting Odds Analysis for benchmark values (Pinnacle)...")
        
        # 1. Get Pinnacle Bookmaker ID
        pinnacle_res = await db.execute(select(Bookmaker.id).where(Bookmaker.key == "pinnacle"))
        pinnacle_id = pinnacle_res.scalar_one_or_none()
        
        if not pinnacle_id:
            logger.warning("Pinnacle bookmaker not found in database. Benchmark analysis skipped.")
            return

        # 2. Get Future Events
        now = datetime.now(timezone.utc) - timedelta(hours=2)
        events_res = await db.execute(select(Event).where(Event.commence_time > now))
        events = events_res.scalars().all()
        
        logger.info(f"Analyzing {len(events)} future events.")

        for event in events:
            # 3. For each market in the event
            markets_res = await db.execute(select(Market).where(Market.event_id == event.id))
            markets = markets_res.scalars().all()
            
            for market in markets:
                # 4. Fetch all odds for this market
                odds_res = await db.execute(select(Odds).where(Odds.market_id == market.id))
                market_odds = odds_res.scalars().all()
                if not market_odds:
                    continue

                # 5. Group by Bookmaker
                odds_by_bk = {}
                for odd in market_odds:
                    if odd.bookmaker_id not in odds_by_bk:
                        odds_by_bk[odd.bookmaker_id] = []
                    odds_by_bk[odd.bookmaker_id].append(odd)

                # 6. Process Pinnacle Benchmark
                if pinnacle_id in odds_by_bk:
                    pinnacle_market_odds = odds_by_bk[pinnacle_id]
                    
                    # Calculate Pinnacle Margin and Fair Probabilities
                    # Margin calculation: Sum(1/price) - 1
                    total_implied_prob = sum(1.0 / o.price for o in pinnacle_market_odds)
                    margin = total_implied_prob - 1.0
                    
                    # Fair Probabilities (normalized to 1.0)
                    fair_probs = {} # normalized_selection -> prob
                    for o in pinnacle_market_odds:
                        fair_prob = (1.0 / o.price) / total_implied_prob
                        fair_probs[o.normalized_selection] = fair_prob
                        
                        # Update Pinnacle odds with its own analysis
                        o.margin = margin
                        o.implied_probability = fair_prob
                        o.true_odds = 1.0 / fair_prob
                        db.add(o)

                    # 7. Apply to other bookmakers
                    for bk_id, bk_odds in odds_by_bk.items():
                        if bk_id == pinnacle_id:
                            continue
                        
                        # Calculate this bookmaker's specific margin for info
                        bk_total_prob = sum(1.0 / o.price for o in bk_odds)
                        bk_margin = bk_total_prob - 1.0

                        for o in bk_odds:
                            # Find matching fair probability from benchmark
                            benchmark_prob = fair_probs.get(o.normalized_selection)
                            if benchmark_prob:
                                o.implied_probability = benchmark_prob
                                o.true_odds = 1.0 / benchmark_prob
                                o.margin = bk_margin # Store this bk's margin
                            else:
                                # If no Pinnacle benchmark available, calculate implied probability from own odds
                                if not o.implied_probability:
                                    o.implied_probability = 1.0 / o.price
                                o.margin = bk_margin
                            db.add(o)
                else:
                    # No Pinnacle odds for this market - calculate implied probability for all bookmakers
                    for bk_id, bk_odds in odds_by_bk.items():
                        # Calculate this bookmaker's margin
                        bk_total_prob = sum(1.0 / o.price for o in bk_odds)
                        bk_margin = bk_total_prob - 1.0
                        
                        for o in bk_odds:
                            # Calculate implied probability from the bookmaker's own odds
                            if not o.implied_probability:
                                o.implied_probability = 1.0 / o.price
                            o.margin = bk_margin
                            db.add(o)

        await db.commit()
        logger.info("Odds Analysis completed.")
