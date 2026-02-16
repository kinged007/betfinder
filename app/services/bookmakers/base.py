
import httpx
import asyncio
import time
import difflib
import re
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.domain.interfaces import AbstractBookmaker
from app.core.enums import BetResult, BetStatus
from app.db.models import Bet, Mapping, League
from app.schemas.odds import OddsEvent, OddsSport

# --- Fuzzy Matching Helpers ---

COUNTRY_SYNONYMS = {
    "dutch": "netherlands",
    "french": "france",
    "german": "germany",
    "spanish": "spain",
    "italian": "italy",
    "english": "england",
    "portuguese": "portugal",
    "brazilian": "brazil",
    "russian": "russia",
    "belgian": "belgium",
    "american": "usa",
}

def tokenize(s: str) -> List[str]:
    return [t for t in re.split(r'[^a-zA-Z0-9]+', s.lower()) if t]

def normalize_title(s: str) -> str:
    tokens = tokenize(s)
    normalized = []
    for t in tokens:
        normalized.append(COUNTRY_SYNONYMS.get(t, t))
    return " ".join(normalized)

def simple_ratio(s1: str, s2: str) -> float:
    return difflib.SequenceMatcher(None, s1.lower(), s2.lower()).ratio()

def token_sort_ratio(s1: str, s2: str) -> float:
    s1 = normalize_title(s1)
    s2 = normalize_title(s2)
    t1 = tokenize(s1)
    t2 = tokenize(s2)
    t1.sort()
    t2.sort()
    return difflib.SequenceMatcher(None, " ".join(t1), " ".join(t2)).ratio()

def token_set_ratio(s1: str, s2: str) -> float:
    s1 = normalize_title(s1)
    s2 = normalize_title(s2)
    t1 = set(tokenize(s1))
    t2 = set(tokenize(s2))
    
    intersection = t1.intersection(t2)
    if not intersection: return 0.0
    
    sorted_inter = " ".join(sorted(list(intersection)))
    sorted_t1 = " ".join(sorted(list(t1)))
    sorted_t2 = " ".join(sorted(list(t2)))
    
    vals = [
        difflib.SequenceMatcher(None, sorted_inter, sorted_t1).ratio(),
        difflib.SequenceMatcher(None, sorted_inter, sorted_t2).ratio(),
        difflib.SequenceMatcher(None, sorted_t1, sorted_t2).ratio()
    ]
    return max(vals)

