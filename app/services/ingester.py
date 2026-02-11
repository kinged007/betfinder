
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

    async def _process_sports_data(self, db: AsyncSession, data: List[Dict[str, Any]], source: str = "the_odds_api"):
        """
        Process sports/leagues data from any source.
        Bookmakers should have already resolved mappings and returned internal keys.
        """
        for item in data:
            key = item["key"]
            group = item["group"]
            title = item["title"]
            active = item["active"]
            has_outrights = item["has_outrights"]
            
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
                await self.league_repo.create(db, obj_in={
                    "key": key,
                    "active": active,
                    "title": title,
                    "group": group,
                    "has_outrights": has_outrights,
                    "sport_key": sport_key
                })

    async def sync_odds(self, db: AsyncSession, sport_key: str):
        odds_data = await self.api_client.get_odds(sport_key)
        await self._process_odds_data(db, odds_data)

    async def sync_bookmakers(self, db: AsyncSession, regions: str = None):
        if regions is None:
            regions = settings.THE_ODDS_API_REGIONS
        logger.info(f"Starting get_bookmakers for regions: {regions}...")
        bookmakers_data = await self.api_client.get_bookmakers(regions=regions)
        logger.info(f"Fetched {len(bookmakers_data)} bookmakers.")
        await self._process_odds_data(db, bookmakers_data) 
        logger.info("sync_bookmakers completed.")

    async def sync_data_for_preset(self, db: AsyncSession, preset: Any):
        """
        Fetches new events and odds for the leagues/sports in the preset.
        """
        logger.info(f"Syncing data for preset: {preset.name}")
        
        leagues = preset.leagues or []
        if not preset.leagues and not preset.sports:
            leagues = ['upcoming']
        elif not preset.leagues and preset.sports:
            logger.warning(f"No leagues selected for preset {preset.name}. Skipping sync for now (TODO).")
            # TODO: Handle 'no league selected' logic- if show_all_leagues, get all leagues, else show popular leagues
            return
            
        # Prepare parameters from preset
        regions = settings.THE_ODDS_API_REGIONS
        markets = ",".join(preset.markets) if preset.markets else "h2h,spreads,totals"

        # Get list of active API bookmaker models from db
        result = await db.execute(select(Bookmaker).where(Bookmaker.active == True, Bookmaker.model_type == 'api'))
        active_bookmakers = result.scalars().all()
        logger.info(f"Syncing data for Preset: {preset.name} with bookmakers: {[bk.key for bk in active_bookmakers]}")

        # Instantiate bookmaker services
        from app.services.bookmakers.base import BookmakerFactory
        bookmaker_services = {}
        for bk_model in active_bookmakers:
            try:
                bk_service = BookmakerFactory.get_bookmaker(bk_model.key, bk_model.config or {}, db)
                if hasattr(bk_service, "fetch_league_odds"):
                    bookmaker_services[bk_model.key] = bk_service
            except Exception as e:
                logger.error(f"Failed to instantiate bookmaker {bk_model.key}: {e}")
        
        for league_key in leagues:
            try:
                logger.info(f"Fetching odds for league: {league_key} (Preset: {preset.name})")
                
                # Try TheOddsAPI first (if available)
                try:
                    odds_data = await self.api_client.get_odds(
                        sport_key=league_key,
                        regions=regions,
                        markets=markets,
                        bookmakers=",".join([bk.key for bk in active_bookmakers])
                    )
                    await self._process_odds_data(db, odds_data)
                except Exception as toa_error:
                    logger.debug(f"TheOddsAPI fetch failed for {league_key}: {toa_error}")
                
                # Try each API bookmaker
                for bk_key, bk_service in bookmaker_services.items():
                    try:
                        # Parse markets string to list for filtering
                        allowed_markets = markets.split(",") if markets else None
                        odds_data = await bk_service.fetch_league_odds(league_key, allowed_markets=allowed_markets)
                        if odds_data:
                            await self._process_odds_data(db, odds_data)
                    except Exception as bk_error:
                        logger.debug(f"{bk_key} fetch failed for {league_key}: {bk_error}")
                
            except Exception as e:
                error_msg = f"Error syncing league {league_key} for preset {preset.name}: {e}"
                logger.error(error_msg)
                notif_manager = NotificationManager(db)
                await notif_manager.send_error_notification(f"Sync Preset Failed ({preset.name})", error_msg)
        
        logger.info(f"Completed sync for preset: {preset.name}")

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

    async def _process_odds_data(self, db: AsyncSession, odds_data: List[Dict[str, Any]]):
        for event_data in odds_data:
            bookmaker_event_id = event_data["id"]  # External ID from bookmaker
            commence_time = datetime.fromisoformat(event_data["commence_time"].replace("Z", "+00:00"))
            
            # The-Odds-API 'sport_key' in odds response IS the league slug (e.g. 'soccer_epl')
            league_slug = event_data["sport_key"]
            
            # Lookup league to get the actual parent sport key (e.g. 'soccer')
            league = await db.get(League, league_slug)
            parent_sport_key = league.sport_key if league else league_slug
            
            home_team = event_data["home_team"]
            away_team = event_data["away_team"]
            
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
                   "home_team": event_data["home_team"],
                   "away_team": event_data["away_team"],
                   "sport_key": parent_sport_key,
                   "league_key": league_slug
               })
            else:
                await self.event_repo.create(db, obj_in={
                    "id": event_id,
                    "sport_key": parent_sport_key,
                    "league_key": league_slug,
                    "commence_time": commence_time,
                    "home_team": event_data["home_team"],
                    "away_team": event_data["away_team"]
                })
            
            for b_data in event_data.get("bookmakers", []):
                bk_key = b_data["key"]
                bk_title = b_data["title"]
                last_update = datetime.fromisoformat(b_data["last_update"].replace("Z", "+00:00"))
                
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
                
                for m_data in b_data.get("markets", []):
                    m_key = m_data["key"]

                    # NOTE We skip _lay markets for now, as we expect most users will be backing
                    if "_lay" in m_key:
                        continue
                    
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
                    
                    for outcome in m_data.get("outcomes", []):
                        price = outcome["price"]
                        name = outcome["name"]
                        point = outcome.get("point")
                        
                        # Extract Links (Priority: Outcome > Market > Bookmaker(Event))
                        url = outcome.get("link") 
                        if not url:
                           url = m_data.get("link")
                        if not url:
                           url = b_data.get("link")
                        
                        # Extract SIDs
                        outcome_sid = outcome.get("sid")
                        market_sid = m_data.get("sid")
                        event_sid = b_data.get("sid")
                        
                        # Extract Bet Limits (Priority: Outcome > Market)
                        bet_limit = outcome.get("limit")
                        if bet_limit is None:
                            bet_limit = m_data.get("limit")

                        normalized_name = name
                        if self.standardizer:
                            # Standardize selection name using bookmaker as source
                            # Pass event context for home/away team matching
                            normalized_name = await self.standardizer.standardize(
                                db, bk_key, "selection", name,
                                context={
                                    "home_team": event_data["home_team"],
                                    "away_team": event_data["away_team"],
                                    "market_key": m_key
                                }
                            )
                        
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
                            existing_odd.normalized_selection = normalized_name # Update normalization just in case
                            # TimestampMixin should handle updated_at automatically on commit if the object is dirty
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
