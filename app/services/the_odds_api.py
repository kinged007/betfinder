
import httpx
from typing import List, Dict, Any, Optional
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

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

    async def get_sports(self) -> List[Dict[str, Any]]:
        """
        Returns a list of available sports and leagues.
        """
        return await self._get("/sports")

    async def get_odds(
        self, 
        sport_key: str = "upcoming", 
        regions: str = settings.THE_ODDS_API_REGIONS, 
        markets: str = "h2h",
        bookmakers: Optional[str] = None,
        commence_from: Optional[str] = None, # ISO 8601 format (e.g. 2026-01-29T10:00:00Z)
        commence_to: Optional[str] = None, # ISO 8601 format (e.g. 2026-01-29T10:00:00Z)
        event_ids: Optional[str] = None, # Comma separated list of event IDs
    ) -> List[Dict[str, Any]]:
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
        return await self._get(f"/sports/{sport_key}/odds", params=params)

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
