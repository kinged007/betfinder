from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import selectinload
from app.db.models import PresetHiddenItem, Event, Odds, Bookmaker, Preset, Market, Sport, League
from app.services.analytics.edge_calculator import EdgeCalculator
from app.services.bookmakers.base import BookmakerFactory, APIBookmaker
from app.core.config import settings

import logging
logger = logging.getLogger(__name__)

@dataclass
class TradeOpportunity:
    odd: Odds
    market: Market
    event: Event
    bookmaker: Bookmaker
    sport: Sport
    league: Optional[League]
    has_bet: bool
    edge: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event.id,
            "home": self.event.home_team,
            "away": self.event.away_team,
            "start_time": self.event.commence_time.isoformat() if "Z" in self.event.commence_time.isoformat() or "+" in self.event.commence_time.isoformat() else self.event.commence_time.isoformat() + "Z",
            "market": self.market.key,
            "selection": self.odd.normalized_selection,
            "selection_name": self.odd.selection,
            "bookmaker": self.bookmaker.title,
            "bookmaker_key": self.bookmaker.key,
            "sport": self.sport.title,
            "league": self.league.title if self.league else "Other",
            "price": self.odd.price,
            "true_odds": self.odd.true_odds,
            "has_bet": self.has_bet,
            "edge": self.edge,
            "implied_probability": self.odd.implied_probability,
            "point": self.odd.point,
            "url": self.odd.url,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

