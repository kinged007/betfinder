"""
Kalshi bookmaker integration.

Kalshi (https://kalshi.com) is a regulated prediction-market exchange.
Their sports markets are binary (yes/no) grouped into:
  Series → Events → Markets

Real API facts (verified against live data):
  - Base URL : https://api.elections.kalshi.com/trade-api/v2
  - Auth     : None required for public market reads
  - Event title format: "Away at Home"  e.g. "Utah at Washington"
  - market.yes_sub_title: team name for the YES side  e.g. "Washington"
  - market.yes_ask: implied probability in cents (0–100)
    → decimal odds = 100 / yes_ask
  - market.status "active" = currently tradeable
  - Batch market fetch: GET /markets?tickers=T1,T2,...

OddsEvent mapping:
  - event_sid   = Kalshi event_ticker  e.g. "KXNBAGAME-26MAR05UTAWAS"
  - market_sid  = Kalshi market ticker  e.g. "KXNBAGAME-26MAR05UTAWAS-WAS"
  - sid         = "yes"  (we always track the yes-side price)
"""

import os
import uuid
import base64
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone

from app.services.bookmakers.base import APIBookmaker
from app.schemas.odds import OddsEvent, OddsBookmaker, OddsMarket, OddsOutcome, OddsSport
from app.services.bookmakers.kalshi_market_types import KalshiMarketType, SERIES_TO_LEAGUE
from app.domain.schemas import BetSlip


