"""
Auto-Trade Service

Handles automated bet placement for presets with auto_trade enabled.
"""

import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from datetime import datetime, timezone, timedelta

from app.db.models import Preset, Bet, Bookmaker, PresetHiddenItem
from app.services.analytics.trade_finder import TradeFinderService, TradeOpportunity
from app.services.bookmakers.base import APIBookmaker, BookmakerFactory
from app.services.notifications.manager import NotificationManager
from app.services.stake_calculator import StakeCalculator

logger = logging.getLogger(__name__)


class AutoTradeService:
    """Service for automated trading based on preset configurations."""
    
    @staticmethod
    async def execute_auto_trades(db: AsyncSession) -> Dict[str, Any]:
        """
        Execute auto-trades for all presets with auto_trade enabled.
        
        Returns:
            Dict with execution statistics
        """
        logger.info("Starting auto-trade execution...")
        
        # Get all active presets with auto_trade enabled
        result = await db.execute(
            select(Preset).where(
                Preset.active == True,
                Preset.auto_trade == True
            )
        )
        presets = result.scalars().all()
        
        if not presets:
            logger.info("No presets with auto-trade enabled.")
            return {"presets_processed": 0, "bets_placed": 0, "errors": 0}
        
        logger.info(f"Found {len(presets)} preset(s) with auto-trade enabled.")
        
        stats = {
            "presets_processed": 0,
            "bets_placed": 0,
            "errors": 0,
            "details": []
        }
        
        # Track bookmakers used in this entire cycle execution
        cycle_used_bookmakers = set()
        
        for preset in presets:
            try:
                preset_stats = await AutoTradeService._process_preset(db, preset, cycle_used_bookmakers)
                stats["presets_processed"] += 1
                stats["bets_placed"] += preset_stats["bets_placed"]
                stats["errors"] += preset_stats["errors"]
                stats["details"].append({
                    "preset_id": preset.id,
                    "preset_name": preset.name,
                    **preset_stats
                })
            except Exception as e:
                logger.error(f"Error processing preset {preset.id} ({preset.name}): {e}", exc_info=True)
                stats["errors"] += 1
        
        logger.info(f"Auto-trade execution completed. Bets placed: {stats['bets_placed']}, Errors: {stats['errors']}")
        return stats
    
    @staticmethod
    async def _process_preset(
        db: AsyncSession, 
        preset: Preset, 
        cycle_used_bookmakers: set = None
    ) -> Dict[str, Any]:
        """
        Process a single preset for auto-trading.
        
        Args:
            db: Database session
            preset: Preset to process
            cycle_used_bookmakers: Set of bookmaker IDs already used in this cycle
            
        Returns:
            Dict with execution statistics for this preset
        """
        logger.info(f"Processing preset: {preset.name} (ID: {preset.id})")
        
        stats = {
            "bets_placed": 0,
            "errors": 0,
            "opportunities_found": 0
        }
        
        if cycle_used_bookmakers is None:
            cycle_used_bookmakers = set()
        
        trade_finder = TradeFinderService()
        
        # Keep looping until no more opportunities or error
        while True:
            #Scan for opportunities (API bookmakers only for auto-trading)
            opportunities = await trade_finder.scan_opportunities(db, preset.id, api_only=True)
            
            if not opportunities:
                logger.info(f"No opportunities found for preset {preset.name}")
                break
            
            # Sort opportunities according to preset's sort configuration
            opportunities = AutoTradeService._sort_opportunities(opportunities, preset)
            
            stats["opportunities_found"] = len(opportunities)
            logger.info(f"Found {len(opportunities)} opportunities for preset {preset.name}")
            
            # Iterate through opportunities to find one we can place
            bet_placed_in_scan = False
            
            for opportunity in opportunities:
                # SKIP if bookmaker already used in this cycle
                if opportunity.bookmaker.id in cycle_used_bookmakers:
                    logger.debug(f"Skipping opportunity for {opportunity.bookmaker.title} (ID: {opportunity.bookmaker.id}) - Bookmaker already used in this cycle")
                    continue
                
                try:
                    bet_placed = await AutoTradeService._place_bet_for_opportunity(
                        db, preset, opportunity
                    )
                    
                    if bet_placed:
                        stats["bets_placed"] += 1
                        logger.info(f"Bet placed successfully for {opportunity.event.home_team} vs {opportunity.event.away_team}")
                        
                        # Mark bookmaker as used for this cycle
                        cycle_used_bookmakers.add(opportunity.bookmaker.id)
                        
                        bet_placed_in_scan = True
                        # Break inner loop to re-scan (refreshing list with hidden items removed)
                        break 
                    else:
                        # Continue to next opportunity if this one failed/skipped
                        continue
                        
                except Exception as e:
                    logger.error(f"Error placing bet: {e}", exc_info=True)
                    stats["errors"] += 1
                    # Continue to next opportunity
            
            # If we went through all opportunities in this scan and placed nothing, stop scanning
            if not bet_placed_in_scan:
                logger.info(f"Finished processing opportunities for preset {preset.name}")
                break
        
        return stats
    
    @staticmethod
    def _sort_opportunities(
        opportunities: List[TradeOpportunity],
        preset: Preset
    ) -> List[TradeOpportunity]:
        """
        Sort opportunities based on the preset's sort configuration.
        
        Reads sort_by and sort_order from preset.other_config.
        Defaults to edge descending if not configured.
        """
        config = preset.other_config or {}
        sort_by = config.get("sort_by", "edge")
        sort_order = config.get("sort_order", "desc")
        reverse = sort_order != "asc"
        
        # Map sort_by values to accessor functions
        sort_key_map = {
            "edge": lambda o: o.edge,
            "start_time": lambda o: o.event.commence_time,
            "price": lambda o: o.odd.price,
            "implied_probability": lambda o: o.odd.implied_probability,
            "home": lambda o: (o.event.home_team or "").lower(),
        }
        
        key_fn = sort_key_map.get(sort_by, sort_key_map["edge"])
        
        # Wrap key function to handle None values (push them to the end)
        def safe_key(o):
            val = key_fn(o)
            if val is None:
                # Use a sentinel that sorts after everything regardless of direction
                return (1, 0)  # (is_none=True, value=0)
            return (0, val)    # (is_none=False, actual_value)
        
        sorted_opps = sorted(opportunities, key=safe_key, reverse=reverse)
        
        logger.debug(
            f"Sorted {len(opportunities)} opportunities by '{sort_by}' "
            f"({'descending' if reverse else 'ascending'})"
        )
        
        return sorted_opps
    
    @staticmethod
    async def _place_bet_for_opportunity(
        db: AsyncSession,
        preset: Preset,
        opportunity: TradeOpportunity
    ) -> bool:
        """
        Attempt to place a bet for a given opportunity.
        
        Args:
            db: Database session
            preset: Preset configuration
            opportunity: Trade opportunity
            
        Returns:
            True if bet was placed successfully, False otherwise
        """
        bookmaker = opportunity.bookmaker
        
        logger.info(
            f"Attempting to place bet for opportunity: "
            f"{opportunity.event.home_team} vs {opportunity.event.away_team}, "
            f"Bookmaker: {bookmaker.title} ({bookmaker.key}), "
            f"Market: {opportunity.market.key}, "
            f"Selection: {opportunity.odd.selection} @ {opportunity.odd.price}"
        )
        
        # Check if bookmaker is an API bookmaker (has place_bet capability)
        if bookmaker.model_type != "api":
            logger.info(f"❌ Bookmaker {bookmaker.key} is not an API bookmaker (model_type={bookmaker.model_type}), skipping")
            return False
        
        logger.debug(f"✓ Bookmaker {bookmaker.key} is an API bookmaker")
        
        # Get bookmaker instance from registry
        bookmaker_instance = BookmakerFactory.get_bookmaker(bookmaker.key, bookmaker.config or {}, db)
        if not bookmaker_instance or not isinstance(bookmaker_instance, APIBookmaker):
            logger.warning(f"❌ Bookmaker {bookmaker.key} not found in registry or not an API bookmaker")
            return False
        
        logger.debug(f"✓ Bookmaker instance created successfully")
        
        # Check if bookmaker has valid credentials
        if not bookmaker_instance.has_credentials():
            logger.info(f"❌ Bookmaker {bookmaker.key} has no credentials configured, skipping")
            return False
        
        logger.debug(f"✓ Bookmaker has valid credentials")
        
        # Get staking configuration from other_config
        other_config = preset.other_config or {}
        staking_strategy = other_config.get('staking_strategy', 'fixed')
        percent_risk = other_config.get('percent_risk')
        kelly_multiplier = other_config.get('kelly_multiplier')
        max_stake = other_config.get('max_stake')
        max_stake = other_config.get('max_stake')
        simulate = other_config.get('simulate', False)
        
        # Determine effective bankroll
        effective_bankroll = bookmaker.balance
        if simulate:
            effective_bankroll = float(other_config.get('simulated_bankroll', 1000))
            logger.info(f"🎭 Using simulated bankroll: {effective_bankroll} EUR")
        
        # Determine probability for calculation (prefer true probability from benchmark)
        # Kelly requires the TRUE win probability. 
        # If true_odds available, use 1/true_odds.
        # Otherwise fallback to 1/price (which typically yields 0 Edge -> 0 Stake).
        calc_probability = 1.0 / opportunity.odd.price
        if opportunity.odd.true_odds and opportunity.odd.true_odds > 0:
            calc_probability = 1.0 / opportunity.odd.true_odds
            
        # Calculate stake using staking strategy
        stake = StakeCalculator.calculate_stake(
            strategy=staking_strategy,
            default_stake=preset.default_stake or 10.0,
            bankroll=effective_bankroll,
            probability=calc_probability,
            odds=opportunity.odd.price,
            percent_risk=percent_risk,
            kelly_multiplier=kelly_multiplier,
            max_stake=max_stake
        )
        
        logger.info(f"Calculated stake: {stake:.2f} EUR using strategy '{staking_strategy}'")
        
        # Check minimum stake requirement (0.1 EUR)
        if stake < 0.1:
            logger.info(f"❌ Calculated stake {stake:.2f} is below minimum threshold of 0.1. Skipping bet.")
            return False
        
        # Check available balance (skip if simulated trade)
        if not simulate:
            if bookmaker.balance < stake:
                logger.warning(
                    f"❌ Insufficient balance for {bookmaker.key}. "
                    f"Required: {stake}, Available: {bookmaker.balance}"
                )
                return False
            
            logger.debug(f"✓ Sufficient balance available ({bookmaker.balance} >= {stake})")
        else:
            logger.info(f"🎭 Simulated trade - skipping balance check")
        
        # Create snapshots
        event_snapshot = {
            "id": opportunity.event.id,
            "sport_key": opportunity.event.sport_key,
            "league_key": opportunity.event.league_key,
            "commence_time": opportunity.event.commence_time.isoformat() if opportunity.event.commence_time else None,
            "home_team": opportunity.event.home_team,
            "away_team": opportunity.event.away_team
        }
        
        market_snapshot = {
            "key": opportunity.market.key,
            "event_id": opportunity.market.event_id
        }
        
        odd_snapshot = {
            "selection": opportunity.odd.selection,
            "normalized_selection": opportunity.odd.normalized_selection,
            "price": opportunity.odd.price,
            "point": opportunity.odd.point,
            "url": opportunity.odd.url,
            "event_sid": opportunity.odd.event_sid,
            "market_sid": opportunity.odd.market_sid,
            "sid": opportunity.odd.sid,
            # Benchmark data
            "implied_probability": opportunity.odd.implied_probability,
            "true_odds": opportunity.odd.true_odds,
            "edge": opportunity.edge 
        }
        
        # Create bet object
        bet = Bet(
            event_id=opportunity.event.id,
            bookmaker_id=bookmaker.id,
            market_key=opportunity.market.key,
            selection=opportunity.odd.normalized_selection,
            price=opportunity.odd.price,
            stake=stake,
            status="pending",
            placed_at=datetime.now(timezone.utc),
            event_data=event_snapshot,
            market_data=market_snapshot,
            odd_data=odd_snapshot,
            preset_id=preset.id
        )
        
        # Check bet delay
        if not await AutoTradeService._check_bet_delay(db, bookmaker):
            return False

        try:
            # Handle simulated vs real bet placement
            if simulate:
                # Simulated trade - don't call API, just register the trade
                logger.info(
                    f"🎭 Simulating bet placement: {opportunity.event.home_team} vs {opportunity.event.away_team}, "
                    f"{opportunity.market.key}, {opportunity.odd.selection} @ {opportunity.odd.price}, "
                    f"Stake: {stake}"
                )
                
                # Mark bet as placed without calling API
                bet.status = "placed"
                # Use synthetic external_id to indicate this is a simulated bet
                bet.external_id = f"SIM-{datetime.now(timezone.utc).timestamp()}"
                
                # Save bet to database (balance is not affected)
                db.add(bet)
                await db.commit()
                await db.refresh(bet)
                
                logger.info(f"✅ Simulated bet registered successfully. Bet ID: {bet.id}")
                
            else:
                # Real bet placement via API
                logger.info(
                    f"Placing bet: {opportunity.event.home_team} vs {opportunity.event.away_team}, "
                    f"{opportunity.market.key}, {opportunity.odd.selection} @ {opportunity.odd.price}, "
                    f"Stake: {stake}"
                )
                
                result = await bookmaker_instance.place_bet(bet)
                
                logger.info(f"Bet placement API response: {result}")
                
                if result.get("success"):
                    # Update bet with response data
                    bet.status = "placed"
                    bet.external_id = result.get("bet_id")
                    
                    # Save bet to database
                    db.add(bet)
                    
                    # Deduct stake from bookmaker balance
                    bookmaker.balance -= stake
                    
                    await db.commit()
                    await db.refresh(bet)
                    
                    logger.info(f"✅ Bet placed successfully. Bet ID: {bet.id}, Bookmaker Bet ID: {bet.external_id}")
                else:
                    error_msg = result.get('error') or result.get('message') or 'Unknown error'
                    logger.warning(f"❌ Bet placement failed: {error_msg}")
                    logger.debug(f"Full response: {result}")
                    return False
            
            # Post-trade actions (Hide opportunity)
            await AutoTradeService._process_after_trade_actions(db, preset, opportunity)
            
            # Send Notification
            try:
                notifier = NotificationManager(db)
                await notifier.send_bet_notification(preset, bet)
            except Exception as e:
                logger.error(f"Failed to send bet notification: {e}", exc_info=True)

            return True
                
        except Exception as e:
            logger.error(f"Exception while placing bet: {e}", exc_info=True)
            await db.rollback()
            return False

    @staticmethod
    async def _check_bet_delay(db: AsyncSession, bookmaker: Bookmaker) -> bool:
        """
        Check if the bookmaker's bet delay requirement is met.
        
        Args:
            db: Database session
            bookmaker: Bookmaker model
            
        Returns:
            True if delay is met (or no delay configured), False otherwise
        """
        # If no delay configured, return True
        if not bookmaker.config.get('bet_delay_seconds') or bookmaker.config.get('bet_delay_seconds') <= 0:
            return True
        
        # Build query for last bet placed by this bookmaker
        # Note: We query the DB to get the most recent bet, as bookmaker.bets might be stale or unloaded
        query = (
            select(Bet)
            .where(
                Bet.bookmaker_id == bookmaker.id,
                Bet.status.in_(["placed", "open", "won", "lost", "pending"])
            )
            .order_by(desc(Bet.placed_at))
            .limit(1)
        )
        
        result = await db.execute(query)
        last_bet = result.scalar_one_or_none()
        
        if not last_bet:
            return True
        
        # Calculate time elapsed
        now = datetime.now(timezone.utc)
        # Ensure last_bet.placed_at is aware
        placed_at = last_bet.placed_at
        if placed_at.tzinfo is None:
            placed_at = placed_at.replace(tzinfo=timezone.utc)
            
        elapsed = (now - placed_at).total_seconds()
        
        if elapsed < bookmaker.config.get('bet_delay_seconds'):
            logger.info(
                f"Bet delay not met for {bookmaker.title}. "
                f"Elapsed: {int(elapsed)}s, Required: {bookmaker.config.get('bet_delay_seconds')}s. Skipping."
            )
            return False
            
        return True

    @staticmethod
    async def _process_after_trade_actions(
        db: AsyncSession, 
        preset: Preset, 
        opportunity: TradeOpportunity
    ) -> None:
        """
        Process post-trade actions such as hiding the event/market/selection 
        based on preset configuration.
        """
        action = preset.after_trade_action
        logger.info(f"Checking post-trade action for preset '{preset.name}'. Action configured: '{action}'")
        
        if action == "keep":
            return
            
        logger.info(f"Processing post-trade action '{action}' for preset {preset.name}")
        
        hidden_item = PresetHiddenItem(
            preset_id=preset.id,
            event_id=str(opportunity.event.id),
            expiry_at=datetime.now(timezone.utc) + timedelta(hours=24) # Default expiry
        )
        
        if action == "remove_match":
            # Hide entire event (default)
            hidden_item.market_key = None
            hidden_item.selection_norm = None
            logger.info("Hiding entire event from future trades")
            
        elif action == "remove_line":
            # Hide entire market (all selections)
            hidden_item.market_key = opportunity.market.key
            hidden_item.selection_norm = None
            logger.info(f"Hiding market '{opportunity.market.key}' from future trades")
            
        elif action == "remove_trade":
            # Hide specific selection only
            hidden_item.market_key = opportunity.market.key
            hidden_item.selection_norm = opportunity.odd.normalized_selection
            logger.info(f"Hiding selection '{opportunity.odd.normalized_selection}' from future trades")
            
        try:
            db.add(hidden_item)
            await db.commit()
            logger.info(f"✅ Successfully created PresetHiddenItem (ID: {hidden_item.id}) for action '{action}'")
        except Exception as e:
            logger.error(f"❌ Failed to create PresetHiddenItem: {e}", exc_info=True)
            await db.rollback()
