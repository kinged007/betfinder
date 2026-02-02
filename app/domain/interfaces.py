
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.db.models import Sport, League, Event, Odds, Bet

class AbstractBookmaker(ABC):
    name: str = ""
    title: str = ""

    def __init__(self, key: str, config: Dict[str, Any]):
        self.key = key
        self.config = config

    @classmethod
    def get_config_schema(cls) -> List[Dict[str, Any]]:
        """Return the configuration schema for this bookmaker."""
        return [
            {"name": "starting_balance", "label": "Starting Balance", "type": "float", "default": 0.0},
            {"name": "commission", "label": "Commission (%)", "type": "float", "default": 0.0},
        ]

    @abstractmethod
    async def obtain_sports(self) -> List[Dict[str, Any]]:
        """Fetch sports available on the bookmaker."""
        pass

    @abstractmethod
    async def obtain_odds(
        self, 
        sport_key: str, 
        event_ids: List[str], 
        log: Optional[Any] = None  # Callable[[str], None]
    ) -> List[Dict[str, Any]]:
        """Fetch odds for specific events."""
        pass

    @abstractmethod
    async def place_bet(self, bet: Bet) -> Dict[str, Any]:
        """Place a bet on the bookmaker."""
        pass

    @abstractmethod
    async def get_account_balance(self) -> Dict[str, Any]:
        """Get account balance."""
        pass
    
    @abstractmethod
    def standardize_sport_key(self, external_key: str) -> str:
        """Convert external sport key to internal standard."""
        pass

    @abstractmethod
    def standardize_team_name(self, team_name: str) -> str:
        """Standardize team names for matching."""
        pass

    @abstractmethod
    async def authorize(self) -> bool:
        """Authenticate with the bookmaker API."""
        pass

    @abstractmethod
    async def fetch_events(self, sport_key: str) -> List[Dict[str, Any]]:
        """Fetch matches/events for a given sport."""
        pass

    @abstractmethod
    async def fetch_markets(self, event_id: str) -> List[Dict[str, Any]]:
        """Fetch available markets for an event."""
        pass

    @abstractmethod
    async def get_order_status(self, external_id: str) -> Dict[str, Any]:
        """Check status of a placed bet."""
        pass

    @abstractmethod
    async def get_bet_settlement(self, bet: Bet) -> Dict[str, Any]:
        """
        Obtain the outcome and settlement value for a bet.
        Should return a dict with: 'status' (won, lost, void, open) and 'payout' (float).
        """
        pass

    @abstractmethod
    async def get_event_results(self, event_id: str) -> List[Dict[str, Any]]:
        """
        Fetch results for an event.
        Returns list of dicts with: {'market_key': str, 'selection': str, 'result': str}
        """
        pass

    async def test_connection(self) -> bool:
        """Test connection to the bookmaker API."""
        return False
