
from datetime import datetime, timedelta, timezone
import logging
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.services.the_odds_api import TheOddsAPIClient
from app.services.standardizer import DataStandardizer
from app.db.models import Sport, League, Event, Market, Odds, Bookmaker
from app.repositories.base import BaseRepository
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
        # Logic: If DB has sports and the most recent valid update was < 7 days ago, skip.
        # But Sport table uses TimestampMixin (updated_at).
        
        # Check count
        result = await db.execute(select(Sport).limit(1))
        first_sport = result.scalars().first()
        
        should_sync = False
        if not first_sport:
             logger.info("Sports table empty. Sync required.")
             should_sync = True
        else:
             # Check age. Assuming if one is old, we resync all or check metadata. 
             # Simplest: Check if last successful sync was recent.
             # Since we don't store "Last Sync Job", we check the updated_at of a sport.
             if first_sport.updated_at:
                 # Ensure updated_at is timezone-aware
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

        logger.info("Fetching sports from API...")
        data = await self.api_client.get_sports()
        logger.info(f"Fetched {len(data)} sports from API.")
        
        for item in data:
            key = item["key"]
            group = item["group"]
            title = item["title"]
            active = item["active"]
            has_outrights = item["has_outrights"]
            
            sport_key = group.lower().replace(" ", "")
            
            existing_sport = await self.sport_repo.get(db, sport_key)
            if not existing_sport:
                logger.debug(f"Creating new sport: {sport_key}")
                existing_sport = await self.sport_repo.create(db, obj_in={
                    "key": sport_key,
                    "title": group,
                    "group": group,
                    "active": True
                })
            
            # Update Leagues? logic remains same...
            
            existing_league = await self.league_repo.get(db, key)
            if existing_league:
                 # Update timestamp implicitly via onupdate in DB or explicit touch?
                 # BaseRepository update updates timestamp if Mixin works.
                 await self.league_repo.update(db, db_obj=existing_league, obj_in={
                     "active": active,
                     "title": title,
                     "group": group,
                     "has_outrights": has_outrights, # fixed field name
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
        
        logger.info("sync_sports completed.")

    async def sync_odds(self, db: AsyncSession, sport_key: str):
        odds_data = await self.api_client.get_odds(sport_key)
        await self._process_odds_data(db, odds_data)

    async def sync_bookmakers(self, db: AsyncSession, regions: str = None):
        if regions is None:
            regions = settings.THE_ODDS_API_REGIONS
        logger.info(f"Starting get_bookmakers for regions: {regions}...")
        bookmakers_data = await self.api_client.get_bookmakers(regions=regions)
        logger.info(f"Fetched {len(bookmakers_data)} bookmakers.")
        await self._process_odds_data(db, bookmakers_data) # process_odds data adds bookmakers too
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

        # Get list of active bookmaker keys from db for this preset
        result = await db.execute(select(Bookmaker.key).where(Bookmaker.active == True))
        bookmaker_keys = result.scalars().all()
        logger.info(f"Syncing data for Preset: {preset.name} with bookmakers: {bookmaker_keys}")
        
        # We iterate through leagues because The-Odds-API is primarily league-based (sport_key).
        for league_key in leagues:
            try:
                logger.info(f"Fetching odds for league: {league_key} (Preset: {preset.name})")
                odds_data = await self.api_client.get_odds(
                    sport_key=league_key,
                    regions=regions,
                    markets=markets,
                    bookmakers= ",".join(bookmaker_keys)
                )
                
                # We could filter by time window here if the API doesn't support it,
                # but _process_odds_data handles basic event creation/update.
                # The filtering logic for the trade feed itself is in TradeFinderService.
                await self._process_odds_data(db, odds_data)
                
            except Exception as e:
                logger.error(f"Error syncing league {league_key} for preset {preset.name}: {e}")
        
        logger.info(f"Completed sync for preset: {preset.name}")

        logger.info(f"Completed sync for preset: {preset.name}")

    async def _process_odds_data(self, db: AsyncSession, odds_data: List[Dict[str, Any]]):
        for event_data in odds_data:
            event_id = event_data["id"]
            commence_time = datetime.fromisoformat(event_data["commence_time"].replace("Z", "+00:00"))
            
            # The-Odds-API 'sport_key' in odds response IS the league slug (e.g. 'soccer_epl')
            league_slug = event_data["sport_key"]
            
            # Lookup league to get the actual parent sport key (e.g. 'soccer')
            league = await db.get(League, league_slug)
            parent_sport_key = league.sport_key if league else league_slug
            
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