class SimpleBookmaker(AbstractBookmaker):
    name = "simple"
    title = "Simple Bookmaker"

    async def obtain_sports(self) -> List[OddsSport]:
        return []

    async def obtain_odds(
        self, 
        league_key: str, 
        event_ids: List[str], 
        log: Optional[Any] = None,
        allowed_markets: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch odds for specific events and return as a FLAT LIST of Dictionary objects.
        Used by Trade Finder for live updates.
        
        Args:
            league_key: Internal league key
            event_ids: List of event IDs to fetch/update
            log: Optional logger
            allowed_markets: Optional list of market keys to filter
            
        Returns:
            List[Dict]: Flat list of odds updates.
        """
        return []

    async def fetch_league_odds(
        self, 
        league_key: str, 
        allowed_markets: Optional[List[str]] = None
    ) -> List[OddsEvent]:
        """
        Fetch odds for a complete league and return in TheOddsAPI-compatible format (List of Events).
        Used by Ingester for bulk sync of new events and full market refreshes.
        
        Args:
            league_key: Internal league key
            allowed_markets: Optional list of market keys to filter
            
        Returns:
            List[OddsEvent]: List of Event objects containing Bookmakers -> Markets -> Outcomes.
        """
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
    requests_per_second: float = 0.5 # Default rate limit for general API requests
    odds_per_second: float = 0.1 # Default rate limit for odds fetching (can be lower due to pricing)
    pre_game_odds: bool = True # Whether bookmaker provides pre-game odds
    live_odds: bool = False # Whether bookmaker provides live odds
    db: Optional[Any] = None
    unauthorized_codes = {401, 403}

    test_on: List[str] = ["api_token", "username"] # Fields that trigger "Test Connection" button availability

    def __init__(self, key: str, config: Dict[str, Any], db: Optional[Any] = None):
        super().__init__(key, config)
        self.api_token = config.get("api_token", "") or config.get("api_key", "")
        self.db = db
        self._rate_limiter = None
        self._last_request_time = 0
        self._last_sync_times: Dict[str, datetime] = {} # {event_id: last_sync_time}
        self._last_odds_sync: float = 0 # Global timestamp for odds rate limiting
        
        # Circuit Breaker Fields
        self._recent_errors: List[float] = [] # timestamps of errors
        self._circuit_open_until: float = 0
        self._error_threshold = 10 # failures
        self._error_window = 300 # seconds (5 mins)
        self._cool_off_duration = 3600 # seconds (1 hour)

    def should_sync_event(self, event_id: str, commence_time: datetime) -> bool:
        """Determines if an event should be synced based on its start time and last sync."""
        
        last_sync = self._last_sync_times.get(event_id)
        if not last_sync:
            return self._check_odds_rate_limit()
            
        now = datetime.now(timezone.utc)
        # Ensure commence_time is timezone-aware
        if commence_time.tzinfo is None:
            commence_time = commence_time.replace(tzinfo=timezone.utc)

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
        self._last_sync_times[event_id] = datetime.now(timezone.utc)
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

    async def _check_circuit_breaker(self):
        now = time.time()
        if now < self._circuit_open_until:
            wait_min = int((self._circuit_open_until - now) / 60)
            raise Exception(f"Circuit tripped. Cooling off for {wait_min} more minutes.")

    async def _handle_request_error(self, last_error: Optional[str] = None):
        now = time.time()
        self._recent_errors.append(now)
        
        # Prune old errors
        window_start = now - self._error_window
        self._recent_errors = [t for t in self._recent_errors if t > window_start]
        
        if len(self._recent_errors) >= self._error_threshold:
            # Trip Circuit
            self._circuit_open_until = now + self._cool_off_duration
            msg = f"API Circuit Breaker Tripped for {self.title} ({self.key}). Too many errors ({len(self._recent_errors)}) in last 5 mins. Pausing for 1 hour."
            
            if last_error:
                msg += f"\n\nLast Error Details:\n{last_error}"
                
            print(msg)
            
            # Attempt to notify
            if self.db:
                try:
                    from app.services.notifications.manager import NotificationManager
                    nm = NotificationManager(self.db)
                    await nm.send_error_notification(f"Circuit Breaker: {self.title}", msg)
                except Exception as e:
                    print(f"Failed to send circuit breaker notification: {e}")
            
            # Clear errors so it resets after cool-off
            self._recent_errors = []

    async def make_request(
        self, 
        method: str, 
        endpoint: str, 
        data: Optional[Dict[str, Any]] = None, 
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None,
        use_auth: bool = True,
        retry_auth: bool = True
    ) -> Any:

        # 0. Check Circuit Breaker
        await self._check_circuit_breaker()

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
        if "User-Agent" not in full_headers:
            full_headers["User-Agent"] = "Mozilla/5.0 (compatible; SportsBetFinder/1.0; +http://localhost)"
        
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
                
                # 4a. Auto-Reauthorization Attempt
                if res.status_code in self.unauthorized_codes and retry_auth:
                    print(f"Auth failed ({res.status_code}) for {self.key}. Attempting re-authorization...")
                    try:
                        auth_success = await self.authorize()
                        if auth_success:
                            print(f"Re-authorization successful for {self.key}. Retrying request...")
                            # Retry request with updated credentials (self.api_token updated by authorize)
                            return await self.make_request(
                                method, endpoint, data, params, headers, use_auth, retry_auth=False
                            )
                        else:
                            print(f"Re-authorization failed for {self.key}.")
                    except Exception as auth_error:
                        print(f"Error during re-authorization for {self.key}: {auth_error}")

                # Check for 5xx errors for Circuit Breaker? 
                # For now, we only trip on connection errors or if we explicitly decide to.
                # If user wants 400 details, we simply return the response.
                
                return res

            except httpx.HTTPStatusError as e:
                # This catches raise_for_status() which we removed, so this block is technically checking nothing 
                # unless we left it. But we want to handle failures manually.
                # However, client.request might raise other HTTP errors? No, only RequestError.
                # We can remove this specific catch or leave it for safety if we ever add raise_for_status back.
                # We'll just catch generic Exception to be safe for connection issues.
                raise e

            except Exception as e:
                # Trigger circuit breaker logic for connection errors
                detailed_error = f"{type(e).__name__}: {str(e)}"
                await self._handle_request_error(last_error=detailed_error)

                print(f"Exception in make_request for {url}: {detailed_error}")
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

    async def resolve_mapping(
        self, 
        mapping_type: str, 
        external_id: str, 
        external_name: str, 
        group: str
    ) -> Optional[str]:
        """
        Resolve an external ID to an internal key using the mapping table.
        If no mapping exists, attempt fuzzy matching and create a new mapping.
        
        Args:
            mapping_type: Type of mapping ('league', 'market', etc.)
            external_id: External identifier from this bookmaker
            external_name: Human-readable name from this bookmaker
            group: Category/group for fuzzy matching (e.g., sport name)
            
        Returns:
            Internal key if mapped/matched, None if PENDING
        """
        if not self.db:
            # If no DB access, cannot resolve mappings
            return None
            
        # Check for existing mapping
        result = await self.db.execute(
            select(Mapping).where(
                Mapping.source == self.key,
                Mapping.type == mapping_type,
                Mapping.external_key == external_id
            )
        )
        existing_mapping = result.scalar_one_or_none()
        
        if existing_mapping:
            if existing_mapping.internal_key == "PENDING":
                return None
            return existing_mapping.internal_key
        
        # No mapping exists - attempt fuzzy match
        if mapping_type == 'league':
            return await self._fuzzy_match_league(external_id, external_name, group)
        
        # For other types, mark as PENDING for now
        new_mapping = Mapping(
            source=self.key,
            type=mapping_type,
            external_key=external_id,
            internal_key="PENDING",
            external_name=external_name
        )
        self.db.add(new_mapping)
        await self.db.commit()
        return None
    
    async def _fuzzy_match_league(
        self, 
        external_id: str, 
        external_name: str, 
        group: str
    ) -> Optional[str]:
        """
        Attempt to fuzzy match a league name to an existing internal league.
        """
        # Fetch all leagues for this sport/group
        result = await self.db.execute(
            select(League).where(League.group == group)
        )
        candidates = result.scalars().all()
        
        if not candidates:
            # No candidates - mark as PENDING
            new_mapping = Mapping(
                source=self.key,
                type='league',
                external_key=external_id,
                internal_key="PENDING",
                external_name=external_name
            )
            self.db.add(new_mapping)
            await self.db.commit()
            return None
        
        best_match = None
        best_score = 0.0
        
        for cand in candidates:
            # Skip leagues from this same bookmaker
            if cand.key.startswith(f"{self.key}_"):
                continue
                
            # Calculate scores
            norm_source = normalize_title(external_name)
            norm_cand = normalize_title(cand.title)
            score_simple = difflib.SequenceMatcher(None, norm_source, norm_cand).ratio()
            score_sort = token_sort_ratio(external_name, cand.title)
            score_set = token_set_ratio(external_name, cand.title)
            
            # Prefer Simple/Sort, use Set only if Sort is decent
            effective_set = score_set if score_sort > 0.6 else 0.0
            current_best = max(score_simple, score_sort, effective_set)
            
            if current_best > best_score:
                best_score = current_best
                best_match = cand
        
        if best_score > 0.85 and best_match:
            # High confidence - auto-map
            new_mapping = Mapping(
                source=self.key,
                type='league',
                external_key=external_id,
                internal_key=best_match.key,
                external_name=external_name
            )
            self.db.add(new_mapping)
            await self.db.commit()
            print(f"[{self.key}] Auto-mapped '{external_name}' to '{best_match.title}' ({best_match.key}). Score: {best_score:.2f}")
            return best_match.key
        else:
            # Low confidence - mark as PENDING
            new_mapping = Mapping(
                source=self.key,
                type='league',
                external_key=external_id,
                internal_key="PENDING",
                external_name=external_name
            )
            self.db.add(new_mapping)
            await self.db.commit()
            print(f"[{self.key}] New unmapped league: '{external_name}' (ID: {external_id}). Marked PENDING.")
            return None
    
    async def get_external_id(
        self, 
        mapping_type: str, 
        internal_key: str
    ) -> Optional[str]:
        """
        Reverse lookup: Get external ID for a given internal key.
        
        Args:
            mapping_type: Type of mapping ('league', 'market', etc.)
            internal_key: Internal identifier
            
        Returns:
            External ID if mapping exists, None otherwise
        """
        if not self.db:
            return None
            
        result = await self.db.execute(
            select(Mapping).where(
                Mapping.source == self.key,
                Mapping.type == mapping_type,
                Mapping.internal_key == internal_key
            )
        )
        mapping = result.scalar_one_or_none()
        return mapping.external_key if mapping else None

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
        keys = [k for k, v in cls._registry.items() if v != SimpleBookmaker and k != "simple"]
        return keys

    @classmethod
    def get_registered_bookmakers_info(cls) -> List[Dict[str, str]]:
        """Return key, title, and model_type for all registered bookmaker classes (excluding SimpleBookmaker)."""
        results = []
        for key, bk_cls in cls._registry.items():
            if bk_cls == SimpleBookmaker or key == "simple":
                continue
            title = getattr(bk_cls, 'title', key)
            model_type = 'api' if issubclass(bk_cls, APIBookmaker) else 'simple'
            results.append({"key": key, "title": title, "model_type": model_type})
        return results

    @classmethod
    def get_all_schemas(cls) -> Dict[str, Dict[str, Any]]:
        schemas = {}
        for k, v in cls._registry.items():
            test_on = getattr(v, 'test_on', [])
            schemas[k] = {
                "fields": v.get_config_schema(),
                "test_on": test_on
            }
        return schemas

# Register SimpleBookmaker (default is handled in get_bookmaker logic, but we can register explicitly)
BookmakerFactory.register("simple", SimpleBookmaker)
