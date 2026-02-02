from enum import Enum

class BetResult(str, Enum):
    WON = "won"
    LOST = "lost"
    VOID = "void"

class BetStatus(str, Enum):
    PENDING = "pending" # Initial state
    OPEN = "open"       # Placed/Accepted by bookmaker
    SETTLED = "settled" # Process complete (generic)
    WON = "won"         # Result: Won
    LOST = "lost"       # Result: Lost
    VOID = "void"       # Result: Voided
    MANUAL = "manual"   # Manual intervention/placement
    AUTO = "auto"       # Auto-traded
