from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import logging
from app.core.enums import BetResult
from app.services.bookmakers.base import APIBookmaker
from app.db.models import Odds, Market, Event
from sqlalchemy import select, update
from sqlalchemy.orm import joinedload
from app.db.models import Bet


class SmarketsBookmaker(APIBookmaker):
    name = "smarkets"
    title = "Smarkets"
    base_url = "https://api.smarkets.com/v3"
    requests_per_second = 1.0 # Smarkets allows more for general requests
    odds_per_second = 1/60 # Conservative rate for odds fetching. Allowed 50 per minute. We set to 1 request per minute  
    auth_type = "Bearer"
    live_odds = False
    
    @classmethod
    def get_config_schema(cls) -> List[Dict[str, Any]]:
        schema = super().get_config_schema()
        # Smarkets might need specific fields, eg. developer_app_key
        # schema.append({"name": "app_key", "label": "Developer App Key", "type": "str"})
        return schema

    def __init__(self, key: str, config: Dict[str, Any], db: Optional[Any] = None):
        super().__init__(key, config, db)
        self.base_url = "https://api.smarkets.com/v3"
        self._session_token = config.get("api_token")

    async def test_connection(self) -> bool:
        """Test connection to the bookmaker API."""
        try:
            res = await self.get_account_balance()
            if isinstance(res, dict) and "balance" in res:
                return True
            if res:
                return True
        except Exception as e:
            print(f"Smarkets test connection failed: {str(e)}")
        return False

    async def authorize(self) -> bool:
        """Handle Smarkets API authentication."""
        token_to_try = self._session_token or self.config.get("api_token")
        
        if token_to_try:
            self.api_token = token_to_try
            try:
                # Use /accounts/ to verify token via standardized make_request
                # Disable retry_auth to avoid infinite recursion if this check fails
                res = await self.make_request("GET", "/accounts/", retry_auth=False)
                if res.status_code == 200:
                    self._session_token = token_to_try
                    return True
            except Exception:
                pass # Token might be invalid or expired
        
        # fallback to login with username/password
        username = self.config.get("username")
        password = self.config.get("password")
        
        if not username or not password:
            return False

        try:
            payload = {
                "username": username,
                "password": password,
                "remember": True
            }
            # Disable retry_auth and use_auth (obviously) for login
            res = await self.make_request("POST", "/sessions/", data=payload, use_auth=False, retry_auth=False)
            if res.status_code == 200 or res.status_code == 201:
                data = res.json()
                self._session_token = data.get("token")
                self.api_token = self._session_token
                return True
        except Exception:
            pass
            
        return False

    async def fetch_events(self, sport_key: str) -> List[Dict[str, Any]]:
        """Fetch matches from Smarkets."""
        res = await self.make_request("GET", "/events/", params={"state": "upcoming", "type": "match"})
        return res.json().get("events", [])

    async def fetch_markets(self, event_id: str) -> List[Dict[str, Any]]:
        """Fetch markets for a Smarkets event."""
        res = await self.make_request("GET", f"/events/{event_id}/markets/")
        return res.json().get("markets", [])

    async def get_event_results(self, event_id: str) -> List[Dict[str, Any]]:
        """
        Fetch results for a single Smarkets event.
        Delegates to batch method for consistency.
        """
        return await self.get_events_results([event_id])

    async def get_events_results(self, event_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Fetch results for multiple Smarkets events using batching.
        """
        results = []
        if not self.db or not event_ids:
            return results

        db = self.db

        # 1. Fetch relevant Odds records for ALL events
        stmt = (
            select(Odds)
            .options(joinedload(Odds.market).joinedload(Market.event))
            .join(Market).join(Event)
            .where(
                Odds.event_sid.in_(event_ids) | Event.id.in_(event_ids), # Flexible matching 
                Odds.bookmaker_id == (await self._get_bookmaker_id(db))
            )
        )
        # Note: The input event_ids are internal Event.id strings. 
        # But wait, odds.event_sid is external. The scheduler passes internal Event IDs.
        # So matching should be on Event.id.
        stmt = (
            select(Odds)
            .options(joinedload(Odds.market).joinedload(Market.event))
            .join(Market).join(Event)
            .where(
                Event.id.in_(event_ids),
                Odds.bookmaker_id == (await self._get_bookmaker_id(db))
            )
        )
        
        odds_records = (await db.execute(stmt)).scalars().all()

        if not odds_records:
            return results

        # 2. Market Discovery
        # Group by event_sid (external ID needed for API)
        # If odds don't have event_sid, we can't easily batch fetch for them without finding it first.
        # Assuming event_sid is usually present on at least one odd per event or we skip.
        
        needs_markets_event_sids = set()
        
        # We need to map event_sid back to odds to update them
        # And ensure we have event_sids
        
        # Filter odds that need markets (missing market_sid)
        for odd in odds_records:
            if not odd.market_sid and odd.event_sid:
                needs_markets_event_sids.add(odd.event_sid)
        
        if needs_markets_event_sids:
            # Batch fetch markets
            # Smarkets API limits? 
            # We'll chunks of 50
            ev_sids_list = list(needs_markets_event_sids)
            chunk_size = 50
            
            for i in range(0, len(ev_sids_list), chunk_size):
                chunk = ev_sids_list[i:i + chunk_size]
                ids_str = ",".join(chunk)
                
                try:
                    res = await self.make_request("GET", f"/events/{ids_str}/markets/")
                    # Response format: {"markets": [...]} flat list for all events? 
                    # Documentation verification: GET /events/{ids}/markets/ usually returns a list of markets mixed.
                    # Or {"events": [{"id":..., "markets": [...]}]} ? 
                    # User said: "Our smarkets class can fetch up to 50 events markets at a time... /events/{comma_sep_ids}/markets/"
                    # Let's assume it returns a flat list of markets containing event_id.
                    
                    markets_data = res.json().get("markets", [])
                    
                    for mkt_info in markets_data:
                        m_event_id = mkt_info.get("event_id")
                        m_type = mkt_info.get("market_type", {}).get("name")
                        m_param = mkt_info.get("market_type", {}).get("param")
                        m_sid = mkt_info.get("id")
                        
                        internal_key = None
                        if m_type == "WINNER_3_WAY" or m_type == "WINNER_2_WAY": 
                            internal_key = "h2h"
                        elif m_type == "OVER_UNDER": 
                            internal_key = "totals"
                        
                        if internal_key:
                            # Match to odds
                            matching_odds = [
                                o for o in odds_records 
                                if o.event_sid == m_event_id and o.market.key == internal_key
                            ]
                            for odd in matching_odds:
                                if internal_key == "totals" and str(odd.point) != str(m_param):
                                    continue
                                if not odd.market_sid:
                                    odd.market_sid = m_sid
                                    # TODO Is this updating the odds table??
                                    
                except Exception as e:
                    print(f"Batch Market Discovery failed: {e}")

        # 3. Contract Discovery & Results
        # Group by market_sid
        active_market_sids = set(o.market_sid for o in odds_records if o.market_sid)
        
        if active_market_sids:
            m_sids_list = list(active_market_sids)
            chunk_size = 50 # Safe limit for contracts/quotes
            
            for i in range(0, len(m_sids_list), chunk_size):
                chunk = m_sids_list[i:i + chunk_size]
                ids_str = ",".join(chunk)
                
                try:
                    # User requested /markets/{ids}/contracts/
                    res = await self.make_request("GET", f"/markets/{ids_str}/contracts/")
                    contracts_data = res.json().get("contracts", [])
                    
                    for contract in contracts_data:
                        c_sid = contract.get("id")
                        c_mkt_id = contract.get("market_id")
                        c_slug = contract.get("slug")
                        outcome = contract.get("state_or_outcome")
                        
                        # Convert outcome
                        res_status = None
                        if outcome == "winner":
                            res_status = BetResult.WON.value
                        elif outcome == "loser":
                            res_status = BetResult.LOST.value
                        elif outcome == "voided":
                            res_status = BetResult.VOID.value
                        
                        # Update Odds
                        for odd in odds_records:
                            if odd.market_sid == c_mkt_id and odd.normalized_selection == c_slug:
                                if not odd.sid:
                                    odd.sid = c_sid
                                
                                if res_status:
                                    results.append({
                                        "market_key": odd.market.key,
                                        "selection": odd.normalized_selection,
                                        "result": res_status,
                                        "event_id": str(odd.market.event_id) # Need internal ID for scheduler
                                    })
                                    
                except Exception as e:
                    print(f"Batch Contract API failed for chunk {chunk}: {e}")

        # Commit updates to SIDs
        await db.commit()
        
        return results

    async def _find_event_sid(self, league_key: str, home: str, away: str, start_time: datetime, log=None) -> Optional[str]:
        """Search Smarkets for an event matching team names and start time."""
        from difflib import SequenceMatcher
        
        def _log(msg):
            if log: log(msg)

        _log(f"Searching Smarkets for {home} vs {away}...")
        
        # Search events starting around the same time (+/- 12 hours)
        start_min = (start_time - timedelta(hours=12)).isoformat()
        start_max = (start_time + timedelta(hours=12)).isoformat()
        
        try:
            res = await self.make_request("GET", "/events/", params={
                "start_datetime_min": start_min,
                "start_datetime_max": start_max,
                "state": "upcoming",
                "type": "match"
            })
            
            events = res.json().get("events", [])
            _log(f"Found {len(events)} potential Smarkets events in time window.")
            
            best_match = None
            max_score = 0
            
            target = f"{home} {away}".lower()
            for ev in events:
                ev_name = ev.get("name", "").lower()
                ev_id = ev.get("id")
                
                # Using SequenceMatcher for similarity score
                score = SequenceMatcher(None, target, ev_name).ratio()
                
                if score > 0.6 and score > max_score:
                    max_score = score
                    best_match = ev_id
            
            if best_match:
                _log(f"Matched Smarkets Event: {best_match} (Score: {round(max_score, 2)})")
            else:
                _log("No confident Smarkets match found.")
                
            return best_match
        except Exception as e:
            _log(f"Smarkets search failed: {str(e)}")
            return None

    async def get_account_balance(self) -> Dict[str, Any]:
        """Get account balance from Smarkets."""
        if not self._session_token:
            await self.authorize()
        
        res = await self.make_request("GET", "/accounts/")
        data = res.json()
        account = data.get("account", {})
        
        return {
            "balance": float(account.get("available_balance", 0)),
            "currency": account.get("currency", "GBP"),
            "commission": float(account.get("commission_type", 0)) * 100,
            "account_id": account.get("account_id")
        }

    # TODO SPK: sport_key should be league_key for all bookmakers. BK will use mapping to get their league id to find odds
    async def obtain_odds(
        self, 
        league_key: str, 
        event_ids: List[str], 
        log: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch odds for specific events on Smarkets using a waterfall ID discovery.
        """
        def _log(msg):
            if log: log(msg)

        if not self._session_token:
            await self.authorize()

        # 1. Fetch relevant Odds records from DB with relationships loaded
        results = []
        if not self.db:
            _log("No DB session provided to obtain_odds. Skipping discovery.")
            return results

        db = self.db

        stmt = (
            select(Odds)
            .options(joinedload(Odds.market).joinedload(Market.event))
            .join(Market).join(Event)
            .where(
                Event.id.in_(event_ids),
                Odds.bookmaker_id == (await self._get_bookmaker_id(db))
            )
        )
        odds_records = (await db.execute(stmt)).scalars().all()

        if not odds_records:
            _log("No Odds records found in DB for these events.")
            return results

        # 2. Discovery Waterfall
        needs_markets = set()   # event_sids that need their markets discovered
        needs_contracts = set()  # market_sids that need their contracts discovered
        needs_market_id = set()  # sids (contract IDs) that need their parent market_id discovered
        active_market_sids = set() # all market_sids we should fetch quotes for
        
        for odd in odds_records:
            if not odd.sid:
                if not odd.market_sid:
                    if not odd.event_sid:
                        # Discovery: Fuzzy match event if missing sid/mkt/ev_sid
                        _log(f"Odd {odd.id} missing all Smarkets IDs. Searching...")
                        discovered_ev_sid = await self._find_event_sid(
                            league_key, 
                            odd.market.event.home_team, 
                            odd.market.event.away_team, 
                            odd.market.event.commence_time,
                            log=log
                        )
                        if discovered_ev_sid:
                            odd.event_sid = discovered_ev_sid
                            needs_markets.add(discovered_ev_sid)
                        else:
                            _log(f"Warning: Odds {odd.id} missing event_sid and discovery failed.")
                    else:
                        needs_markets.add(odd.event_sid)
                else:
                    needs_contracts.add(odd.market_sid)
            else:
                # We have sid, but do we have market_sid? 
                # Smarkets quote API needs market_id (not strictly, but we use it to group).
                # Actually, /v3/markets/:id/quotes/ takes market IDs.
                if not odd.market_sid:
                    needs_market_id.add(odd.sid)
            
            if odd.market_sid:
                active_market_sids.add(odd.market_sid)

        # Discovery Step Zero: Parent Market IDs (sid -> market_sid)
        if needs_market_id:
            _log(f"Discovering parent market IDs for {len(needs_market_id)} sids...")
            try:
                c_ids = ",".join(list(needs_market_id))
                res = await self.make_request("GET", f"/contracts/{c_ids}/")
                contracts_data = res.json().get("contracts", [])
                for c_info in contracts_data:
                    c_id = c_info.get("id")
                    m_id = c_info.get("market_id")
                    _log(f"  SID {c_id} -> Market {m_id}")
                    for odd in odds_records:
                        if odd.sid == c_id:
                            odd.market_sid = m_id
                            active_market_sids.add(m_id)
                await db.commit()
            except Exception as e:
                _log(f"Discovery Step Zero failed: {str(e)}")

        # Discovery Step A: Markets (event_sid -> market_sid)
        if needs_markets:
            _log(f"Discovering markets for {len(needs_markets)} event_sids...")
            for ev_sid in needs_markets:
                try:
                    res = await self.make_request("GET", f"/events/{ev_sid}/markets/")
                    markets_data = res.json().get("markets", [])
                    _log(f"  Found {len(markets_data)} markets for event {ev_sid}")
                    for mkt_info in markets_data:
                        m_type = mkt_info.get("market_type", {}).get("name")
                        m_param = mkt_info.get("market_type", {}).get("param")
                        m_sid = mkt_info.get("id")
                        
                        internal_key = None
                        if m_type == "WINNER_3_WAY" or m_type == "WINNER_2_WAY": 
                            internal_key = "h2h"
                        elif m_type == "OVER_UNDER": 
                            internal_key = "totals"
                        
                        if internal_key:
                            matching_odds = [o for o in odds_records if o.event_sid == ev_sid and o.market.key == internal_key]
                            for odd in matching_odds:
                                if internal_key == "totals" and str(odd.point) != str(m_param):
                                    continue
                                odd.market_sid = m_sid
                                needs_contracts.add(m_sid)
                                active_market_sids.add(m_sid)
                                _log(f"  Mapped {m_type} to internal '{internal_key}' -> {m_sid}")
                        else:
                            _log(f"  Skipping unmapped Smarkets market type: {m_type}")
                except Exception as e:
                    _log(f"Step A failed for {ev_sid}: {str(e)}")
            await db.commit()

        # Discovery Step B: Contracts (market_sid -> sid)
        if needs_contracts:
            _log(f"Discovering contracts for {len(needs_contracts)} market_sids...")
            for m_sid in needs_contracts:
                try:
                    res = await self.make_request("GET", f"/markets/{m_sid}/contracts/")
                    contracts_data = res.json().get("contracts", [])
                    for contract in contracts_data:
                        c_sid = contract.get("id")
                        c_slug = contract.get("slug") # home, away, draw, over, under
                        
                        for odd in odds_records:
                            if odd.market_sid == m_sid:
                                if odd.normalized_selection == c_slug:
                                    odd.sid = c_sid
                except Exception as e:
                    _log(f"Step B failed for {m_sid}: {str(e)}")
            await db.commit()

        # 3. Fetch Quotes (Prices)
        if not active_market_sids:
            _log("No market_sids available to fetch quotes.")
            return results

        _log(f"Fetching quotes for {len(active_market_sids)} markets...")
        try:
            market_ids_str = ",".join(list(active_market_sids))
            res = await self.make_request("GET", f"/markets/{market_ids_str}/quotes/")
            quotes_data = res.json()
            _log(f"Received quotes for {len(quotes_data)} contracts/markets.")
            
            for odd in odds_records:
                if not odd.sid: continue
                
                quote = quotes_data.get(odd.sid)
                if quote and quote.get("offers"):
                    # We take the best offer (lowest price to buy)
                    best_offer = min(quote["offers"], key=lambda x: x["price"])
                    price_int = best_offer["price"]
                    # quantity = best_offer["quantity"]
                    
                    new_decimal_price = round(10000.0 / price_int, 3) if price_int > 0 else 0
                    # implied_prob = price_int / 10000.0
                    
                    results.append({
                        "external_event_id": odd.market.event_id,
                        "market_key": odd.market.key,
                        "selection": odd.selection,
                        "price": new_decimal_price,
                        # "implied_probability": implied_prob,
                        "point": odd.point,
                        # "bet_limit": (quantity * price_int) / 100000000.0,
                        "sid": odd.sid,
                        "market_sid": odd.market_sid,
                        "event_sid": odd.event_sid
                    })
        except Exception as e:
            _log(f"Error fetching quotes: {str(e)}")
                    
        return results

    async def _get_bookmaker_id(self, db) -> int:
        from app.db.models import Bookmaker
        from sqlalchemy import select
        res = await db.execute(select(Bookmaker.id).where(Bookmaker.key == self.name))
        return res.scalar() or 0

    async def place_bet(self, bet: Bet) -> Dict[str, Any]:
        """
        Simulate placing a bet on Smarkets.
        
        Note: Smarkets API is closed to new customers, so we simulate the bet placement
        by generating a simulated bet ID and returning success.
        """
        import uuid
        
        # Generate a unique simulated bet ID
        # simulated_id = f"SIMULATED-{uuid.uuid4().hex[:12].upper()}"
        simulated_id = None # We leave it empty, since we don't have access to Smarkets API. Leaving it empty will force the model to obtain the results in another way.
        
        return {
            "success": True,
            "status": "auto",
            "bet_id": simulated_id,
            "external_id": simulated_id,
            "message": f"Simulated bet placement (Smarkets API closed to new customers). Bet ID: {simulated_id}"
        }

    async def get_order_status(self, external_id: str) -> Dict[str, Any]:
        """Check status of a Smarkets order."""
        # NOTE: Order API not accessible, as Smarkets is not onboarding new API clients.
        # TODO: Implement order status check using Smarkets API, once they open it up.
        try:
            res = await self.make_request("GET", f"/orders/{external_id}/")
            data = res.json()
            order = data.get("order", {})
            state = order.get("status") # placed, cancelled, voided, settled
            
            # Map Smarkets states to internal statuses
            status_map = {
                "placed": BetStatus.OPEN.value,
                "cancelled": BetResult.VOID.value,
                "settled": BetStatus.SETTLED.value, # Needs further check for won/lost
                "voided": BetResult.VOID.value
            }
            
            internal_status = status_map.get(state, BetStatus.PENDING.value)
            payout = float(order.get("payout", 0)) / 100.0 if state == "settled" else None
            
            if state == "settled" and internal_status == BetStatus.SETTLED.value:
                # Determine won/lost based on payout
                # Smarkets payout is total returned. If payout > 0, it's a win or partial win.
                # Usually we map it to 'won' if > 0, or 'lost' if 0.
                if payout > 0:
                    internal_status = BetResult.WON.value
                else:
                    internal_status = BetResult.LOST.value

            return {
                "status": internal_status,
                "payout": payout,
                "external_id": external_id,
                "raw_state": state
            }
        except Exception as e:
            return {"status": "unknown", "message": f"Failed to get Smarkets order {external_id}: {str(e)}"}

    async def obtain_bet_status(self, bet: Bet) -> str:
        """
        Check bet status. If external_id is missing, try to check the contract/market state.
        """
        print("SMARKETS", bet.external_id, bet.status, bet.odd_data)
        if bet.external_id:
            return await super().obtain_bet_status(bet)
            
        # Manual/missing external ID: fallback to market/sid if available
        market_sid = bet.odd_data.get("market_sid") if bet.odd_data else None
        contract_id = bet.odd_data.get("sid") if bet.odd_data else None
        
        if not market_sid or not contract_id:
            # Try to obtain it from the odds table directly
            res = await self.db.execute(select(Odds).where(Odds.event_sid == bet.odd_data.get("event_sid"), Odds.bookmaker_id == bet.bookmaker_id))
            odds = res.scalars().all()
            
            if odds:
                for odd in odds:
                    if odd.normalized_selection == bet.odd_data.get("normalized_selection"):
                        market_sid = odd.market_sid
                        contract_id = odd.sid
                        break

            if not market_sid or not contract_id:
                # TODO SPK: sport_key should be league_key for all bookmakers. BK will use mapping to get their league id to find odds
                odds = await self.obtain_odds(
                    bet.event_data.get("league_key"), 
                    [bet.event_data.get("id")], 
                    log=print
                )
                return bet.status

            # Update the bet with the obtained market_sid and contract_id
            await self.db.execute(
                update(Bet).where(Bet.id == bet.id).values(
                    odd_data=bet.odd_data | {"market_sid": market_sid, "sid": contract_id}
                )
            )
            await self.db.commit()

        # print("SMARKETS", market_sid, contract_id)
        try:
            # Fetch contracts for this market to find our specific selection
            contracts_res = await self.make_request("GET", f"/markets/{market_sid}/contracts/")
            contracts_data = contracts_res.json().get("contracts", [])

            for contract in contracts_data:
                if str(contract.get("id")) == str(contract_id):
                    outcome = contract.get("state_or_outcome")
                    # print(f"Outcome discovery for bet {bet.id}: {outcome}")
                    if outcome == "winner":
                        return "won"
                    elif outcome == "loser":
                        return "lost"
                    elif outcome == "voided":
                        return "void"
                    break

        except Exception:
            pass
            
        return bet.status

    async def obtain_bet_payout(self, bet: Bet) -> float:
        """
        Check payout. If external_id is missing, but status is won, return stake * price.
        """
        if bet.external_id:
            return await super().obtain_bet_payout(bet)
        
        # For manual bets, the base class get_bet_settlement already handles 
        # the fallback to stake * price if status is 'won' but payout is 0.
        return bet.payout or 0.0