class TradeFinderService:
    def __init__(self):
        pass

    async def scan_opportunities(self, db: AsyncSession, preset_id: int, api_only: bool = False) -> List[TradeOpportunity]:
        # Fetch preset
        result = await db.execute(select(Preset).where(Preset.id == preset_id))
        preset = result.scalar_one_or_none()
        
        if not preset or not preset.active:
            return []

        # Time window logic
        # Assume a default game/match/event length of 120 minutes
        # Use this to filter out events that have already started
        event_length = timedelta(minutes=120)
        now = datetime.now(timezone.utc)
        
        # Build query
        # We start from Odds and join relationships explicitly
        from app.db.models import Bet

        query = (
            select(
                Odds, Market, Event, Bookmaker, Sport, League,
                # Subquery/Join check for existing bet
                # We can do this via an outer join or exists, but exists is cleaner for a boolean flag
                # However, to be efficient we can just left join distinct bets
                select(Bet.id).where(
                    Bet.event_id == Event.id,
                    Bet.bookmaker_id == Bookmaker.id,
                    Bet.market_key == Market.key,
                    Bet.selection == Odds.normalized_selection
                ).limit(1).exists().label("has_bet")
            )
            .select_from(Odds)
            .join(Odds.market)
            .join(Market.event)
            .join(Odds.bookmaker)
            .outerjoin(Event.league)
            .outerjoin(League.sport)
        )
        
        # Essential Filters
        query = query.where(Event.active == True)

        # Benchmarking: Filter for items with true odds unless ignored
        if not preset.ignore_benchmarks:
            query = query.where(Odds.true_odds != None)
        
        # Filter for API bookmakers only (for auto-trading)
        if api_only:
            query = query.where(Bookmaker.model_type == "api")
        
        # Preset Filters: Sports
        if preset.sports:
            query = query.where(Event.sport_key.in_(preset.sports))
            
        # Preset Filters: Bookmakers
        if preset.bookmakers:
            query = query.where(Bookmaker.key.in_(preset.bookmakers))
            
        # Preset Filters: Leagues
        if preset.leagues:
            query = query.where(Event.league_key.in_(preset.leagues))
            
        # Preset Filters: Markets
        if preset.markets:
            query = query.where(Market.key.in_(preset.markets))
            
        # Preset Filters: Selections (normalized)
        if preset.selections:
            query = query.where(Odds.normalized_selection.in_(preset.selections))
            
        # Preset Filters: Odds Range
        if preset.min_odds:
            query = query.where(Odds.price >= preset.min_odds)
        if preset.max_odds:
            query = query.where(Odds.price <= preset.max_odds)
            
        # Preset Filters: Game State (Pre-Game vs Live)
        if preset.is_live:
            query = query.where(Event.commence_time >= now - event_length).where(Event.commence_time <= now)
        else:
            query = query.where(Event.commence_time >= now)
            # Pre-Game: Hours Before Min/Max
            if preset.hours_before_min is not None:
                query = query.where(Event.commence_time > now + timedelta(hours=preset.hours_before_min))
            if preset.hours_before_max is not None:
                query = query.where(Event.commence_time < now + timedelta(hours=preset.hours_before_max))

        # Hidden Items Filtering (SQL side)
        # We exclude rows where there is a matching HiddenItem for this preset
        # Hidden Logic:
        # 1. Hide Event if (hidden.event_id == event.id AND hidden.market_key IS NULL)
        # 2. Hide Market if (hidden.event_id == event.id AND hidden.market_key == market.key AND hidden.selection_norm IS NULL)
        # 3. Hide Selection if (hidden.event_id == event.id AND hidden.market_key == market.key AND hidden.selection_norm == odds.normalized_selection)
        
        # Using NOT EXISTS involves complex OR logic.
        # Ideally: NOT EXISTS (SELECT 1 FROM hidden_items WHERE hidden.preset_id = preset.id AND ...)
        
        query = query.where(
            ~select(PresetHiddenItem.id).where(
                PresetHiddenItem.preset_id == preset.id,
                PresetHiddenItem.event_id == Event.id,
                or_(
                    PresetHiddenItem.market_key.is_(None),  # Hide entire event
                    and_(
                        PresetHiddenItem.market_key == Market.key,
                        PresetHiddenItem.selection_norm.is_(None) # Hide market
                    ),
                    and_(
                        PresetHiddenItem.market_key == Market.key,
                        PresetHiddenItem.selection_norm == Odds.normalized_selection # Hide selection
                    )
                )
            ).exists()
        )
            
        result = await db.execute(query)
        rows = result.all()

        opportunities = []
        for odd, market, event, bookmaker, sport, league, has_bet in rows:
            # We already filtered has_bet in SQL (as a boolean flag)
            # And hidden items are filtered out in SQL

            # Final Edge calculation and check
            if odd.true_odds:
                edge = (odd.price / odd.true_odds) - 1.0
            else:
                edge = None
            
            if not odd.implied_probability:
                odd.implied_probability = 1 / odd.price
            
            # Preset Filters: Edge Range
            if edge is not None:
                if preset.min_edge is not None and (edge * 100) < preset.min_edge:
                    continue
                if preset.max_edge is not None and (edge * 100) > preset.max_edge:
                    continue
            elif preset.min_edge is not None or preset.max_edge is not None:
                continue
            
            # Preset Filters: Probability Range
            if preset.min_probability is not None and odd.implied_probability is not None:
                if (odd.implied_probability * 100) < preset.min_probability:
                    continue
            if preset.max_probability is not None and odd.implied_probability is not None:
                if (odd.implied_probability * 100) > preset.max_probability:
                    continue
                
            opportunities.append(TradeOpportunity(
                odd=odd,
                market=market,
                event=event,
                bookmaker=bookmaker,
                sport=sport,
                league=league,
                has_bet=has_bet,
                edge=edge
            ))
            
        return opportunities[:100]

    async def sync_live_odds(self, db: AsyncSession, opportunities: List[TradeOpportunity]) -> None:
        """
        Background task to sync live odds for opportunities.
        Groups opportunities by bookmaker and event, then calls obtain_odds
        only for events that should be synced based on throttling rules.
        """
        
        # Group opportunities by (bookmaker.key, league.key)
        # Note: We rely on event.league_key which should be populated.
        # If league is None, we might face issues, but events usually have it.
        bookmaker_league_groups: Dict[tuple, List[TradeOpportunity]] = {}
        for opp in opportunities:
            key = (opp.bookmaker.key, opp.event.league_key)
            if key not in bookmaker_league_groups:
                bookmaker_league_groups[key] = []
            bookmaker_league_groups[key].append(opp)
        
        # For each group, sync eligible events
        for (bookmaker_key, league_key), opps in bookmaker_league_groups.items():
            try:
                # Get bookmaker config from DB
                result = await db.execute(
                    select(Bookmaker).where(Bookmaker.key == bookmaker_key)
                )
                bookmaker_model = result.scalar_one_or_none()
                if not bookmaker_model or not bookmaker_model.config:
                    continue
                
                # Initialize bookmaker instance
                # We instantiate first to check if it's an APIBookmaker, making this robust against DB type errors
                bookmaker_instance = BookmakerFactory.get_bookmaker(
                    bookmaker_key, 
                    bookmaker_model.config,
                    db
                )
                
                # Only process API bookmakers (Class check is more reliable than DB string)
                if not isinstance(bookmaker_instance, APIBookmaker):
                    continue
                
                # Filter events that should be synced based on throttling
                events_to_sync = set()
                for opp in opps:
                    event_id_str = str(opp.event.id)
                    if bookmaker_instance.should_sync_event(event_id_str, opp.event.commence_time):
                        events_to_sync.add(event_id_str)
                
                if not events_to_sync:
                    continue
                
                await self.sync_bookmaker_odds(db, bookmaker_model, list(events_to_sync), league_key)
                
            except Exception as e:
                # Log the error
                logger.error(f"Error syncing odds for {bookmaker_key}: {str(e)}")
                await db.rollback()
                pass

    async def sync_bookmaker_odds(self, db: AsyncSession, bookmaker_model: Bookmaker, event_ids: List[str], league_key: str) -> int:
        """
        Sync live odds for a specific bookmaker and a list of events.
        Low level method used by both opportunity-based sync and global sync.
        """
        try:
            # Initialize bookmaker instance
            bookmaker_instance = BookmakerFactory.get_bookmaker(
                bookmaker_model.key, 
                bookmaker_model.config,
                db
            )
            
            # Call obtain_odds for this bookmaker
            # This API call fetches fresh data
            # TODO SPK: sport_key should be league_key for all bookmakers. BK will use mapping to get their league id to find odds
            raw_odds = await bookmaker_instance.obtain_odds(
                league_key=league_key,
                event_ids=event_ids,
            )
            
            if not raw_odds:
                print(f"DEBUG: No odds returned for {bookmaker_model.key} / {league_key}")
                return 0
            
            print(f"DEBUG: {bookmaker_model.key} returned {len(raw_odds)} odds entries for {league_key}")

            # Optimization: Bulk fetch existing odds for update
            # We want to find Odds matching (ext_event_id, market_key, sel/sel_norm) for this bookmaker
            
            # Get event IDs from response to scope our query
            received_event_ids = list(set([entry.get("external_event_id") for entry in raw_odds if entry.get("external_event_id")]))
            print(f"DEBUG: Updating odds for events: {received_event_ids}")
            
            stmt = (
                select(Odds, Market.event_id, Market.key)
                .join(Market).join(Event)
                .where(
                    Event.id.in_(received_event_ids),
                    Odds.bookmaker_id == bookmaker_model.id
                )
            )
            res = await db.execute(stmt)
            existing_rows = res.all()
            print(f"DEBUG: Found {len(existing_rows)} existing odds rows in DB for these events.")
            
            # Build Lookup Map
            # Keys: (ext_event_id, market_key, normalized_selection) -> Odds Object
            # Also support exact selection lookup as fallback? 
            # Ideally we rely on normalized_selection, but raw_odds might have 'selection'.
            # We will map both:
            # (ev, mkt, sel) -> Odd
            # (ev, mkt, norm_sel) -> Odd
            
            lookup_map = {}
            for odd, ev_id, mkt_key in existing_rows:
                # Key 1: Normalized
                if odd.normalized_selection:
                    lookup_map[(ev_id, mkt_key, odd.normalized_selection)] = odd
                # Key 2: Exact
                lookup_map[(ev_id, mkt_key, odd.selection)] = odd
            
            total_updated = 0
            for entry in raw_odds:
                ext_event_id = entry.get("external_event_id")
                mkt_key = entry.get("market_key")
                sel = entry.get("selection")
                
                new_price = entry.get("price")
                new_point = entry.get("point")
                
                # Try finding existing record
                odds_record = lookup_map.get((ext_event_id, mkt_key, sel))
                
                if odds_record:
                    # Debug log significant changes or specific event updates
                    if abs(odds_record.price - new_price) > 0.001:
                        print(f"DEBUG: Updating price {ext_event_id}/{mkt_key}/{sel}: {odds_record.price} -> {new_price}")
                    
                    # Update fields
                    odds_record.price = new_price
                    odds_record.point = new_point
                    
                    if entry.get("bet_limit"):
                        odds_record.bet_limit = entry.get("bet_limit")
                        
                    # Update discovered IDs if provided
                    if entry.get("sid"): odds_record.sid = entry["sid"]
                    if entry.get("market_sid"): odds_record.market_sid = entry["market_sid"]
                    if entry.get("event_sid"): odds_record.event_sid = entry["event_sid"]

                    total_updated += 1
            
            logger.info(f"Sync complete for {bookmaker_model.key}. Updated {total_updated} odds.")
            
            # Record successful sync for all events (even if no odds updated, we checked)
            for event_id in event_ids:
                bookmaker_instance.record_sync(event_id)
            
            await db.commit()
            return total_updated
            
        except Exception as e:
            logger.error(f"Error syncing odds for {bookmaker_model.key}: {str(e)}")
            await db.rollback()
            return 0

    async def sync_all_api_bookmaker_odds(self, db: AsyncSession) -> None:
        """
        Global sync job to update odds for all API bookmakers and future events.
        """
        logger.info("Starting global API Bookmaker Live Odds sync...")
        
        # 1. Get all Active API bookmakers IDs to avoid session expiration issues during loop
        res = await db.execute(
            select(Bookmaker.id)
            .where(
                Bookmaker.model_type == 'api',
                Bookmaker.active == True,
            )
        )
        api_bookmaker_ids = res.scalars().all()
        
        if not api_bookmaker_ids:
            logger.info("No active API bookmakers found for sync.")
            return

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=2) # Include recently started (live) events
        
        for bm_id in api_bookmaker_ids:
            # Fetch fresh bookmaker instance to avoid MissingGreenlet/expired object issues
            # because sync_bookmaker_odds commits the session.
            bm = await db.get(Bookmaker, bm_id)
            if not bm:
                continue
                
            # Additional check for config
            if not bm.config:
                logger.warning(f"Skipping bookmaker {bm.title} ({bm.key}) due to missing/empty config.")
                continue
                
            try:
                # 2. Find events this bookmaker has odds for
                # We group by league_key to keep requests efficient
                stmt = (
                    select(Event.id, Event.league_key, Event.commence_time)
                    .join(Market).join(Odds)
                    .where(
                        Odds.bookmaker_id == bm.id,
                        Event.commence_time > cutoff
                    )
                    .distinct()
                )
                res = await db.execute(stmt)
                events = res.all()
                
                if not events:
                    continue
                
                # Group by league_key
                league_groups: Dict[str, List[tuple]] = {}
                for eid, lk, ct in events:
                    if lk not in league_groups:
                        league_groups[lk] = []
                    league_groups[lk].append((eid, ct))
                
                total_bm_synced = 0
                for league_key, ev_list in league_groups.items():
                    # Filter based on bookmaker's internal throttling
                    bookmaker_instance = BookmakerFactory.get_bookmaker(bm.key, bm.config or {}, db)
                    events_to_sync = [str(eid) for eid, ct in ev_list if bookmaker_instance.should_sync_event(str(eid), ct)]
                    
                    if not events_to_sync:
                        continue
                    
                    updated = await self.sync_bookmaker_odds(db, bm, events_to_sync, league_key)
                    total_bm_synced += updated
                    
                logger.info(f"Global sync for {bm.title} complete. Total updated: {total_bm_synced}")
                
            except Exception as e:
                logger.error(f"Global sync failed for bookmaker {bm.key}: {e}")
                continue

    async def scan_hidden_opportunities(self, db: AsyncSession, preset_id: int) -> List[Dict[str, Any]]:
        # Fetch preset with hidden items eagerly or separately
        # Load preset with hidden items
        result = await db.execute(
            select(Preset)
            .options(selectinload(Preset.hidden_items))
            .where(Preset.id == preset_id)
        )
        preset = result.scalar_one_or_none()
        
        if not preset or not preset.active:
            return []

        # Time window logic
        # Show recent and future events (looser than main scan)
        now = datetime.now(timezone.utc)
        limit_time = now - timedelta(hours=6) # Show items from last 6 hours (live/just finished)
        
        query = (
            select(Odds, Market, Event, Bookmaker, Sport, League)
            .select_from(Odds)
            .join(Odds.market)
            .join(Market.event)
            .join(Odds.bookmaker)
            .outerjoin(Event.league)
            .outerjoin(League.sport)
        )
        
        query = query.where(
            Event.active == True,
            Event.commence_time > limit_time
        )
        
        if not preset.ignore_benchmarks:
            query = query.where(Odds.true_odds != None)
        
        if preset.sports:
            query = query.where(Event.sport_key.in_(preset.sports))
        if preset.bookmakers:
            query = query.where(Bookmaker.key.in_(preset.bookmakers))
        if preset.leagues:
            query = query.where(Event.league_key.in_(preset.leagues))
        if preset.markets:
            query = query.where(Market.key.in_(preset.markets))
        if preset.selections:
            query = query.where(Odds.normalized_selection.in_(preset.selections))
            
        # NOTE: Removed Min/Max Odds filters to ensure hidden items are seen even if odds shifted
        # NOTE: Removed Hours Before Min/Max filters to ensure hidden items are seen regardless of time window

        result = await db.execute(query)
        rows = result.all()
        
        event_ids = list(set(row[2].id for row in rows))
        existing_bets = []
        if event_ids:
            from app.db.models import Bet
            result = await db.execute(select(Bet).where(Bet.event_id.in_(event_ids)))
            existing_bets = result.scalars().all()

        opportunities = []
        for odd, market, event, bookmaker, sport, league in rows:
            is_hidden = False
            matched_hidden_id = None
            for hidden in preset.hidden_items:
                if hidden.event_id == event.id:
                    if hidden.market_key is None:
                        is_hidden = True
                        matched_hidden_id = hidden.id
                        break
                    
                    if hidden.market_key == market.key:
                        if hidden.selection_norm is None:
                            is_hidden = True
                            matched_hidden_id = hidden.id
                            break
                        
                        if hidden.selection_norm == odd.normalized_selection:
                            is_hidden = True
                            matched_hidden_id = hidden.id
                            break
            
            # ONLY including HIDDEN items
            if not is_hidden:
                continue

            has_bet = any(
                b.event_id == event.id and 
                b.bookmaker_id == bookmaker.id and 
                b.market_key == market.key and 
                b.selection == odd.normalized_selection
                for b in existing_bets
            )

            if odd.true_odds:
                edge = (odd.price / odd.true_odds) - 1.0
            else:
                edge = None
            
            # NOTE: Removed Min/Max Edge and Probability filters check
                
            opportunities.append({
                "event_id": event.id,
                "home": event.home_team,
                "away": event.away_team,
                "start_time": event.commence_time.isoformat() if "Z" in event.commence_time.isoformat() or "+" in event.commence_time.isoformat() else event.commence_time.isoformat() + "Z",
                "market": market.key,
                "selection": odd.normalized_selection,
                "selection_name": odd.selection,
                "bookmaker": bookmaker.title,
                "bookmaker_key": bookmaker.key,
                "sport": sport.title,
                "league": league.title if league else "Other",
                "price": odd.price,
                "true_odds": odd.true_odds,
                "has_bet": has_bet,
                "edge": edge,
                "point": odd.point,
                "url": odd.url,
                "hidden_id": matched_hidden_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
        return opportunities
