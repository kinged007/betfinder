"""
Test script to verify MarketType functionality
"""

from app.core.market_types import MarketType

def test_market_type_mapping():
    """Test that market types are correctly mapped from SX Bet IDs"""
    
    print("Testing MarketType mappings...\n")
    
    # Test Asian Handicap (type 3) - the main issue
    market_key = MarketType.from_sx_bet_type(3, "Team A +1.5")
    assert market_key == "spreads", f"Expected 'spreads', got '{market_key}'"
    print("[PASS] Type 3 (Asian Handicap) correctly maps to 'spreads'")
    
    # Test 1X2 (type 1)
    market_key = MarketType.from_sx_bet_type(1, "Home")
    assert market_key == "h2h", f"Expected 'h2h', got '{market_key}'"
    print("[PASS] Type 1 (1X2) correctly maps to 'h2h'")
    
    # Test Under/Over (type 2)
    market_key = MarketType.from_sx_bet_type(2, "Over 2.5")
    assert market_key == "totals", f"Expected 'totals', got '{market_key}'"
    print("[PASS] Type 2 (Under/Over) correctly maps to 'totals'")
    
    # Test has_lines
    assert MarketType.has_lines(3) == True, "Asian Handicap should have lines"
    assert MarketType.has_lines(1) == False, "1X2 should not have lines"
    print("[PASS] has_lines() works correctly")
    
    # Test market filtering
    assert MarketType.is_supported("h2h", ["h2h", "spreads"]) == True
    assert MarketType.is_supported("totals", ["h2h", "spreads"]) == False
    assert MarketType.is_supported("h2h", None) == True  # No filter = all allowed
    print("[PASS] is_supported() filtering works correctly")
    
    # Test get_by_id
    market_def = MarketType.get_by_id(3)
    assert market_def is not None, "Should find market type 3"
    assert market_def.name == "Asian Handicap"
    assert market_def.internal_key == "spreads"
    print("[PASS] get_by_id() works correctly")
    
    print("\n=== All tests passed! ===")

if __name__ == "__main__":
    test_market_type_mapping()

