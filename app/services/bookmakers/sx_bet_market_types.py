"""
Market Type Definitions and Mapping

This module provides a centralized definition of all market types supported by various bookmakers.
It serves as the single source of truth for market type properties and conversions.
"""

from typing import Optional, Dict, List
from dataclasses import dataclass


@dataclass
class MarketTypeDefinition:
    """Definition of a market type with its properties."""
    id: int
    name: str
    has_lines: bool
    description: str
    bet_group: str
    internal_key: str  # Our internal standardized key (h2h, spreads, totals, etc.)


class MarketType:
    """
    Market Type registry and mapping utilities.
    
    This class maintains all market type definitions and provides methods
    to convert between external bookmaker IDs and internal market keys.
    """
    
    # Market Type Definitions (from SX Bet API and other sources)
    DEFINITIONS: List[MarketTypeDefinition] = [
        # Game Lines - Main Markets
        MarketTypeDefinition(1, "1X2", False, "Who will win the game (1X2)", "1x2", "h2h"),
        MarketTypeDefinition(52, "12", False, "Who will win the game", "game-lines", "h2h"),
        MarketTypeDefinition(88, "To Qualify", False, "Which team will qualify", "game-lines", "h2h"),
        MarketTypeDefinition(226, "12 Including Overtime", False, "Who will win the game including overtime (no draw)", "game-lines", "h2h"),
        
        # Handicap Markets
        MarketTypeDefinition(3, "Asian Handicap", True, "Who will win the game with handicap (no draw)", "game-lines", "spreads"),
        MarketTypeDefinition(201, "Asian Handicap Games", True, "Who will win more games with handicap (no draw)", "game-lines", "spreads"),
        MarketTypeDefinition(342, "Asian Handicap Including Overtime", True, "Who will win the game with handicap (no draw) including Overtime", "game-lines", "spreads"),
        
        # Totals Markets
        MarketTypeDefinition(2, "Under/Over", True, "Will the score be under/over a specific line", "game-lines", "totals"),
        MarketTypeDefinition(835, "Asian Under/Over", True, "Will the score be under/over specific asian line", "game-lines", "totals"),
        MarketTypeDefinition(28, "Under/Over Including Overtime", True, "Will the score including overtime be over/under a specific line", "game-lines", "totals"),
        MarketTypeDefinition(29, "Under/Over Rounds", True, "Will the number of rounds in the match will be under/over a specific line", "game-lines", "totals"),
        MarketTypeDefinition(166, "Under/Over Games", True, "Number of games will be under/over a specific line", "game-lines", "totals"),
        MarketTypeDefinition(1536, "Under/Over Maps", True, "Will the number of maps be under/over a specific line", "game-lines", "totals"),
        
        # Outright Markets
        MarketTypeDefinition(274, "Outright Winner", False, "Winner of a tournament, not a single match", "outright-winner", "outrights"),
        
        # Period Markets - Winners
        MarketTypeDefinition(202, "First Period Winner", False, "Who will win the 1st Period Home/Away", "first-period-lines", "h2h_p1"),
        MarketTypeDefinition(203, "Second Period Winner", False, "Who will win the 2nd Period Home/Away", "second-period-lines", "h2h_p2"),
        MarketTypeDefinition(204, "Third Period Winner", False, "Who will win the 3rd Period Home/Away", "third-period-lines", "h2h_p3"),
        MarketTypeDefinition(205, "Fourth Period Winner", False, "Who will win the 4th Period Home/Away", "fourth-period-lines", "h2h_p4"),
        
        # Set Markets
        MarketTypeDefinition(866, "Set Spread", True, "Which team/player will win more sets with handicap", "set-betting", "spreads_sets"),
        MarketTypeDefinition(165, "Set Total", True, "Number of sets will be under/over a specific line", "set-betting", "totals_sets"),
        
        # Half/Period Handicaps
        MarketTypeDefinition(53, "Asian Handicap Halftime", True, "Who will win the 1st half with handicap (no draw)", "first-half-lines", "spreads_h1"),
        MarketTypeDefinition(64, "Asian Handicap First Period", True, "Who will win the 1st period with handicap (no draw)", "first-period-lines", "spreads_p1"),
        MarketTypeDefinition(65, "Asian Handicap Second Period", True, "Who will win the 2nd period with handicap (no draw)", "second-period-lines", "spreads_p2"),
        MarketTypeDefinition(66, "Asian Handicap Third Period", True, "Who will win the 3rd period with handicap (no draw)", "third-period-lines", "spreads_p3"),
        
        # Half/Period Totals
        MarketTypeDefinition(63, "12 Halftime", False, "Who will win the 1st half (no draw)", "first-half-lines", "h2h_h1"),
        MarketTypeDefinition(77, "Under/Over Halftime", True, "Will the score in the 1st half be under/over a specific line", "first-half-lines", "totals_h1"),
        MarketTypeDefinition(21, "Under/Over First Period", True, "Will the score in the 1st period be under/over a specific line", "first-period-lines", "totals_p1"),
        MarketTypeDefinition(45, "Under/Over Second Period", True, "Will the score in the 2nd period be under/over a specific line", "second-period-lines", "totals_p2"),
        MarketTypeDefinition(46, "Under/Over Third Period", True, "Will the score in the 3rd period be under/over a specific line", "third-period-lines", "totals_p3"),
        
        # First Five Innings (Baseball)
        MarketTypeDefinition(281, "1st Five Innings Asian handicap", True, "Who will win the 1st five innings with handicap (no draw)", "first-five-innings", "spreads_f5"),
        MarketTypeDefinition(1618, "1st 5 Innings Winner-12", False, "Who will win in the 1st five innings", "first-five-innings", "h2h_f5"),
        MarketTypeDefinition(236, "1st 5 Innings Under/Over", True, "Will the score in the 1st five innings be under/over a specific line", "first-five-innings", "totals_f5"),
    ]
    
    # Build lookup dictionaries for fast access
    _BY_ID: Dict[int, MarketTypeDefinition] = {mt.id: mt for mt in DEFINITIONS}
    _BY_NAME: Dict[str, MarketTypeDefinition] = {mt.name: mt for mt in DEFINITIONS}
    _BY_INTERNAL_KEY: Dict[str, List[MarketTypeDefinition]] = {}
    
    # Build internal key lookup
    for mt in DEFINITIONS:
        if mt.internal_key not in _BY_INTERNAL_KEY:
            _BY_INTERNAL_KEY[mt.internal_key] = []
        _BY_INTERNAL_KEY[mt.internal_key].append(mt)
    
    @classmethod
    def from_sx_bet_type(cls, type_id: int, outcome_name: str = "") -> Optional[str]:
        """
        Convert SX Bet market type ID to internal market key.
        
        Args:
            type_id: The market type ID from SX Bet API
            outcome_name: Optional outcome name for fallback detection
            
        Returns:
            Internal market key (e.g., 'h2h', 'spreads', 'totals')
        """
        # Try direct lookup first
        market_def = cls._BY_ID.get(type_id)
        if market_def:
            return market_def.internal_key
        
        # Fallback to name-based detection (for unknown types)
        if outcome_name:
            outcome_lower = outcome_name.lower()
            if "over" in outcome_lower or "under" in outcome_lower:
                return "totals"
            elif any(char in outcome_name for char in ["+", "-"]):
                return "spreads"
        
        
        # Default to None if unknown (do NOT default to h2h)
        return None
    
    @classmethod
    def get_by_id(cls, type_id: int) -> Optional[MarketTypeDefinition]:
        """Get market type definition by ID."""
        return cls._BY_ID.get(type_id)
    
    @classmethod
    def get_by_name(cls, name: str) -> Optional[MarketTypeDefinition]:
        """Get market type definition by name."""
        return cls._BY_NAME.get(name)
    
    @classmethod
    def get_by_internal_key(cls, internal_key: str) -> List[MarketTypeDefinition]:
        """Get all market type definitions for an internal key."""
        return cls._BY_INTERNAL_KEY.get(internal_key, [])
    
    @classmethod
    def has_lines(cls, type_id: int) -> bool:
        """Check if a market type has lines/points."""
        market_def = cls._BY_ID.get(type_id)
        return market_def.has_lines if market_def else False
    
    @classmethod
    def is_supported(cls, internal_key: str, allowed_markets: Optional[List[str]] = None) -> bool:
        """
        Check if a market type is supported based on configuration.
        
        Args:
            internal_key: Internal market key to check
            allowed_markets: List of allowed market keys from configuration
            
        Returns:
            True if the market is supported, False otherwise
        """
        if not allowed_markets:
            return True  # No filter, all markets allowed
        
        return internal_key in allowed_markets
