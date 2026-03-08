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

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone

from app.services.bookmakers.base import APIBookmaker
from app.schemas.odds import OddsEvent, OddsBookmaker, OddsMarket, OddsOutcome, OddsSport
from app.services.bookmakers.kalshi_market_types import KalshiMarketType, SERIES_TO_LEAGUE


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
        # Optional: Kalshi API key for authenticated requests (higher rate limits)
        # When provided it will be sent as a Bearer token.
        if self.api_token:
            self.auth_type = "Bearer"

    @classmethod
    def get_config_schema(cls) -> List[Dict[str, Any]]:
        schema = super().get_config_schema()
        # Remove fields irrelevant for a read-only public API
        schema = [f for f in schema if f["name"] not in ("username", "password", "has_2fa")]
        schema.extend([
            {
                "name": "api_token",
                "label": "API Key (optional – increases rate limits)",
                "type": "password",
                "default": "",
            },
        ])
        return schema

    async def test_connection(self) -> bool:
        """Test connectivity by fetching one active market."""
        try:
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

                # Infer sport group from tags (first tag, title-cased)
                group = tags[0] if tags else "Sports"

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
                yes_sub_title = market.get("yes_sub_title", "")
                ticker = market.get("ticker", "")
                status = market.get("status", "")

                # Skip markets with no liquidity or inactive
                if yes_ask <= 0 or status not in ("active", "open"):
                    continue

                price = round(100 / yes_ask, 3)
                normalized = KalshiMarketType.normalize_selection(
                    yes_sub_title, home_team, away_team, market_key
                )

                outcomes.append(OddsOutcome(
                    selection=yes_sub_title,
                    normalized_selection=normalized,
                    price=price,
                    sid=ticker,
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
        # market_ticker → Kalshi event ticker (for event_sid)
        ticker_to_event_sid: Dict[str, str] = {}
        # market_ticker → (home_team, away_team)
        ticker_to_teams: Dict[str, tuple[str, str]] = {}

        for db_sid, db_event_sid, internal_id, home, away in rows:
            if db_sid and db_sid != 'yes':  # Exclude placeholder 'yes' from old kalshi syncs
                ticker_to_uuid[db_sid] = str(internal_id)
                ticker_to_event_sid[db_sid] = db_event_sid or ""
                ticker_to_teams[db_sid] = (home, away)

        if not ticker_to_uuid:
            return []

        # ── 2. Batch-fetch market prices ──────────────────────────────────────
        all_tickers = list(ticker_to_uuid.keys())
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
            yes_sub_title = market.get("yes_sub_title", "")

            internal_uuid = ticker_to_uuid.get(ticker)
            if not internal_uuid:
                continue
            if yes_ask <= 0:
                continue

            event_sid = ticker_to_event_sid.get(ticker, "")
            price = round(100 / yes_ask, 3)

            # Infer market_key from series part of ticker (first segment)
            series_ticker = ticker.split("-")[0] if "-" in ticker else ticker
            market_type = KalshiMarketType.series_to_market_key(series_ticker)
            if allowed_markets and market_type not in allowed_markets:
                continue

            home_team, away_team = ticker_to_teams.get(ticker, ("", ""))
            normalized_selection = KalshiMarketType.normalize_selection(
                yes_sub_title, home_team, away_team, market_type
            )

            flat_odds.append({
                "external_event_id": internal_uuid,
                "market_key": market_type,
                "selection": yes_sub_title,
                "normalized_selection": normalized_selection,
                "price": price,
                "point": None,
                "sid": ticker,
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
        return {"balance": 0.0, "currency": "USD"}

    async def place_bet(self, bet: Any) -> Dict[str, Any]:
        return {"status": "error", "message": "Kalshi bet placement not yet implemented"}

    async def get_order_status(self, external_id: str) -> Dict[str, Any]:
        return {"status": "unknown"}

    async def get_event_results(self, event_id: str) -> List[Dict[str, Any]]:
        return []