class KalshiBookmaker(APIBookmaker):
    name = "kalshi"
    title = "Kalshi"
    base_url = "https://api.elections.kalshi.com/trade-api/v2"
    auth_type = None          # Public API for market data reads
    requests_per_second = 10.0
    odds_per_second = 5.0
    pre_game_odds = True
    live_odds = False

    # Maximum number of market tickers in a single batch request
    BATCH_SIZE = 100

    def __init__(self, key: str, config: Dict[str, Any], db: Optional[Any] = None):
        super().__init__(key, config, db)
        self.api_token = config.get("api_token", "") or config.get("api_key", "")
        self.private_key_str = config.get("private_key", "")
        self.auth_type = "Kalshi" if (self.api_token and self.private_key_str) else None
        self._rsa_private_key = None
        
        if self.private_key_str:
            try:
                pk_content = self.private_key_str.encode('utf-8')
                self._rsa_private_key = serialization.load_pem_private_key(
                    pk_content, 
                    password=None, 
                    backend=default_backend()
                )
            except Exception as e:
                print(f"[kalshi] Error loading private key: {e}")

    @classmethod
    def get_config_schema(cls) -> List[Dict[str, Any]]:
        schema = super().get_config_schema()
        schema = [f for f in schema if f["name"] not in ("username", "password", "has_2fa")]
        schema.extend([
            {
                "name": "private_key",
                "label": "Private Key String",
                "type": "textarea",
                "default": "",
            },
        ])
        return schema

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
        full_headers = headers.copy() if headers else {}
        print(f"[kalshi] OUTGOING: {method.upper()} {endpoint} | Auth: {use_auth and bool(self._rsa_private_key)}")
        
        if use_auth and self._rsa_private_key and self.api_token:
            timestamp = str(int(datetime.now().timestamp() * 1000))
            path = "/trade-api/v2" + (endpoint if endpoint.startswith("/") else "/" + endpoint)
            path_without_query = path.split('?')[0]
            message = f"{timestamp}{method.upper()}{path_without_query}".encode('utf-8')
            signature_bytes = self._rsa_private_key.sign(
                message,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                hashes.SHA256()
            )
            signature = base64.b64encode(signature_bytes).decode('utf-8')
            
            full_headers['KALSHI-ACCESS-KEY'] = self.api_token
            full_headers['KALSHI-ACCESS-SIGNATURE'] = signature
            full_headers['KALSHI-ACCESS-TIMESTAMP'] = timestamp
        
        return await super().make_request(
            method=method,
            endpoint=endpoint,
            data=data,
            params=params,
            headers=full_headers,
            use_auth=False, # Auth handled here
            retry_auth=retry_auth
        )

    async def test_connection(self) -> bool:
        """Test connectivity. If auth is provided, tests authenticated balance route."""
        print(f"[kalshi] Running test_connection. Has Key: {bool(self._rsa_private_key)}, Has Token: {bool(self.api_token)}")
        try:
            if self._rsa_private_key and self.api_token:
                res = await self.make_request("GET", "/portfolio/balance", use_auth=True)
                print(f"[kalshi] Auth Test Response: {res.status_code}")
                if res.status_code != 200:
                    try:
                        print(f"[kalshi] Auth Test Error Details: {res.text}")
                    except:
                        pass
                return res.status_code == 200
            else:
                res = await self.make_request("GET", "/markets", params={"limit": 1}, use_auth=False)
                return res.status_code == 200
        except Exception as e:
            print(f"Kalshi connection test failed: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Sports discovery
    # ──────────────────────────────────────────────────────────────────────────

    async def obtain_sports(self) -> List[OddsSport]:
        """
        Fetch all Kalshi sports series and map them to internal league keys.
        Only game-level series (not championship/outright) are returned.
        """
        results: List[OddsSport] = []
        try:
            res = await self.make_request(
                "GET", "/series",
                params={"limit": 200, "category": "Sports"},
                use_auth=False
            )
            series_list = res.json().get("series", [])

            for s in series_list:
                ticker = s.get("ticker", "")
                title = s.get("title", "")
                tags = s.get("tags", [])

                # Infer sport group from tags (first tag, title-cased)
                group = tags[0] if tags else "Sports"

                internal_key = KalshiMarketType.series_to_league(ticker)
                if not internal_key:
                    # Dynamically resolve and store PENDING mappings for unmapped series in database
                    internal_key = await self.resolve_mapping(
                        mapping_type='league',
                        external_id=ticker,
                        external_name=title,
                        group=group
                    )
                    if not internal_key:
                        continue

                results.append(OddsSport(
                    key=internal_key,
                    group=group,
                    title=title,
                    active=True,
                    has_outrights=False,
                    details={"series_ticker": ticker},
                ))

        except Exception as e:
            print(f"[kalshi] Error fetching series: {e}")

        return results

    # ──────────────────────────────────────────────────────────────────────────
    # Bulk odds fetch (used by Ingester for full-league sync)
    # ──────────────────────────────────────────────────────────────────────────

    async def fetch_league_odds(
        self,
        league_key: str,
        allowed_markets: Optional[List[str]] = None,
    ) -> List[OddsEvent]:
        """
        Fetch all active events for a league from Kalshi and return them as
        OddsEvent objects (TheOddsAPI-compatible format).

        Strategy:
          1. Map league_key → Kalshi series ticker via predefined mapping or DB.
          2. GET /events?series_ticker=<ticker>&status=open&with_nested_markets=true
          3. For each event, build an OddsEvent from the nested markets.
        """
        series_ticker = KalshiMarketType.get_series_for_league(league_key)

        if not series_ticker and self.db:
            # Try DB mapping (supports custom/user-added mappings)
            series_ticker = await self.get_external_id("league", league_key)

        if not series_ticker:
            print(f"[kalshi] No series ticker found for league '{league_key}'")
            return []

        market_key = KalshiMarketType.series_to_market_key(series_ticker)
        if allowed_markets and market_key not in allowed_markets:
            return []

        try:
            res = await self.make_request(
                "GET", "/events",
                params={
                    "series_ticker": series_ticker,
                    "status": "open",
                    "limit": 200,
                    "with_nested_markets": "true",
                },
                use_auth=False,
            )
            events_data = res.json().get("events", [])
        except Exception as e:
            print(f"[kalshi] Error fetching events for series '{series_ticker}': {e}")
            return []

        # Persist series → league mapping so future calls skip this lookup
        if self.db:
            await self._ensure_series_mapping(series_ticker, league_key)

        return self._parse_events(events_data, league_key, market_key)

    def _parse_events(
        self,
        events_data: list,
        league_key: str,
        market_key: str,
    ) -> List[OddsEvent]:
        """Convert raw Kalshi event dicts into OddsEvent objects."""
        result: List[OddsEvent] = []

        for event_data in events_data:
            event_ticker = event_data.get("event_ticker", "")
            title = event_data.get("title", "")
            markets_data = event_data.get("markets", [])

            if not event_ticker or not markets_data:
                continue

            # Extract teams from event title ("Away at Home" format)
            home_team, away_team = KalshiMarketType.extract_teams_from_event_title(title)
            if not home_team or not away_team:
                # Fallback: try sub_title or skip
                print(f"[kalshi] Cannot parse teams from title: '{title}' – skipping")
                continue

            # Approximate game start time.
            # Kalshi provides expected_expiration_time (when game ends) but not
            # the exact start. We use it minus 3 hours as a reasonable proxy.
            expiration_str = (
                event_data.get("expected_expiration_time")
                or event_data.get("close_time", "")
            )
            try:
                expiration_dt = datetime.fromisoformat(
                    expiration_str.replace("Z", "+00:00")
                )
                commence_time = expiration_dt - timedelta(hours=3)
            except (ValueError, AttributeError):
                commence_time = datetime.now(timezone.utc)

            outcomes: List[OddsOutcome] = []

            for market in markets_data:
                yes_ask = market.get("yes_ask", 0)
                no_ask = market.get("no_ask", 0)
                yes_sub_title = market.get("yes_sub_title", "")
                ticker = market.get("ticker", "")
                status = market.get("status", "")
                floor_strike = market.get("floor_strike")
                
                # Point parsing
                point = None
                if floor_strike is not None:
                    point = float(floor_strike)

                # Skip markets with no liquidity or inactive
                if status not in ("active", "open"):
                    continue

                normalized_yes = KalshiMarketType.normalize_selection(
                    yes_sub_title, home_team, away_team, market_key
                )

                if yes_ask > 0:
                    price_yes = round(100 / yes_ask, 3)
                    
                    yes_point = point
                    if market_key == "spreads" and yes_point is not None:
                        yes_point = -yes_point # "wins by over X" means a minus spread

                    outcomes.append(OddsOutcome(
                        selection=yes_sub_title,
                        normalized_selection=normalized_yes,
                        price=price_yes,
                        point=yes_point,
                        sid=ticker,
                        market_sid=event_ticker,
                        event_sid=event_ticker,
                    ))

                if no_ask > 0:
                    price_no = round(100 / no_ask, 3)
                    
                    no_selection_name = ""
                    normalized_no = ""
                    no_point = point
                    
                    if market_key == "spreads":
                        # The No side of "Home -X" is "Away +X"
                        if normalized_yes == "home":
                            normalized_no = "away"
                            no_selection_name = away_team
                        elif normalized_yes == "away":
                            normalized_no = "home"
                            no_selection_name = home_team
                        if no_point is not None:
                            no_point = abs(no_point) # The No side takes the plus points
                    elif market_key == "totals":
                        # The No side of "Over X" is "Under X"
                        if normalized_yes == "over":
                            normalized_no = "under"
                            no_selection_name = "Under"
                        elif normalized_yes == "under":
                            normalized_no = "over"
                            no_selection_name = "Over"
                    elif market_key == "h2h":
                        # For H2H, typically there isn't a "No" side we explicitly map unless we map it to "Not X", 
                        # but standard odds require explicit teams/draw. We skip mapping 'No' for H2H for now 
                        # as it could be ambiguous in 3-way markets (No Home = Away OR Draw).
                        continue

                    if normalized_no:
                        outcomes.append(OddsOutcome(
                            selection=no_selection_name,
                            normalized_selection=normalized_no,
                            price=price_no,
                            point=no_point,
                            sid=ticker + "_no", # Unique SID for the NO side
                            market_sid=event_ticker,
                            event_sid=event_ticker,
                        ))

            if not outcomes:
                continue

            odds_event = OddsEvent(
                id=event_ticker,
                sport_key=league_key,
                sport_title=event_data.get("category", "Sports"),
                commence_time=commence_time,
                home_team=home_team,
                away_team=away_team,
                bookmakers=[
                    OddsBookmaker(
                        key=self.name,
                        title=self.title,
                        last_update=datetime.now(timezone.utc),
                        sid=event_ticker,
                        markets=[
                            OddsMarket(
                                key=market_key,
                                sid=event_ticker,
                                outcomes=outcomes,
                                last_update=datetime.now(timezone.utc),
                            )
                        ],
                    )
                ],
            )
            result.append(odds_event)

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Fast odds update via stored sids (used by TradeFinder)
    # ──────────────────────────────────────────────────────────────────────────

    async def obtain_odds(
        self,
        league_key: str,
        event_ids: Optional[List[str]] = None,
        log: Optional[Any] = None,
        allowed_markets: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Refresh odds for specific events using stored market_sid values.

        The fast-path avoids re-scanning all Kalshi events:
          1. Query Odds rows for the given internal event UUIDs.
          2. Extract stored market_sid (Kalshi market tickers).
          3. Batch GET /markets?tickers=T1,T2,...
          4. Return flat odds list with internal event UUIDs.
        """
        if not event_ids or not self.db:
            return []

        from app.db.models import Odds, Market, Event, Bookmaker
        from sqlalchemy import select

        # ── 1. Collect stored sid (market ticker) → internal UUID mapping ──────────────
        try:
            stmt = (
                select(Odds.sid, Odds.event_sid, Event.id, Event.home_team, Event.away_team)
                .select_from(Odds)
                .join(Market, Market.id == Odds.market_id)
                .join(Event, Event.id == Market.event_id)
                .join(Bookmaker, Bookmaker.id == Odds.bookmaker_id)
                .where(
                    Event.id.in_(event_ids),
                    Bookmaker.key == self.name,
                    Odds.sid.isnot(None),
                )
                .distinct()
            )
            res = await self.db.execute(stmt)
            rows = res.all()
        except Exception as e:
            print(f"[kalshi] Error querying stored sids: {e}")
            return []

        if not rows:
            print(f"[kalshi] No stored SIDs for events: {event_ids}")
            return []

        # market_ticker → internal event UUID
        ticker_to_uuid: Dict[str, str] = {}
        # market_ticker → internal event UUID
        ticker_to_uuid: Dict[str, str] = {}
        # market_ticker → Kalshi event ticker (for event_sid)
        ticker_to_event_sid: Dict[str, str] = {}
        # market_ticker → (home_team, away_team)
        ticker_to_teams: Dict[str, tuple[str, str]] = {}
        
        # We need the pure Kalshi tickers to make the API call
        pure_api_tickers = set()

        for db_sid, db_event_sid, internal_id, home, away in rows:
            if db_sid and db_sid != 'yes':  # Exclude placeholder 'yes' from old kalshi syncs
                ticker_to_uuid[db_sid] = str(internal_id)
                ticker_to_event_sid[db_sid] = db_event_sid or ""
                ticker_to_teams[db_sid] = (home, away)
                
                # If sid ends with _no, it's our internal construct. We strip it for the API query.
                pure_ticker = db_sid[:-3] if db_sid.endswith("_no") else db_sid
                pure_api_tickers.add(pure_ticker)

        if not ticker_to_uuid:
            return []

        # ── 2. Batch-fetch market prices ──────────────────────────────────────
        all_tickers = list(pure_api_tickers)
        fetched_markets: List[Dict] = []

        for i in range(0, len(all_tickers), self.BATCH_SIZE):
            batch = all_tickers[i: i + self.BATCH_SIZE]
            try:
                res = await self.make_request(
                    "GET", "/markets",
                    params={"tickers": ",".join(batch)},
                    use_auth=False,
                )
                fetched_markets.extend(res.json().get("markets", []))
            except Exception as e:
                print(f"[kalshi] Batch market fetch error: {e}")

        if not fetched_markets:
            return []

        # ── 3. Build flat odds list ───────────────────────────────────────────
        flat_odds: List[Dict[str, Any]] = []

        for market in fetched_markets:
            ticker = market.get("ticker", "")
            yes_ask = market.get("yes_ask", 0)
            no_ask = market.get("no_ask", 0)
            yes_sub_title = market.get("yes_sub_title", "")
            floor_strike = market.get("floor_strike")
            
            point = None
            if floor_strike is not None:
                point = float(floor_strike)

            # Infer market_key from series part of ticker (first segment)
            series_ticker = ticker.split("-")[0] if "-" in ticker else ticker
            market_type = KalshiMarketType.series_to_market_key(series_ticker)
            if allowed_markets and market_type not in allowed_markets:
                continue

            # Process Yes Side
            internal_uuid_yes = ticker_to_uuid.get(ticker)
            if internal_uuid_yes and yes_ask > 0:
                event_sid = ticker_to_event_sid.get(ticker, "")
                price_yes = round(100 / yes_ask, 3)
                home_team, away_team = ticker_to_teams.get(ticker, ("", ""))
                
                normalized_yes = KalshiMarketType.normalize_selection(
                    yes_sub_title, home_team, away_team, market_type
                )
                
                yes_point = point
                if market_type == "spreads" and yes_point is not None:
                    yes_point = -yes_point

                flat_odds.append({
                    "external_event_id": internal_uuid_yes,
                    "market_key": market_type,
                    "selection": yes_sub_title,
                    "normalized_selection": normalized_yes,
                    "price": price_yes,
                    "point": yes_point,
                    "sid": ticker,
                    "market_sid": event_sid,
                    "event_sid": event_sid,
                })
                
            # Process No Side
            ticker_no = ticker + "_no"
            internal_uuid_no = ticker_to_uuid.get(ticker_no)
            if internal_uuid_no and no_ask > 0:
                event_sid = ticker_to_event_sid.get(ticker_no, "")
                price_no = round(100 / no_ask, 3)
                home_team, away_team = ticker_to_teams.get(ticker_no, ("", ""))
                
                normalized_yes = KalshiMarketType.normalize_selection(
                    yes_sub_title, home_team, away_team, market_type
                )
                
                no_selection_name = ""
                normalized_no = ""
                no_point = point
                
                if market_type == "spreads":
                    if normalized_yes == "home":
                        normalized_no = "away"
                        no_selection_name = away_team
                    elif normalized_yes == "away":
                        normalized_no = "home"
                        no_selection_name = home_team
                    if no_point is not None:
                        no_point = abs(no_point)
                elif market_type == "totals":
                    if normalized_yes == "over":
                        normalized_no = "under"
                        no_selection_name = "Under"
                    elif normalized_yes == "under":
                        normalized_no = "over"
                        no_selection_name = "Over"

                if normalized_no:
                    flat_odds.append({
                        "external_event_id": internal_uuid_no,
                        "market_key": market_type,
                        "selection": no_selection_name,
                        "normalized_selection": normalized_no,
                        "price": price_no,
                        "point": no_point,
                        "sid": ticker_no,
                        "market_sid": event_sid,
                        "event_sid": event_sid,
                    })

        return flat_odds

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _ensure_series_mapping(self, series_ticker: str, league_key: str) -> None:
        """
        Persist a Kalshi series ticker → internal league key mapping in the DB
        so future calls resolve quickly via get_external_id().
        """
        from app.db.models import Mapping
        from sqlalchemy import select

        try:
            result = await self.db.execute(
                select(Mapping).where(
                    Mapping.source == self.name,
                    Mapping.type == "league",
                    Mapping.external_key == series_ticker,
                )
            )
            if not result.scalar_one_or_none():
                mapping = Mapping(
                    source=self.name,
                    type="league",
                    external_key=series_ticker,
                    internal_key=league_key,
                    external_name=series_ticker,
                )
                self.db.add(mapping)
                await self.db.commit()
        except Exception as e:
            print(f"[kalshi] Error saving series mapping: {e}")

    async def get_account_balance(self) -> Dict[str, Any]:
        if not self._rsa_private_key:
            return {"balance": 0.0, "currency": "USD"}
        try:
            res = await self.make_request("GET", "/portfolio/balance", use_auth=True)
            data = res.json()
            balance_cents = data.get("balance", 0)
            return {"balance": balance_cents / 100.0, "currency": "USD"}
        except Exception as e:
            print(f"[kalshi] Error fetching balance: {e}")
            return {"balance": 0.0, "currency": "USD"}

    async def place_bet(self, bet: Any) -> BetSlip:
        """
        Place a bet on Kalshi.
        bet: Bet object (app.db.models.Bet)
        """
        if not self._rsa_private_key:
            return BetSlip(status="error", status_message="Kalshi authentication not configured.", placed_at=datetime.now(timezone.utc), executed_stake=0.0, executed_price=0.0)
            
        try:
            yes_price = round(100 / bet.price)
            if yes_price < 1: yes_price = 1
            if yes_price > 99: yes_price = 99
                
            ticker = getattr(bet, 'sid', None)
            if not ticker and getattr(bet, 'odd_data', None):
                ticker = bet.odd_data.get('sid')
                
            if not ticker:
                return BetSlip(status="error", status_message="Missing market ticker (sid) for Kalshi bet.", placed_at=datetime.now(timezone.utc), executed_stake=0.0, executed_price=0.0)
            
            # --- Check Live Price ---
            try:
                market_res = await self.make_request("GET", "/markets", params={"tickers": ticker}, use_auth=False)
                if market_res.status_code == 200:
                    markets = market_res.json().get("markets", [])
                    if markets:
                        live_yes_ask = markets[0].get("yes_ask", 0)
                        if live_yes_ask > 0 and live_yes_ask != yes_price:
                            live_odds = round(100.0 / live_yes_ask, 3)
                            return BetSlip(
                                status="price_changed",
                                status_message=f"Market price changed from {yes_price}c (odds {round(bet.price, 2)}) to {live_yes_ask}c (odds {live_odds}). Please confirm new odds.",
                                placed_at=datetime.now(timezone.utc),
                                executed_stake=0.0,
                                executed_price=live_odds,
                            )
            except Exception as e:
                print(f"[kalshi] Live price check failed, continuing: {e}")
            
            # --- Continue with placing order ---
            count = int((bet.stake * 100) / yes_price)
            if count < 1: count = 1
            
            client_order_id = str(uuid.uuid4())
            
            payload = {
                "action": "buy",
                "client_order_id": client_order_id,
                "count": count,
                "side": "yes",
                "ticker": ticker,
                "type": "limit",
                "yes_price": yes_price
            }
            
            res = await self.make_request(
                "POST", 
                "/portfolio/orders", 
                data=payload, 
                use_auth=True
            )
            
            data = res.json()
            if "order" not in data:
                return BetSlip(status="error", status_message=f"Kalshi API Error: {res.text}", placed_at=datetime.now(timezone.utc), executed_stake=0.0, executed_price=0.0)
                
            order = data.get("order", {})
            
            # Exact execution tracking
            actual_yes_price = order.get("yes_price", yes_price)
            actual_count = order.get("count", count)
            
            fee_cents = 0
            for key in ["fee", "fees", "taker_fee", "taker_fees", "maker_fee", "maker_fees"]:
                val = order.get(key, 0)
                if isinstance(val, (int, float)):
                    fee_cents += val
            
            executed_stake = ((actual_count * actual_yes_price) + fee_cents) / 100.0
            executed_odds = round(100.0 / actual_yes_price, 3)
            
            return BetSlip(
                status="success",
                external_id=order.get("order_id"),
                status_message=order.get("status") or "Order placed",
                placed_at=datetime.now(timezone.utc),
                executed_stake=executed_stake,
                executed_price=executed_odds
            )
        except Exception as e:
            print(f"[kalshi] Error placing bet: {e}")
            return BetSlip(status="error", status_message=str(e), placed_at=datetime.now(timezone.utc), executed_stake=0.0, executed_price=0.0)

    async def get_order_status(self, external_id: str) -> Dict[str, Any]:
        """
        Check the status of an existing order using external_id.
        """
        if not self._rsa_private_key:
            return {"status": "unknown"}
        try:
            res = await self.make_request("GET", f"/portfolio/orders/{external_id}", use_auth=True)
            data = res.json()
            order = data.get("order", {})
            k_status = order.get("status")
            mapped = "open"
            if k_status == "executed":
                mapped = "settled"
            elif k_status in ("canceled", "expired"):
                mapped = "void"
                
            return {
                "status": mapped,
                "kalshi_status": k_status
            }
        except Exception as e:
            print(f"[kalshi] Error fetching order status: {e}")
            return {"status": "unknown"}

    async def get_event_results(self, event_id: str) -> List[Dict[str, Any]]:
        return []
