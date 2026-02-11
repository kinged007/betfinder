
import httpx
from typing import List, Dict, Any, Optional
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

from app.schemas.odds import OddsEvent, OddsBookmaker, OddsMarket, OddsOutcome, OddsSport
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.standardizer import DataStandardizer

class TheOddsAPIClient:
    BASE_URL = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key: str = settings.THE_ODDS_API_KEY):
        self.api_key = api_key

    async def _get(self, endpoint: str, params: Dict[str, Any] = {}) -> Any:
        params["apiKey"] = self.api_key
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.BASE_URL}{endpoint}", params=params)
            if response.status_code >= 400:
                try:
                    error_data = response.json()
                    message = error_data.get("message", response.text)
                    # Helper to get other details if available
                    if "details" in error_data:
                         message += f" (Details: {error_data['details']})"
                except Exception:
                    message = response.text
                
                logger.error(f"TheOddsAPI Error {response.status_code}: {message}")
                raise Exception(f"TheOddsAPI Error {response.status_code}: {message}")
                
            return response.json()

    async def get_sports(self) -> List[OddsSport]:
        """
        Returns a list of active sports and leagues.
        """
        raw_data = await self._get("/sports")
        return [
            OddsSport(
                key=item["key"],
                group=item["group"],
                title=item["title"],
                active=item["active"],
                has_outrights=item["has_outrights"]
            )
            for item in raw_data
        ]

    async def get_odds(
        self, 
        sport_key: str = "upcoming", 
        regions: str = settings.THE_ODDS_API_REGIONS, 
        markets: str = "h2h",
        bookmakers: Optional[str] = None,
        commence_from: Optional[str] = None, # ISO 8601 format (e.g. 2026-01-29T10:00:00Z)
        commence_to: Optional[str] = None, # ISO 8601 format (e.g. 2026-01-29T10:00:00Z)
        event_ids: Optional[str] = None, # Comma separated list of event IDs
        standardizer: Optional[DataStandardizer] = None,
        db: Optional[AsyncSession] = None
    ) -> List[OddsEvent]:
        """
        Returns a list of upcoming events and their odds for a given sport.
        """
        params = {
            "regions": regions,
            "markets": markets,
            "oddsFormat": "decimal",
            "dateFormat": "iso",
            "includeLinks": True,
            "includeSids": True,
            "includeBetLimits": True,
            "includeRotationNumbers": True,
        }
        if bookmakers:
            if "pinnacle" not in bookmakers.lower():
                bookmakers = "pinnacle," + bookmakers
            params["bookmakers"] = bookmakers
        if commence_from:
            params["commenceFrom"] = commence_from
        if commence_to:
            params["commenceTo"] = commence_to
        if event_ids:
            params["eventIds"] = event_ids
        raw_data = await self._get(f"/sports/{sport_key}/odds", params=params)
        
        # Convert to Pydantic Models and Standardize
        odds_events = []
        for event in raw_data:
            # Basic Event Info
            commence_time = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
            
            bookmakers_list = []
            for b_data in event.get("bookmakers", []):
                markets_list = []
                for m_data in b_data.get("markets", []):
                    # NOTE We skip _lay markets for now, as we expect most users will be backing
                    if "_lay" in m_data["key"]:
                        continue

                    outcomes_list = []
                    for outcome in m_data.get("outcomes", []):
                        sel_name = outcome["name"]
                        norm_name = sel_name # Default
                        
                        if standardizer and db:
                            # Standardize selection name
                            norm_name = await standardizer.standardize(
                                db, b_data["key"], "selection", sel_name,
                                context={
                                    "home_team": event["home_team"],
                                    "away_team": event["away_team"],
                                    "market_key": m_data["key"]
                                }
                            )
                        
                        outcomes_list.append(OddsOutcome(
                            selection=sel_name,
                            normalized_selection=norm_name,
                            price=outcome["price"],
                            point=outcome.get("point"),
                            url=outcome.get("link"),
                            sid=outcome.get("sid"),
                            bet_limit=outcome.get("limit")
                        ))
                    
                    if not outcomes_list:
                        continue

                    markets_list.append(OddsMarket(
                        key=m_data["key"],
                        outcomes=outcomes_list,
                        sid=m_data.get("sid"),
                        link=m_data.get("link"),
                        last_update=datetime.fromisoformat(b_data["last_update"].replace("Z", "+00:00")) if b_data.get("last_update") else None
                    ))
                
                if not markets_list:
                    continue

                bookmakers_list.append(OddsBookmaker(
                    key=b_data["key"],
                    title=b_data["title"],
                    markets=markets_list,
                    last_update=datetime.fromisoformat(b_data["last_update"].replace("Z", "+00:00")),
                    sid=b_data.get("sid"),
                    link=b_data.get("link")
                ))

            odds_events.append(OddsEvent(
                id=event["id"],
                sport_key=event["sport_key"],
                sport_title=event["sport_title"],
                commence_time=commence_time,
                home_team=event["home_team"],
                away_team=event["away_team"],
                bookmakers=bookmakers_list
            ))
            
        return odds_events

    async def get_events(
        self, 
        sport_key: str, 
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Returns a list of events (fixtures) without odds.
        """
        params = {}
        # The odds-api doesn't always support strict date filtering on this endpoint in free tier or standardized way like this, 
        # but check documentation. Usually fetching odds gives events. 
        # But there is specific /events endpoint? Check docs or assume odds endpoint is primary source.
        # Actually /sports/{sport}/events exists.
        return await self._get(f"/sports/{sport_key}/events", params=params)

    async def get_bookmakers(
        self,
        regions: str = settings.THE_ODDS_API_REGIONS,
        markets: str = "h2h,spreads,totals",
    ) -> List[Dict[str, Any]]:
        """
        Returns bookmakers (which includes odds) for all upcoming sports - simplest method to obtain bookmakers for regions
        """
        return await self.get_odds(sport_key="upcoming", regions=regions, markets=markets)
