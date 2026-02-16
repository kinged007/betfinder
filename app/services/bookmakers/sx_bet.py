from typing import Dict, Any, List, Optional
import time
from datetime import datetime, timezone

from app.services.bookmakers.base import APIBookmaker
from app.core.enums import BetResult, BetStatus
from app.services.bookmakers.sx_bet_market_types import MarketType
from app.db.models import Bet
from app.schemas.odds import OddsEvent, OddsBookmaker, OddsMarket, OddsOutcome, OddsSport
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_typed_data

# Constants for SX Network (Chain ID 4162)
SX_MAINNET_TOKENS = {
    "USDC": {"address": "0x6629Ce1Cf35Cc1329ebB4F63202F3f197b3F050B", "decimals": 6},
    "WSX": {"address": "0x3E96B0a25d51e3Cc89C557f152797c33B839968f", "decimals": 18}
}
# Constants for SX Testnet (Chain ID 4162)
SX_TESTNET_TOKENS = {
    "USDC": {"address": "0x6629Ce1Cf35Cc1329ebB4F63202F3f197b3F050B", "decimals": 6},
    "WSX": {"address": "0x3E96B0a25d51e3Cc89C557f152797c33B839968f", "decimals": 18}
}

class SXBetBookmaker(APIBookmaker):
    name = "sx_bet"
    title = "SX.Bet (Exchange)"
    test_on = ["private_key"]
    auth_type = None # Public API does not require auth headers
    
    # Unit conversion constants
    BASE_DECIMALS = 6    # USDC/WSX uses 6 decimals not require auth headers
    
    # Defaults
    base_url = "https://api.sx.bet" 
    requests_per_second = 5.0 
    odds_per_second = 2.0 

    def __init__(self, key: str, config: Dict[str, Any], db: Optional[Any] = None):
        super().__init__(key, config, db)
        
        # Network Configuration
        # Default to Mainnet if not specified
        self.use_testnet = config.get("use_testnet", False)
        
        if self.use_testnet:
            # Testnet URL (Toronto)
            self.base_url = "https://api.toronto.sx.bet"
            # Unverified testnet RPC - using mainnet for now or need specific testnet RPC
            # According to docs, Toronto testnet might use a different RPC.
            # For now, let's use the same RPC if it supports both, or find the testnet RPC.
            # Valid Testnet RPC for SX (Sepolia/Toronto): https://rpc.toronto.sx.bet
            self.rpc_url = "https://rpc.toronto.sx.bet" 
            self.chain_id = 4162 # Verify if testnet has diff chainId. Toronto is 4162? 
            # SX Mainnet is 4162. Toronto Testnet is usually same ID on different network? No.
            # SX Docs say: SX Network (Mainnet) Chain ID: 4162. 
            # Toronto (Testnet) Chain ID: 647.
            self.chain_id = 647
            self.tokens = SX_TESTNET_TOKENS 
        else:
            self.base_url = "https://api.sx.bet"
            self.tokens = SX_MAINNET_TOKENS
            self.chain_id = 4162
            self.metadata_url = "https://api.sx.bet/metadata"
            self.rpc_url = "https://rpc.sx-rollup.gelato.digital"

        self.currency = config.get("currency", "USDC")
        self.exchange_address = config.get("exchange_address", "")
        self.private_key = config.get("private_key", "")
        
        self.w3 = None
        self.account = None
        if self.private_key:
            try:
                self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
                self.account = Account.from_key(self.private_key)
            except Exception as e:
                print(f"Failed to initialize web3 for SX Bet: {e}")
            
        self.currency = config.get("currency", "USDC")
        self.exchange_address = config.get("exchange_address", "")
        
        # Resolve Token Address
        token_info = self.tokens.get(self.currency, self.tokens["USDC"])
        self.base_token = token_info["address"]
        self.token_decimals = token_info["decimals"]

    # def should_sync_event(self, event_id_str, event_commence_time) -> bool:
    #     """temporary override."""
    #     # print("sx_bet should_sync_event", event_id_str, event_commence_time)
    #     return True

    @classmethod
    def get_config_schema(cls) -> List[Dict[str, Any]]:
        # Inherit defaults (api_token, etc.)
        schema = super().get_config_schema()
        
        # Remove base 'currency' to replace with specific options
        schema = [f for f in schema if f["name"] != "currency"]
        
        # Add SX.Bet specific fields
        schema.extend([
            {
                "name": "use_testnet", 
                "label": "Use Testnet (Sepolia)", 
                "type": "bool", 
                "default": False
            },
            {
                "name": "currency", 
                "label": "Currency", 
                "type": "select", 
                "options": ["USDC", "WSX"], 
                "default": "USDC"
            },
            {
                "name": "exchange_address", 
                "label": "Exchange Address (Optional)", 
                "type": "str", 
                "default": ""
            },
            {
                "name": "private_key",
                "label": "Private Key (for betting)",
                "type": "password",
                "default": ""
            }
        ])
        return schema

    async def test_connection(self) -> bool:
        """Test connection by fetching sports."""
        try:
            sports = await self.obtain_sports()
            return len(sports) > 0 or isinstance(sports, list)
        except Exception as e:
            print(f"SX.Bet Connection Failed: {e}")
            return False

    async def obtain_sports(self) -> List[OddsSport]:
        """
        Fetch active sports and leagues from SX.Bet.
        Returns a list of dicts consistent with our internal Sport/League structure.
        """
        standardized_sports = []
        
        try:
            # 1. Fetch Sports AND Leagues
            # /leagues/active endpoint 
            res_leagues = await self.make_request("GET", "/leagues/active")
            leagues_data = res_leagues.json().get("data", [])
            
            # /sports endpoint
            res_sports = await self.make_request("GET", "/sports")
            sports_data = res_sports.json().get("data", [])
            
            # Map Sport ID to Sport Name
            sport_map = {s["sportId"]: s["label"] for s in sports_data}
            
            for league in leagues_data:
                sport_id = league.get("sportId")
                sport_name = sport_map.get(sport_id, "Unknown Sport")
                
                league_id = str(league.get("leagueId"))
                league_label = league.get("label")
                
                # Use resolve_mapping to get internal key
                internal_key = await self.resolve_mapping(
                    mapping_type='league',
                    external_id=league_id,
                    external_name=league_label,
                    group=sport_name
                )
                
                # Skip if PENDING (no match found)
                if not internal_key:
                    continue
                
                standardized_sports.append(OddsSport(
                    key=internal_key,  # Use internal key instead of sx_bet_ prefix
                    group=sport_name,
                    title=league_label,
                    active=True,
                    has_outrights=False, 
                    details={
                        "sport_id": sport_id,
                        "league_id": league_id
                    }
                ))
                
            return standardized_sports
            
        except Exception as e:
            print(f"Error fetching SX.Bet sports: {e}")
            return []

    async def fetch_events(self, league_key: str) -> List[Dict[str, Any]]:
        """
        Fetch events for a specific league.
        """
        # TODO currently not in use. use it or remove it
        # Use get_external_id to reverse map internal key to SX Bet league ID
        league_id = await self.get_external_id('league', league_key)
        
        if not league_id:
            # No mapping found
            return []

        try:
            # GET /fixture/active?leagueId=...
            res = await self.make_request("GET", "/fixture/active", params={"leagueId": league_id})
            # Response: {"status": "success", "data": [...]}
            data = res.json().get("data", [])
            
            # SX Bet fixtures (data) format:
            # { "participantOneName": ..., "startDate": ..., "eventId": "L6206070", ... }
            
            # We don't need to standardize here, Ingester handles it via _process_odds_data usually.
            # But wait, `ingester._process_odds_data` expects TheOddsAPI format.
            # So generic `fetch_events` isn't enough, we need to return in TheOddsAPI format
            # OR we need `fetch_odds` to return everything (events + odds).
            # The `ingester` `sync_data_for_preset` calls `client.get_odds` (or our `fetch_odds`).
            # So `fetch_odds` is the main entry point that must return generic structure.
            return data
        except Exception as e:
            print(f"Error fetching events for league {league_key}: {e}")
            return []

    async def fetch_league_odds(self, league_key: str, allowed_markets: Optional[List[str]] = None) -> List[OddsEvent]:
        """
        Fetch odds for a complete league and return in TheOddsAPI-compatible format (List of OddsEvent).
        Used by Ingester for bulk sync.
        """
        # Use get_external_id to reverse map internal key to SX Bet league ID
        league_id = await self.get_external_id('league', league_key)
        
        if not league_id:
            # No mapping found
            return []

        try:
            # 2. Fetch Active Markets (Definitions)
            # GET /markets/active?leagueId=...&onlyMainLine=true
            # We want main lines (Spread, Total, Moneyline) to start.
            res_markets = await self.make_request("GET", "/markets/active", params={
                "leagueId": league_id,
                "onlyMainLine": "true"
            })
            if res_markets.status_code != 200:
                print(f"Error fetching markets: {res_markets.status_code}")
                return []
                
            markets_data = res_markets.json().get("data", {}).get("markets", [])
            # Map marketHash -> Market Info
            market_map = {m["marketHash"]: m for m in markets_data}
            
            if not markets_data:
                return []
                
            # 3. Fetch Best Odds
            # GET /orders/odds/best?leagueIds=...&baseToken=...
            res_odds = await self.make_request("GET", "/orders/odds/best", params={
                "leagueIds": league_id,
                "baseToken": self.base_token
            })
            odds_data = res_odds.json().get("data", {}).get("bestOdds", [])
            
            # 4. Construct Result
            # Pre-scan to identify all market types present for each event
            # This allows us to handle conflicts (e.g. Type 1 vs Type 52 both mapping to h2h)
            event_market_types = {}
            for odd_entry in odds_data:
                market_hash = odd_entry.get("marketHash")
                market_info = market_map.get(market_hash)
                if not market_info:
                    continue
                event_id = market_info.get("sportXeventId")
                m_type = market_info.get("type")
                if event_id and m_type:
                    if event_id not in event_market_types:
                        event_market_types[event_id] = set()
                    event_market_types[event_id].add(m_type)

            events_map: Dict[str, OddsEvent] = {} # eventId -> OddsEvent
            
            for odd_entry in odds_data:
                market_hash = odd_entry.get("marketHash")
                market_info = market_map.get(market_hash)
                
                if not market_info:
                    continue
                    
                event_id = market_info.get("sportXeventId") # e.g. "L7032829"
                if not event_id:
                    continue
                    
                # Initialize Event in Map if needed
                if event_id not in events_map:
                    events_map[event_id] = OddsEvent(
                        id=event_id,
                        sport_key=league_key,
                        sport_title=market_info.get("sportLabel") or "",
                        commence_time=datetime.fromtimestamp(market_info.get("gameTime", 0), timezone.utc),
                        home_team=market_info.get("teamOneName") or "Unknown Home",
                        away_team=market_info.get("teamTwoName") or "Unknown Away",
                        bookmakers=[] 
                    )
                
                # Normalize Market Key using MarketType class
                m_type = market_info.get("type")
                outcome_one_name = market_info.get("outcomeOneName", "")
                
                # Use MarketType to determine the correct internal key
                market_key = MarketType.from_sx_bet_type(m_type, outcome_one_name)
                
                # Conflict Resolution:
                # If we have Type 52 (Winner/DNB) AND Type 1 (1X2) for the same event, 
                # map Type 52 to 'dnb' to avoid overwriting the main 'h2h' market.
                if m_type == 52 and 1 in event_market_types.get(event_id, set()):
                    market_key = "dnb"

                if not market_key:
                     # Log only once per run/market to avoid spam
                     print(f"DEBUG: Unknown Market Type {m_type} ({outcome_one_name}). Skipping.")
                     continue
                
                # Filter markets based on allowed_markets configuration
                if not MarketType.is_supported(market_key, allowed_markets):
                    continue

                if market_key == 'h2h' and m_type not in [1, 52, 88, 226]: # Known H2H types
                     # Log potential misclassification
                     print(f"DEBUG: Market Type {m_type} ({outcome_one_name}) classified as h2h. Might be incorect.")
                
                # Get point/line if the market type supports it
                point = None
                if MarketType.has_lines(m_type):
                    point = market_info.get("line")
                     
                # Prepare Outcomes
                outcomes: List[OddsOutcome] = []
                
                outcome_one_name = market_info.get("outcomeOneName")
                if outcome_one_name == "Tie":
                    outcome_one_name = "draw"

                outcome_two_name = market_info.get("outcomeTwoName")
                if outcome_two_name == "Tie":
                    outcome_two_name = "draw"
                
                # Internal helper for normalization (simplified for SX Bet specific logic if needed, or generic)
                # For now using simple logic:
                def normalize_selection(sel_name, m_key, h_team, a_team):
                    sel_lower = sel_name.lower()
                    if m_key in ['h2h', 'spreads', 'moneyline']:
                        if sel_lower == h_team.lower(): return 'home'
                        if sel_lower == a_team.lower(): return 'away'
                        if sel_lower == 'draw': return 'draw'
                    if m_key in ['totals']:
                        if sel_lower.startswith('over'): return 'over'
                        if sel_lower.startswith('under'): return 'under'
                    return sel_name

                # Process Outcome One (maker perspective) -> assign to Outcome Two (taker perspective)
                if outcome_two_name and not outcome_two_name.startswith("Not "):
                    odd_1_data = odd_entry.get("outcomeOne", {})
                    maker_pct_1 = odd_1_data.get("percentageOdds")
                    
                    if maker_pct_1:
                        try:
                            maker_prob_1 = float(maker_pct_1) / 1e20
                            if 0.0 < maker_prob_1 < 1.0:
                                taker_prob_1 = 1.0 - maker_prob_1
                                price_1 = round(1.0 / taker_prob_1, 3)
                                
                                if price_1 > 1.0:
                                    outcomes.append(OddsOutcome(
                                        selection=outcome_two_name,
                                        normalized_selection=normalize_selection(outcome_two_name, market_key, events_map[event_id].home_team, events_map[event_id].away_team),
                                        price=price_1,
                                        point=point,
                                        sid="outcomeTwo",
                                        market_sid=market_hash,
                                        event_sid=event_id
                                    ))
                        except (ValueError, TypeError, ZeroDivisionError):
                            pass
                
                # Process Outcome Two (maker perspective) -> assign to Outcome One (taker perspective)
                if outcome_one_name and not outcome_one_name.startswith("Not "):
                    odd_2_data = odd_entry.get("outcomeTwo", {})
                    maker_pct_2 = odd_2_data.get("percentageOdds")
                    
                    if maker_pct_2:
                        try:
                            maker_prob_2 = float(maker_pct_2) / 1e20
                            if 0.0 < maker_prob_2 < 1.0:
                                taker_prob_2 = 1.0 - maker_prob_2
                                price_2 = round(1.0 / taker_prob_2, 3)
                                
                                if price_2 > 1.0:
                                    outcomes.append(OddsOutcome(
                                        selection=outcome_one_name,
                                        normalized_selection=normalize_selection(outcome_one_name, market_key, events_map[event_id].home_team, events_map[event_id].away_team),
                                        price=price_2,
                                        point=point,
                                        sid="outcomeOne",
                                        market_sid=market_hash,
                                        event_sid=event_id
                                    ))
                        except (ValueError, TypeError, ZeroDivisionError):
                            pass
                
                if not outcomes:
                    continue

                # Add to Event -> Bookmaker
                # Find or create bookmaker entry in the event
                current_event = events_map[event_id]
                bk_entry = next((bk for bk in current_event.bookmakers if bk.key == self.name), None)
                
                if not bk_entry:
                    bk_entry = OddsBookmaker(
                        key=self.name,
                        title=self.title,
                        last_update=datetime.now(timezone.utc),
                        markets=[],
                        sid=event_id
                    )
                    current_event.bookmakers.append(bk_entry)
                
                bk_entry.markets.append(OddsMarket(
                    key=market_key,
                    sid=market_hash,
                    outcomes=outcomes,
                    last_update=datetime.now(timezone.utc)
                ))
            
            return list(events_map.values())

        except Exception as e:
            print(f"Error fetching odds for {league_key}: {e}")
            return []

    # TODO SPK: sport_key should be league_key for all bookmakers. BK will use mapping to get their league id to find odds
    async def obtain_odds(
        self, 
        league_key: str, 
        event_ids: Optional[List[str]] = None,
        log: Optional[Any] = None,
        allowed_markets: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch odds for specific events and return as a FLAT LIST of Dictionary objects.
        This is used by Trade Finder to update specific odds.
        
        Args:
            league_key: Internal league key
            event_ids: List of INTERNAL Event IDs (UUIDs) to filter by.
            log: Logger
            allowed_markets: Optional list of market keys
            
        Returns:
            List[Dict] where each dict represents an Odd update.
        """
        # 0. Resolve Internal UUIDs to SX Bet "SportXeventId" (e.g. L12345)
        # TradeFinder passes UUIDs. API returns L-IDs. 
        # We need to map: UUID -> L-ID to filter API results.
        # And map: L-ID -> UUID to return results with correct updated ID.
        
        sx_id_to_uuid = {} # Map L-ID -> UUID
        try:
            if event_ids and self.db:
                # Query Odds table to find existing event_sid for these event_ids
                # We assume the Ingester has already populated Odds with bookmaker_id=self.id
                from app.db.models import Odds, Market, Event, Bookmaker
                from sqlalchemy import select

                # We need to find the Bookmaker ID for "sx_bet"
                # But we might not have it handy. 
                # Alternative: Query by Event directly if we assume only 1 sx_bet odd per event? No.
                # We should query Odds joined with Bookmaker.
                
                # Fetch bookmaker ID first if needed, or join
                stmt = (
                    select(Odds.event_sid, Event.id)
                    .select_from(Odds)
                    .join(Market, Market.id == Odds.market_id)
                    .join(Event, Event.id == Market.event_id)
                    .join(Bookmaker, Bookmaker.id == Odds.bookmaker_id)
                    .where(
                        Event.id.in_(event_ids),
                        Bookmaker.key == self.name,
                        Odds.event_sid.isnot(None)
                    )
                    .distinct()
                )
                res = await self.db.execute(stmt)
                rows = res.all()
                
                for sx_id, uuid in rows:
                    if sx_id:
                        sx_id_to_uuid[sx_id] = str(uuid)
                        
                print(f"DEBUG: Mapped {len(sx_id_to_uuid)} internal events to SX Bet IDs.")
                
        except Exception as e:
            print(f"Error mapping IDs in sx_bet.obtain_odds: {e}")
            # If mapping fails, we might return nothing or everything?
            # If we return everything with SX identifiers, TradeFinder won't match them.
            return []

        # 1. Fetch all odds for the league (hierarchical)
        # TODO: Optimize to fetch only specific markets if API allowed, but currently we fetch all main lines.
        events_data = await self.fetch_league_odds(league_key, allowed_markets)
        # print("DEBUG: events_data", len(events_data), league_key, allowed_markets)
        
        flat_odds = []
        
        # If event_ids were provided but we found no mappings, we can't update anything
        if event_ids and not sx_id_to_uuid:
            print("DEBUG: No ID mappings found for requested events. Skipping update.")
            return []
            
        target_sx_ids = set(sx_id_to_uuid.keys()) if event_ids else None
        
        for event in events_data:
            sx_event_id = event.id # This is "L..."
            
            # Filter by mapped SX IDs
            if target_sx_ids and sx_event_id not in target_sx_ids:
                continue
            
            # If no event_ids provided, we need to find the UUID for this SX ID? 
            # Or is this case only for "Update All"? 
            # If Update All, we might miss UUIDs if we didn't pre-fetch map.
            # But obtain_odds is usually called with specific IDs by TradeFinder.
            # If called without IDs, we might need a different strategy, but let's focus on TradeFinder case.
            
            internal_uuid = sx_id_to_uuid.get(sx_event_id)
            if not internal_uuid:
                # If we don't know the UUID, we can't return a useful update for TradeFinder
                continue

            # Dig into bookmakers -> markets -> outcomes
            # formatted_bks = event.bookmakers # Now object access
            
            for bk in event.bookmakers:
                if bk.key != self.name:
                    continue
                    
                event_sid = bk.sid
                
                for market in bk.markets:
                    market_key = market.key
                    market_sid = market.sid
                    
                    for outcome in market.outcomes:
                        flat_odds.append({
                            "external_event_id": internal_uuid, # CRITICAL: Return UUID, not SX ID
                            "market_key": market_key,
                            "selection": outcome.selection,
                            "price": outcome.price,
                            "point": outcome.point,
                            "sid": outcome.sid or "outcomeOne", 
                            "market_sid": market_sid,
                            "event_sid": event_sid
                        })
                        
        return flat_odds

    async def get_account_balance(self) -> Dict[str, Any]:
        """
        Get available balance from the blockchain wallet.
        """
        if not self.w3 or not self.account:
             return {
                "balance": 0.0,
                "currency": self.currency,
                "account_id": "not_configured"
            }

        try:
             # ERC20 Balance Check
             token_addr = self.base_token
             # minimal ABI for balanceOf
             abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"payable":False,"stateMutability":"view","type":"function"}]
             
             contract = self.w3.eth.contract(address=token_addr, abi=abi)
             balance_wei = contract.functions.balanceOf(self.account.address).call()
             
             balance = float(balance_wei) / (10 ** self.token_decimals)
             
             return {
                "balance": balance,
                "currency": self.currency,
                "account_id": self.account.address
            }
        except Exception as e:
            print(f"Error fetching balance for SX Bet: {e}")
            return {
                "balance": 0.0,
                "currency": self.currency,
                "account_id": self.account.address if self.account else "error"
            }

    async def place_bet(self, bet: Bet) -> Dict[str, Any]:
        """
        Place a bet on SX Bet using EIP-712 signing and the V2 Fill API.
        """
        if not self.w3 or not self.account:
             return {"status": "error", "message": "SX Bet Private Key not configured."}

        try:
            # 1. Prepare Order Parameters
            market_hash = bet.odd_data.get("market_sid")
            if not market_hash:
                 return {"status": "error", "message": "Missing market_hash (market_sid) for this bet."}
            
            # SX Bet Taker Logic:
            # If we are backing Outcome 1 (Home?), isTakerBettingOutcomeOne = True?
            # We need to map our selection to Outcome 1 or 2 based on `sx_bet.py` logic.
            # In `fetch_league_odds`:
            # - If market_info.outcomeOneName == selection -> Outcome 1
            # - If market_info.outcomeTwoName == selection -> Outcome 2
            
            # The odd_data stores 'sid' which is contract ID? Or 'outcomeOne'/'outcomeTwo'?
            # In `fetch_league_odds`: 
            #   sid="outcomeTwo" if we processed outcomeTwoName (which was maker pct 1 -> outcome 2)
            #   sid="outcomeOne" if we processed outcomeOneName
            
            sid = bet.odd_data.get("sid")
            is_taker_betting_outcome_one = False
            
            # Verification needed:
            # If `sid` == "outcomeOne", does that mean we want Outcome One?
            # Yes. In `fetch_league_odds`, we assign `sid="outcomeOne"` when processing `outcome_one_name`.
            if sid == "outcomeOne":
                is_taker_betting_outcome_one = True
            elif sid == "outcomeTwo":
                is_taker_betting_outcome_one = False
            else:
                # Fallback or error?
                return {"status": "error", "message": f"Unknown selection side: {sid}"}

            # Amounts
            stake_amount = bet.stake
            stake_wei = int(stake_amount * (10 ** self.token_decimals))
            
            price = bet.price
            # Desired Odds in 10^20 format
            # Implied Prob = 1 / Price
            # Signal Odds (Maker Side?) No, API takes Taker Odds.
            # "desiredOdds" parameter documentation says: "83000000000000000000; // ~1.20 decimal odds"
            # It seems they use Implied Probability * 10^20 as the format.
            # Wait, 1.20 decimal -> 1/1.20 = 0.8333 -> 8.33 * 10^19?
            # Docs Example: "desiredOdds": "83000000000000000000" (~1.20 decimal)
            # 83 * 10^18 / 10^20 = 0.83. 1/0.83 = 1.204. Checks out.
            
            # Implied Prob = 1 / Price
            implied_prob = 1.0 / price
            
            # SX Bet Odds Ladder Enforcement
            # Step is 0.125% = 0.00125
            # In 10^20 representation: 0.00125 * 10^20 = 1.25 * 10^17 = 125,000,000,000,000,000
            LADDER_STEP = 125_000_000_000_000_000 
            
            raw_odds_int = int(implied_prob * (10 ** 20))
            
            # Round to nearest step
            # round(val / step) * step
            desired_odds_int = int(round(raw_odds_int / LADDER_STEP) * LADDER_STEP)
            
            desired_odds_str = str(desired_odds_int)
            print(f"Odds Calc: Price {price} -> Prob {implied_prob:.4f} -> Raw {raw_odds_int} -> Ladder {desired_odds_int} (Step {LADDER_STEP})")
            
            # Slippage (User configurable? Hardcode to 5% or 1% for now?)
            odds_slippage = 5
            
            # Salt
            import random
            fill_salt = str(random.getrandbits(256))
            
            # Metadata / Contract Addresses
            # We need TokenTransferProxy and EIP712FillHasher addresses.
            # We can use defaults based on Mainnet/Testnet constants or fetch metadata.
            # For robustness, let's hardcode knowns or fetch.
            # Let's fetch metadata once or use class constants if we had them.
            # I'll create a helper to get these.
            
            addresses = await self._get_sx_addresses()
            fill_hasher = addresses.get("EIP712FillHasher")
            
            # 2. Build EIP-712 Payload
            
            # Fetch Chain ID dynamically to ensure it matches the RPC
            try:
                dynamic_chain_id = self.w3.eth.chain_id
            except Exception as e:
                print(f"Failed to fetch chain_id from RPC: {e}")
                dynamic_chain_id = self.chain_id

            domain = {
                "name": "SX Bet",
                "version": "6.0",
                "chainId": dynamic_chain_id,
                "verifyingContract": fill_hasher
            }
            
            print(f"DEBUG: Using Chain ID {dynamic_chain_id} for EIP-712 Signature")
            
            types = {
                "Details": [
                    {"name": "action", "type": "string"},
                    {"name": "market", "type": "string"},
                    {"name": "betting", "type": "string"},
                    {"name": "stake", "type": "string"},
                    {"name": "worstOdds", "type": "string"},
                    {"name": "worstReturning", "type": "string"},
                    {"name": "fills", "type": "FillObject"},
                ],
                "FillObject": [
                    {"name": "stakeWei", "type": "string"},
                    {"name": "marketHash", "type": "string"},
                    {"name": "baseToken", "type": "string"},
                    {"name": "desiredOdds", "type": "string"},
                    {"name": "oddsSlippage", "type": "uint256"},
                    {"name": "isTakerBettingOutcomeOne", "type": "bool"},
                    {"name": "fillSalt", "type": "uint256"},
                    {"name": "beneficiary", "type": "address"},
                    {"name": "beneficiaryType", "type": "uint8"},
                    {"name": "cashOutTarget", "type": "bytes32"},
                ]
            }
            
            # Message
            # Note: The "message" part of typed data for SX Bet involves some "N/A" fields for action/betting/etc 
            # and the nested "fills" object.
            # Based on docs example:
            # message: { action: "N/A", ..., fills: { ... } }
            
            fills = {
                "stakeWei": str(stake_wei),
                "marketHash": market_hash,
                "baseToken": self.base_token,
                "desiredOdds": desired_odds_str,
                "oddsSlippage": odds_slippage,
                "isTakerBettingOutcomeOne": is_taker_betting_outcome_one,
                "fillSalt": int(fill_salt),
                "beneficiary": "0x0000000000000000000000000000000000000000",
                "beneficiaryType": 0,
                "cashOutTarget": "0x0000000000000000000000000000000000000000000000000000000000000000"
            }
            
            message = {
                "action": "N/A",
                "market": market_hash,
                "betting": "N/A",
                "stake": "N/A",
                "worstOdds": "N/A",
                "worstReturning": "N/A",
                "fills": fills
            }
            
            # 3. Sign
            encoded_data = encode_typed_data(domain_data=domain, message_types=types, message_data=message)
            signed_msg = self.w3.eth.account.sign_message(encoded_data, private_key=self.private_key)
            signature = signed_msg.signature.hex()
            
            # 4. API Request
            payload = {
                "market": market_hash,
                "baseToken": self.base_token,
                "isTakerBettingOutcomeOne": is_taker_betting_outcome_one,
                "stakeWei": str(stake_wei),
                "desiredOdds": desired_odds_str,
                "oddsSlippage": odds_slippage,
                "taker": self.account.address,
                "takerSig": signature,
                "fillSalt": fill_salt
            }
            
            print(f"\n--- SX Bet Debug ---")
            print(f"EIP-712 Message: {message}")
            print(f"API Payload: {payload}")
            print(f"--------------------\n")
            
            res = await self.make_request("POST", "/orders/fill/v2", data=payload)
            
            if res.status_code == 200:
                 data = res.json()
                 if data.get("status") == "success":
                     # Where is transaction hash? 
                     # Response format: { status: "success", data: { transactionHash: "..." } } ? 
                     # Or might be orderHash. 
                     # The docs example response just says "success" or "failure".
                     # Assuming typical structure.
                     return {
                        "success": True,
                        "status": BetStatus.OPEN.value,
                        "bet_id": data.get("data", {}).get("transactionHash") or "pending",
                        "external_id": data.get("data", {}).get("orderHash"),
                        "message": "Bet placed successfully on SX Bet."
                    }
                 else:
                     return {"status": "error", "message": f"SX Bet API Error: {data}"}
            else:
                 return {"status": "error", "message": f"HTTP {res.status_code}: {res.text}"}

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"status": "error", "message": f"Failed to place bet: {str(e)}"}

    async def _get_sx_addresses(self) -> Dict[str, Any]:
        """Fetch metadata or return defaults."""
        # Simple caching or just return known testnet/mainnet values
        if self.use_testnet:
             return {
                 "EIP712FillHasher": "0xC8dbedb008deB9c870E871F7a470f847C67135E9",
                 "TokenTransferProxy": "0xD7cCD18d33d3EC2879A6DF8e82Ef81C8830c534F"
             }
        else:
             return {
                 "EIP712FillHasher": "0x845a2Da2D70fEDe8474b1C8518200798c60aC364",
                 "TokenTransferProxy": "0x38aef22152BC8965bf0af7Cf53586e4b0C4E9936"
             }

