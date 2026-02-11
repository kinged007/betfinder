
from datetime import datetime, timedelta, timezone
import logging
import hashlib
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, and_
from app.services.the_odds_api import TheOddsAPIClient
from app.services.standardizer import DataStandardizer
from app.db.models import Sport, League, Event, Market, Odds, Bookmaker, Mapping
from app.repositories.base import BaseRepository
from app.schemas.odds import OddsEvent, OddsSport
import difflib
import re
from app.core.config import settings
from app.services.analytics.trade_finder import TradeFinderService
from app.services.notifications.manager import NotificationManager

logger = logging.getLogger(__name__)

class DataIngester:
    def __init__(self, api_client: TheOddsAPIClient, standardizer: DataStandardizer = None):
        self.api_client = api_client
        self.standardizer = standardizer
        self.sport_repo = BaseRepository(Sport)
        self.league_repo = BaseRepository(League)
        self.event_repo = BaseRepository(Event)
        self.bookmaker_repo = BaseRepository(Bookmaker)

    async def sync_sports(self, db: AsyncSession):
        logger.info("Starting sync_sports...")
        
        # Check if we need to sync
        result = await db.execute(select(Sport).limit(1))
        first_sport = result.scalars().first()
        
        should_sync = False
        if not first_sport:
             logger.info("Sports table empty. Sync required.")
             should_sync = True
        else:
             if first_sport.updated_at:
                 updated_at = first_sport.updated_at
                 if updated_at.tzinfo is None:
                     updated_at = updated_at.replace(tzinfo=timezone.utc)
                 age = datetime.now(timezone.utc) - updated_at
                 if age > timedelta(days=7):
                     logger.info(f"Sports data > 7 days old ({age}). Sync required.")
                     should_sync = True
                 else:
                     logger.debug(f"Sports data is fresh ({age} old). Skipping sync.")
             else:
                 should_sync = True

        if not should_sync:
            return

        # 1. Fetch from TheOddsAPI
        logger.info("Fetching sports from TheOddsAPI...")
        try:
            toa_data = await self.api_client.get_sports()
            logger.info(f"Fetched {len(toa_data)} sports from TheOddsAPI.")
            await self._process_sports_data(db, toa_data, source="the_odds_api")
        except Exception as e:
            error_msg = f"Failed to fetch sports from TheOddsAPI: {e}"
            logger.error(error_msg)
            # Send Notification
            notif_manager = NotificationManager(db)
            await notif_manager.send_error_notification("Sync Sports Failed (TOA)", error_msg)

        # 2. Fetch from other Active API Bookmakers
        from app.services.bookmakers.base import BookmakerFactory
        
        # Get active API bookmakers
        result = await db.execute(select(Bookmaker).where(Bookmaker.active == True, Bookmaker.model_type == 'api'))
        active_bookmakers = result.scalars().all()
        
        for bk_model in active_bookmakers:
            # Skip TheOddsAPI (handled by client) if it were a model, but it's usually not.
            # If the bookmaker has 'obtain_sports' capability (e.g. SXBet), call it.
            try:
                bk_service = BookmakerFactory.get_bookmaker(bk_model.key, bk_model.config or {}, db)
                if hasattr(bk_service, "obtain_sports"):
                    logger.info(f"Fetching sports from {bk_model.title}...")
                    bk_sports = await bk_service.obtain_sports()
                    if bk_sports:
                        logger.info(f"Fetched {len(bk_sports)} sports from {bk_model.title}.")
                        await self._process_sports_data(db, bk_sports, source=bk_model.key)
            except Exception as e:
                error_msg = f"Failed to fetch sports from {bk_model.title}: {e}"
                logger.error(error_msg)
                notif_manager = NotificationManager(db)
                await notif_manager.send_error_notification(f"Sync Sports Failed ({bk_model.title})", error_msg)
        
        logger.info("sync_sports completed.")

    async def _process_sports_data(self, db: AsyncSession, data: List[OddsSport], source: str = "the_odds_api"):
        """
        Process sports/leagues data from any source.
        Bookmakers should have already resolved mappings and returned internal keys.
        """
        for item in data:
            if isinstance(item, dict):
                 # Fallback/Error guard
                 raise ValueError("Received dict instead of OddsSport model")

            key = item.key
            group = item.group
            title = item.title
            active = item.active
            has_outrights = item.has_outrights
            
            # Standardize Sport First
            sport_key = group.lower().replace(" ", "")
            
            # Ensure Sport Exists
            existing_sport = await self.sport_repo.get(db, sport_key)
            if not existing_sport:
                logger.debug(f"Creating new sport: {sport_key}")
                existing_sport = await self.sport_repo.create(db, obj_in={
                    "key": sport_key,
                    "title": group,
                    "group": group,
                    "active": True
                })

            # Update or Create League
            existing_league = await self.league_repo.get(db, key)
            if existing_league:
                 await self.league_repo.update(db, db_obj=existing_league, obj_in={
                     "active": active,
                     "group": group, 
                     "has_outrights": has_outrights, 
                     "sport_key": sport_key
                 })
            else:
                logger.debug(f"Creating new league: {key}")
                from app.schemas.sports_config import POPULAR_SPORT_KEYS
                is_popular = key in POPULAR_SPORT_KEYS
                
                await self.league_repo.create(db, obj_in={
                    "key": key,
                    "active": active,
                    "title": title,
                    "group": group,
                    "has_outrights": has_outrights,
                    "sport_key": sport_key,
                    "popular": is_popular
                })

    async def sync_odds(self, db: AsyncSession, sport_key: str):
        odds_data = await self.api_client.get_odds(sport_key, standardizer=self.standardizer, db=db)
        await self._process_odds_data(db, odds_data)

    async def sync_bookmakers(self, db: AsyncSession, regions: str = None):
        if regions is None:
            regions = settings.THE_ODDS_API_REGIONS
        logger.info(f"Starting get_bookmakers for regions: {regions}...")
        bookmakers_data = await self.api_client.get_bookmakers(regions=regions) # TODO: get_bookmakers also calls get_odds internally?
        # get_bookmakers in TheOddsAPIClient calls get_odds(sport_key="upcoming", ...)
        # We need to update get_bookmakers to accept standardizer/db as well or call get_odds directly here?
        # Let's check get_bookmakers in the_odds_api.py. 
        # It calls self.get_odds(sport_key="upcoming", ...)
        # So we should update get_bookmakers signature too? Or just call get_odds here.
        # TheOddsAPIClient.get_bookmakers definition:
        # async def get_bookmakers(self, regions=..., markets=...) -> ...
        # return await self.get_odds(sport_key="upcoming", ...)
        # I missed updating get_bookmakers signature in the previous step. 
        # I should fix TheOddsAPIClient.get_bookmakers first or update this call to use get_odds directly if get_bookmakers is just a wrapper.
        # It is just a wrapper.
        # I will change this to call get_odds directly with sport_key='upcoming' to pass standardizer.
        bookmakers_data = await self.api_client.get_odds(
            sport_key="upcoming", 
            regions=regions, 
            markets="h2h,spreads,totals",
            standardizer=self.standardizer,
            db=db
        )
        logger.info(f"Fetched {len(bookmakers_data)} (events with) bookmakers.") # Note: get_bookmakers returns events with bookmakers
        await self._process_odds_data(db, bookmakers_data) 

        # Ensure all registered bookmaker classes exist in DB
        from app.services.bookmakers.base import BookmakerFactory
        registered = BookmakerFactory.get_registered_bookmakers_info()
        created_count = 0
        for info in registered:
            result = await db.execute(select(Bookmaker).where(Bookmaker.key == info["key"]))
            if not result.scalar_one_or_none():
                new_bk = Bookmaker(
                    key=info["key"],
                    title=info["title"],
                    model_type=info["model_type"],
                    active=False,
                )
                db.add(new_bk)
                created_count += 1
                logger.info(f"Created bookmaker '{info['title']}' ({info['key']}) from registered class.")
        if created_count > 0:
            await db.commit()

        logger.info("sync_bookmakers completed.")

    async def sync_league(
        self, 
        db: AsyncSession, 
        league_key: str, 
        markets: str, 
        active_bookmakers: List[Any],
        preset_names: List[str] = []
    ):
        """
        Fetches events and odds for a specific league, consolidating requests.
        """
        logger.info(f"Syncing league: {league_key} (Presets: {','.join(preset_names)})")
        
        regions = settings.THE_ODDS_API_REGIONS
        # Ensure we have a valid market string
        if not markets:
            markets = "h2h,spreads,totals"

        # Instantiate bookmaker services for other providers
        from app.services.bookmakers.base import BookmakerFactory
        bookmaker_services = {}
        
        # We need a list of keys for TheOddsAPI
        toa_bookmaker_keys = []
        
        for bk_model in active_bookmakers:
            toa_bookmaker_keys.append(bk_model.key)
            try:
                # 1. Check if it's a known service with fetch_league_odds
                # We instantiate gently
                bk_service = BookmakerFactory.get_bookmaker(bk_model.key, bk_model.config or {}, db)
                if hasattr(bk_service, "fetch_league_odds"):
                    bookmaker_services[bk_model.key] = bk_service
            except Exception:
                pass
        
        # 1. Fetch from TheOddsAPI
        # We always try TOA for the 'upcoming' or specific league, assuming TOA key covers it.
        try:
            # logger.debug(f"Fetching TOA odds for {league_key} with markets: {markets}")
            # For Odds API, we should only request acceptable markets, otherwise api will return an error.
            odds_markets = ",".join([m for m in markets.split(",") if m in ['h2h','spreads','totals','outrights']])
            odds_data = await self.api_client.get_odds(
                sport_key=league_key,
                regions=regions,
                markets=odds_markets,
                bookmakers=",".join(toa_bookmaker_keys),
                standardizer=self.standardizer,
                db=db
            )
            await self._process_odds_data(db, odds_data)
        except Exception as toa_error:
            logger.error(f"TheOddsAPI fetch failed for {league_key}: {toa_error}")
            
        # 2. Fetch from Custom Bookmaker Services (e.g. SX Bet)
        for bk_key, bk_service in bookmaker_services.items():
            try:
                # Parse markets string to list for filtering if supported
                allowed_markets = markets.split(",") if markets else None
                # logger.debug(f"Fetching {bk_key} odds for {league_key}...")
                odds_data = await bk_service.fetch_league_odds(league_key, allowed_markets=allowed_markets)
                if odds_data:
                    await self._process_odds_data(db, odds_data)
            except Exception as bk_error:
                 logger.error(f"{bk_key} fetch failed for {league_key}: {bk_error}")

    async def sync_data_for_preset(self, db: AsyncSession, preset: Any):
        """
        Wrapper for single-preset sync (legacy support).
        """
        # logger.warning(f"sync_data_for_preset is deprecated. Use scheduler aggregation.")
        
        leagues = preset.leagues or []
        if not preset.leagues and not preset.sports:
            leagues = ['upcoming']
        elif not preset.leagues and preset.sports:
             # Fallback for sport-only presets if any
             return

        markets = ",".join(preset.markets) if preset.markets else "h2h,spreads,totals"
        
        result = await db.execute(select(Bookmaker).where(Bookmaker.active == True, Bookmaker.model_type == 'api'))
        active_bookmakers = result.scalars().all()
        
        for league_key in leagues:
            await self.sync_league(db, league_key, markets, active_bookmakers, [preset.name])



    async def _find_existing_event(
        self,
        db: AsyncSession,
        league_key: str,
        commence_time: datetime,
        home_team: str,
        away_team: str,
        time_tolerance_minutes: int = 5
    ) -> Optional[str]:
        """
        Find existing event by league + time + team fuzzy match.
        Returns event ID if found, None otherwise.
        """
        from datetime import timedelta
        from app.services.bookmakers.base import token_sort_ratio
        
        # Calculate time window
        time_start = commence_time - timedelta(minutes=time_tolerance_minutes)
        time_end = commence_time + timedelta(minutes=time_tolerance_minutes)
        
        # Query events in same league and time window
        result = await db.execute(
            select(Event).where(
                and_(
                    Event.league_key == league_key,
                    Event.commence_time >= time_start,
                    Event.commence_time <= time_end
                )
            )
        )
        candidates = result.scalars().all()
        
        if not candidates:
            return None
        
        # Fuzzy match on team names
        best_match = None
        best_score = 0.0
        
        for candidate in candidates:
            # Calculate fuzzy match scores for both teams
            home_score = token_sort_ratio(home_team, candidate.home_team)
            away_score = token_sort_ratio(away_team, candidate.away_team)
            
            # Average score (both teams must match well)
            avg_score = (home_score + away_score) / 2.0
            
            if avg_score > best_score:
                best_score = avg_score
                best_match = candidate
        
        # Return match if confidence is high
        if best_score > 0.85 and best_match:
            logger.info(f"Matched event '{home_team} vs {away_team}' to existing '{best_match.home_team} vs {best_match.away_team}' (score: {best_score:.2f})")
            return best_match.id
        
        return None

    async def _process_odds_data(self, db: AsyncSession, odds_data: List[OddsEvent]):
        for event_data in odds_data:
            # Handle both Pydantic model and Dict (for backward compatibility if needed, or strict model)
            if isinstance(event_data, dict):
                # Fallback if we still receive dicts from somewhere else (unlikely with strict typing but safe)
                raise ValueError("Received dict instead of OddsEvent model")
                # Actually, we should assume models.
            
            bookmaker_event_id = event_data.id 
            commence_time = event_data.commence_time
            if commence_time.tzinfo is None:
                commence_time = commence_time.replace(tzinfo=timezone.utc)
            
            # The-Odds-API 'sport_key' in odds response IS the league slug (e.g. 'soccer_epl')
            league_slug = event_data.sport_key
            
            # Lookup league to get the actual parent sport key (e.g. 'soccer')
            league = await db.get(League, league_slug)
            parent_sport_key = league.sport_key if league else league_slug
            
            home_team = event_data.home_team
            away_team = event_data.away_team
            
            # Try to find existing event using fuzzy matching
            event_id = await self._find_existing_event(
                db, league_slug, commence_time, home_team, away_team
            )
            
            # If not found, generate deterministic internal ID
            if not event_id:
                # Use hash of league + teams + time for deterministic ID
                event_id = hashlib.md5(
                    f"{league_slug}_{home_team}_{away_team}_{commence_time.isoformat()}".encode()
                ).hexdigest()
                logger.debug(f"Generated new event ID: {event_id} for '{home_team} vs {away_team}'")
            
            existing_event = await self.event_repo.get(db, event_id)
            if existing_event:
               await self.event_repo.update(db, db_obj=existing_event, obj_in={
                   "commence_time": commence_time,
                   "home_team": home_team,
                   "away_team": away_team,
                   "sport_key": parent_sport_key,
                   "league_key": league_slug
               })
            else:
                await self.event_repo.create(db, obj_in={
                    "id": event_id,
                    "sport_key": parent_sport_key,
                    "league_key": league_slug,
                    "commence_time": commence_time,
                    "home_team": home_team,
                    "away_team": away_team
                })
            
            for b_data in event_data.bookmakers:
                bk_key = b_data.key
                bk_title = b_data.title
                last_update = b_data.last_update
                if last_update.tzinfo is None:
                    last_update = last_update.replace(tzinfo=timezone.utc)
                
                result = await db.execute(select(Bookmaker).where(Bookmaker.key == bk_key))
                bookmaker = result.scalar_one_or_none()
                
                if not bookmaker:
                    bookmaker = Bookmaker(
                        key=bk_key,
                        title=bk_title,
                        last_update=last_update
                    )
                    db.add(bookmaker)
                    await db.commit()
                    await db.refresh(bookmaker)
                else:
                    bookmaker.last_update = last_update
                    db.add(bookmaker)
                
                for m_data in b_data.markets:
                    m_key = m_data.key
                    
                    result = await db.execute(
                        select(Market).where(Market.event_id == event_id, Market.key == m_key)
                    )
                    market = result.scalar_one_or_none()
                    
                    if not market:
                        market = Market(key=m_key, event_id=event_id)
                        db.add(market)
                        await db.commit()
                        await db.refresh(market)
                        
                    # Fetch existing odds for this market and bookmaker
                    existing_odds_result = await db.execute(
                        select(Odds).where(Odds.market_id == market.id, Odds.bookmaker_id == bookmaker.id)
                    )
                    existing_odds_list = existing_odds_result.scalars().all()
                    
                    # Create a map for quick lookup: (selection, point) -> Odds object
                    # Point can be None, so we handle that.
                    existing_odds_map = {
                        (o.selection, o.point): o for o in existing_odds_list
                    }
                    
                    for outcome in m_data.outcomes:
                        price = outcome.price
                        name = outcome.selection
                        point = outcome.point
                        
                        # Extract Links (Priority: Outcome > Market > Bookmaker(Event))
                        # In Pydantic model this logic should ideally be done upstream but we can fallback here
                        url = outcome.url 
                        if not url:
                           url = m_data.link
                        if not url:
                           url = b_data.link
                        
                        # Extract SIDs
                        outcome_sid = outcome.sid
                        market_sid = outcome.market_sid or m_data.sid
                        event_sid = outcome.event_sid or b_data.sid
                        
                        bet_limit = outcome.bet_limit

                        normalized_name = outcome.normalized_selection
                        # We rely on the model having populated normalized_selection
                        
                        # Check if odds exist
                        existing_odd = existing_odds_map.get((name, point))
                        
                        if existing_odd:
                            # Update existing
                            existing_odd.price = price
                            existing_odd.url = url
                            existing_odd.event_sid = event_sid
                            existing_odd.market_sid = market_sid
                            existing_odd.sid = outcome_sid
                            existing_odd.bet_limit = bet_limit
                            existing_odd.normalized_selection = normalized_name 
                            db.add(existing_odd)
                        else:
                            # Create new
                            new_odd = Odds(
                                market_id=market.id,
                                bookmaker_id=bookmaker.id,
                                selection=name,
                                normalized_selection=normalized_name,
                                price=price,
                                point=point,
                                url=url,
                                event_sid=event_sid,
                                market_sid=market_sid,
                                sid=outcome_sid,
                                bet_limit=bet_limit
                            )
                            db.add(new_odd)
                
        await db.commit()
