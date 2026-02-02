
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.mapping import MappingRepository

class DataStandardizer:
    def __init__(self, mapping_repo: MappingRepository):
        self.mapping_repo = mapping_repo

    async def standardize(
        self, db: AsyncSession, source: str, type: str, external_key: str, context: Optional[dict] = None
    ) -> str:
        """
        Convert external key to internal key.
        If no mapping exists, returns the external key (or could log missing mapping).
        Context can include event details like home_team, away_team for better normalization.
        """
        internal_key = await self.mapping_repo.get_internal_key(
            db, source, type, external_key
        )
        if internal_key:
            return internal_key
        
        # Fallback to default normalization logic if no DB mapping exists
        return self._default_normalize(type, external_key, context)

    def _default_normalize(self, type: str, external_key: str, context: Optional[dict] = None) -> str:
        """
        Handle common normalization for selections if no DB mapping is found.
        Uses context (home_team, away_team) to properly normalize team names to 'home' and 'away'.
        """
        if type != "selection":
            return external_key
            
        val = external_key.lower().strip()
        
        # First, check if we have event context for home/away team matching
        if context:
            home_team = context.get("home_team", "").strip()
            away_team = context.get("away_team", "").strip()
            market_key = context.get("market_key", "")
            
            # For H2H and spreads markets, match team names
            if market_key in ["h2h", "spreads"]:
                # Exact match (case-insensitive)
                if external_key.strip().lower() == home_team.lower():
                    return "home"
                if external_key.strip().lower() == away_team.lower():
                    return "away"
        
        # Generic H2H / Match Winner patterns
        if val in ["home", "1", "team 1", "team1"]: return "home"
        if val in ["away", "2", "team 2", "team2"]: return "away"
        if val in ["draw", "x", "the draw"]: return "draw"
        
        # Totals
        if val.startswith("over"): return "over"
        if val.startswith("under"): return "under"
        
        # If no match found, return original (this handles team names that couldn't be matched)
        return external_key

    async def learn_mapping(
        self, db: AsyncSession, source: str, type: str, external_key: str, internal_key: str
    ):
        """
        Add a new mapping to the database.
        """
        # Check if exists first
        existing = await self.mapping_repo.get_internal_key(db, source, type, external_key)
        if not existing:
            await self.mapping_repo.create(
                db, 
                obj_in={
                    "source": source,
                    "type": type,
                    "external_key": external_key,
                    "internal_key": internal_key
                }
            )
