
import httpx
import asyncio
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.domain.interfaces import AbstractBookmaker
from app.core.enums import BetResult, BetStatus
from app.db.models import Bet

class SimpleBookmaker(AbstractBookmaker):
    name = "simple"
    title = "Simple Bookmaker"

    async def obtain_sports(self) -> List[Dict[str, Any]]:
        return []

    async def obtain_odds(
        self, 
        sport_key: str, 
        event_ids: List[str], 
        log: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        return []

    async def place_bet(self, bet: Bet) -> Dict[str, Any]:
        return {"status": "error", "message": "Placing bets not supported by SimpleBookmaker"}

    async def get_account_balance(self) -> Dict[str, Any]:
        return {"balance": 0.0, "currency": "USD"}
    
    def standardize_sport_key(self, external_key: str) -> str:
        return external_key

    def standardize_team_name(self, team_name: str) -> str:
        return team_name

    async def authorize(self) -> bool:
        return True

    async def fetch_events(self, sport_key: str) -> List[Dict[str, Any]]:
        return []

    async def fetch_markets(self, event_id: str) -> List[Dict[str, Any]]:
        return []

    async def get_order_status(self, external_id: str) -> Dict[str, Any]:
        return {"status": "unknown", "message": "Method not implemented for SimpleBookmaker"}

    async def get_bet_settlement(self, bet: Bet) -> Dict[str, Any]:
        """Simple bookmaker doesn't support auto-settlement."""
        return {"status": bet.status, "payout": bet.payout or 0.0}

    async def get_event_results(self, event_id: str) -> List[Dict[str, Any]]:
        return []

    async def get_events_results(self, event_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Fetch results for multiple events.
        Default implementation iterates and calls single-event method.
        Subclasses should override for batch optimization.
        """
        all_results = []
        for event_id in event_ids:
            try:
                results = await self.get_event_results(event_id)
                all_results.extend(results)
            except Exception as e:
                # Log error but continue with other events
                print(f"Error fetching results for event {event_id}: {e}")
        return all_results

class APIBookmaker(SimpleBookmaker):
    auth_type: str = "Bearer" # "Bearer", "ApiKey", "Basic", or None
    base_url: str = ""
    api_token: str = ""
    requests_per_second: float = 5.0 # Default rate limit for general API requests
    odds_per_second: float = 1.0 # Default rate limit for odds fetching (can be lower due to pricing)
    pre_game_odds: bool = True # Whether bookmaker provides pre-game odds
    live_odds: bool = False # Whether bookmaker provides live odds
    db: Optional[Any] = None

    def __init__(self, key: str, config: Dict[str, Any], db: Optional[Any] = None):
        super().__init__(key, config)
        self.api_token = config.get("api_token", "") or config.get("api_key", "")
        self.db = db
        self._rate_limiter = None
        self._last_request_time = 0
        self._last_sync_times: Dict[str, datetime] = {} # {event_id: last_sync_time}
        self._last_odds_sync: float = 0 # Global timestamp for odds rate limiting

    def should_sync_event(self, event_id: str, commence_time: datetime) -> bool:
        """Determines if an event should be synced based on its start time and last sync."""
        
        last_sync = self._last_sync_times.get(event_id)
        if not last_sync:
            return self._check_odds_rate_limit()
            
        now = datetime.utcnow()
        time_to_event = commence_time - now
        
        # Throttling Rules:
        # 1. If event is in more than 12 hours -> Sync every 1 hour
        if time_to_event > timedelta(hours=12):
            if (now - last_sync) > timedelta(hours=1):
                return self._check_odds_rate_limit()
            return False
            
        # 2. If event is in 6-12 hours -> Sync every 10 minutes
        if time_to_event > timedelta(hours=6):
            if (now - last_sync) > timedelta(minutes=10):
                return self._check_odds_rate_limit()
            return False
            
        # 3. If event is in less than 6 hours or live -> Sync on every run, but respect rate limit
        if not self.live_odds:
            return self._check_odds_rate_limit()
        
        return True

    def _check_odds_rate_limit(self) -> bool:
        """Check if we can make an odds request based on odds_per_second rate limit."""
        now = time.time()
        elapsed = now - self._last_odds_sync
        min_interval = 1.0 / self.odds_per_second
        return elapsed >= min_interval

    def record_sync(self, event_id: str):
        """Record the timestamp of a successful sync for an event."""
        self._last_sync_times[event_id] = datetime.utcnow()
        self._last_odds_sync = time.time()
    
    def has_credentials(self) -> bool:
        """
        Check if the bookmaker has valid credentials configured.
        
        Checks for:
        - API token (api_token or api_key)
        - Username + Password combination
        - Session token (for bookmakers that authenticate via login)
        """
        # Check for API token
        if self.api_token:
            return True
        
        # Check for username + password
        username = self.config.get("username")
        password = self.config.get("password")
        if username and password:
            return True
        
        # Check for session token (some bookmakers store this after login)
        session_token = self.config.get("session_token")
        if session_token:
            return True
        
        return False

    def _get_rate_limiter(self):
        if self._rate_limiter is None:
            # Simple semaphore-based or sleep-based limiter
            self._rate_limiter = asyncio.Semaphore(1) 
        return self._rate_limiter

    async def make_request(
        self, 
        method: str, 
        endpoint: str, 
        data: Optional[Dict[str, Any]] = None, 
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None,
        use_auth: bool = True
    ) -> Any:

        # 1. Rate Limiting
        async with self._get_rate_limiter():
            now = time.time()
            elapsed = now - self._last_request_time
            delay = (1.0 / self.requests_per_second) - elapsed
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request_time = time.time()

        # 2. Prepare URL and Headers
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        full_headers = headers.copy() if headers else {}
        
        if use_auth and self.api_token:
            if self.auth_type == "Bearer":
                full_headers["Authorization"] = f"Bearer {self.api_token}"
            elif self.auth_type == "ApiKey":
                full_headers["X-API-Key"] = self.api_token
            # Add more types as needed

        # 3. Execution
        proxy = self.config.get("proxy", None)
        if proxy:
            proxy = {"http": proxy, "https": proxy}
        else:
            proxy = None
        async with httpx.AsyncClient(proxy=proxy) as client:
            try:
                res = await client.request(
                    method=method,
                    url=url,
                    json=data,
                    params=params,
                    headers=full_headers,
                    timeout=30.0
                )
                res.raise_for_status()
                return res
            except httpx.HTTPStatusError as e:
                # Log or handle specific errors
                raise e
            except Exception as e:
                raise e

    @classmethod
    def get_config_schema(cls) -> List[Dict[str, Any]]:
        schema = super().get_config_schema()
        schema.extend([
            {"name": "username", "label": "Username", "type": "str"},
            {"name": "password", "label": "Password", "type": "password"},
            {"name": "api_token", "label": "API Token", "type": "password"},
            {"name": "has_2fa", "label": "Has 2FA", "type": "bool", "default": False},
            {"name": "bet_delay_seconds", "label": "Auto-Bet Delay (Seconds)", "type": "int", "default": 30},
            {"name": "currency", "label": "Currency", "type": "str", "default": "USD"},
            {"name": "account_id", "label": "Account ID", "type": "str", "default": ""},
            {"name": "proxy", "label": "Proxy (URL)", "type": "str", "default": ""},
            {"name": "use_for_results", "label": "Use for Results", "type": "bool", "default": False}
        ])
        return schema

    async def test_connection(self) -> bool:
        return await self.authorize()

    async def get_bet_settlement(self, bet: Bet) -> Dict[str, Any]:
        """
        Obtain the outcome and settlement value for a bet.
        Shared logic in base class calls bookmaker-specific status and payout methods.
        If a bookmaker cannot determine the result, it returns the current bet status.
        """
        status = await self.obtain_bet_status(bet)
        payout = await self.obtain_bet_payout(bet)
        
        # Fallback logic for payout if status changed but payout is missing
        if status == BetResult.WON.value and (payout is None or payout == 0.0):
            payout = bet.stake * bet.price 
        elif status == BetResult.VOID.value and (payout is None or payout == 0.0):
            payout = bet.stake
        elif status == BetResult.LOST.value:
            payout = -bet.stake
            
        return {
            "status": status,
            "payout": payout if payout is not None else 0.0
        }

    async def obtain_bet_status(self, bet: Bet) -> str:
        """
        Fetch the status of the bet from the API.
        Default implementation tries external_id first.
        Subclasses can override to check market results via sid/market_sid.
        """
        if bet.external_id:
            result = await self.get_order_status(bet.external_id)
            return result.get("status", bet.status)
        return bet.status

    async def obtain_bet_payout(self, bet: Bet) -> float:
        """
        Fetch the settlement value (payout) of the bet from the API.
        Default implementation tries external_id first.
        """
        if bet.external_id:
            result = await self.get_order_status(bet.external_id)
            payout = result.get("payout")
            if payout is not None:
                return float(payout)
        return bet.payout or 0.0

class BookmakerFactory:
    _registry = {}
    _instances: Dict[str, AbstractBookmaker] = {}

    @classmethod
    def register(cls, key: str, bookmaker_cls):
        cls._registry[key] = bookmaker_cls

    @classmethod
    def get_bookmaker(cls, key: str, config: Dict[str, Any] = {}, db: Optional[Any] = None) -> AbstractBookmaker:
        if key in cls._instances:
            instance = cls._instances[key]
            instance.config = config
            if isinstance(instance, APIBookmaker):
                instance.db = db
            return instance
            
        bookmaker_cls = cls._registry.get(key, SimpleBookmaker)
        if issubclass(bookmaker_cls, APIBookmaker):
            instance = bookmaker_cls(key, config, db)
        else:
            instance = bookmaker_cls(key, config)
        cls._instances[key] = instance
        return instance

    @classmethod
    def get_registered_keys(cls) -> List[str]:
        return [k for k, v in cls._registry.items() if v != SimpleBookmaker and k != "simple"]

    @classmethod
    def get_all_schemas(cls) -> Dict[str, List[Dict[str, Any]]]:
        return {k: v.get_config_schema() for k, v in cls._registry.items()}

# Register SimpleBookmaker (default is handled in get_bookmaker logic, but we can register explicitly)
BookmakerFactory.register("simple", SimpleBookmaker)
