from typing import Dict, Any, List, Optional
import time
from datetime import datetime, timezone

from app.services.bookmakers.base import APIBookmaker
from app.core.enums import BetResult, BetStatus
from app.services.bookmakers.sx_bet_market_types import MarketType
from app.db.models import Bet

# Constants for SX Network (Chain ID 4162)
SX_MAINNET_TOKENS = {
    "USDC": {"address": "0x6629Ce1Cf35Cc1329ebB4F63202F3f197b3F050B", "decimals": 6},
    "WSX": {"address": "0x3E96B0a25d51e3Cc89C557f152797c33B839968f", "decimals": 18}
}

class SXBetBookmaker(APIBookmaker):
    name = "sx_bet"
    title = "SX.Bet"
    auth_type = None # Public API does not require auth headers
    
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
            self.base_url = "https://api-toronto.sx.bet"
            # TODO: Add verification for Testnet token addresses if different
            self.tokens = SX_MAINNET_TOKENS # Fallback
        else:
            self.base_url = "https://api.sx.bet"
            self.tokens = SX_MAINNET_TOKENS
            
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

    async def obtain_sports(self) -> List[Dict[str, Any]]:
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
                
                standardized_sports.append({
                    "key": internal_key,  # Use internal key instead of sx_bet_ prefix
                    "group": sport_name,
                    "title": league_label,
                    "active": True,
                    "has_outrights": False, 
                    "details": {
                        "sport_id": sport_id,
                        "league_id": league_id
                    }
                })
                
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

    async def fetch_league_odds(self, league_key: str, allowed_markets: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Fetch odds for a complete league and return in TheOddsAPI-compatible format (List of Events).
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

            events_map = {} # eventId -> { ...event_data, bookmakers: [...] }
            
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
                    events_map[event_id] = {
                        "id": event_id,
                        "sport_key": league_key,
                        "sport_title": market_info.get("sportLabel"),
                        "commence_time": datetime.fromtimestamp(market_info.get("gameTime", 0), timezone.utc).isoformat(),
                        "home_team": market_info.get("teamOneName"),
                        "away_team": market_info.get("teamTwoName"),
                        "bookmakers": {} # keyed by bookie key (sx_bet)
                    }
                
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
                outcomes = []
                
                outcome_one_name = market_info.get("outcomeOneName")
                if outcome_one_name == "Tie":
                    outcome_one_name = "draw"

                outcome_two_name = market_info.get("outcomeTwoName")
                if outcome_two_name == "Tie":
                    outcome_two_name = "draw"
                
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
                                    outcomes.append({
                                        "name": outcome_two_name,
                                        "price": price_1,
                                        "point": point,
                                        "sid": "outcomeTwo"
                                    })
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
                                    outcomes.append({
                                        "name": outcome_one_name,
                                        "price": price_2,
                                        "point": point,
                                        "sid": "outcomeOne"
                                    })
                        except (ValueError, TypeError, ZeroDivisionError):
                            pass
                
                if not outcomes:
                    continue

                # Add to Event -> Bookmaker
                bk_key = self.name # sx_bet
                if bk_key not in events_map[event_id]["bookmakers"]:
                    events_map[event_id]["bookmakers"][bk_key] = {
                        "key": bk_key,
                        "title": self.title,
                        "last_update": datetime.now(timezone.utc).isoformat(),
                        "sid": event_id,
                        "markets": []
                    }
                
                events_map[event_id]["bookmakers"][bk_key]["markets"].append({
                    "key": market_key,
                    "sid": market_hash,
                    "outcomes": outcomes,
                    "last_update": datetime.now(timezone.utc).isoformat()
                })
            
            # Convert Map to List
            final_output = []
            for ev in events_map.values():
                ev["bookmakers"] = list(ev["bookmakers"].values())
                final_output.append(ev)
                
            return final_output

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
            sx_event_id = event.get("id") # This is "L..."
            
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
            formatted_bks = event.get("bookmakers", [])
            
            for bk in formatted_bks:
                if bk["key"] != self.name:
                    continue
                    
                event_sid = bk.get("sid")
                
                for market in bk.get("markets", []):
                    market_key = market.get("key")
                    market_sid = market.get("sid")
                    
                    for outcome in market.get("outcomes", []):
                        flat_odds.append({
                            "external_event_id": internal_uuid, # CRITICAL: Return UUID, not SX ID
                            "market_key": market_key,
                            "selection": outcome.get("name"),
                            "price": outcome.get("price"),
                            "point": outcome.get("point"),
                            "sid": outcome.get("sid", "outcomeOne"), 
                            "market_sid": market_sid,
                            "event_sid": event_sid
                        })
                        
        return flat_odds

    async def get_account_balance(self) -> Dict[str, Any]:
        """
        Return 0 balance for now as we are not connecting wallet in Phase 1.
        """
        return {
            "balance": 0.0,
            "currency": self.currency,
            "account_id": self.exchange_address or "not_configured"
        }

