
from typing import Dict, Any, List
from app.services.bookmakers.base import SimpleBookmaker, BookmakerFactory, APIBookmaker
from app.db.models import Bet

from app.services.bookmakers.smarkets import SmarketsBookmaker

class BetfairExEUBookmaker(APIBookmaker):
    name = "betfair_ex_eu"
    title = "Betfair Exchange EU"
    async def place_bet(self, bet: Bet) -> Dict[str, Any]:
        # TODO: Implement Betfair API bet placement
        return {"status": "pending", "external_id": "mock_betfair_id", "message": "Bet placed on Betfair (Mock)"}

class SxBetBookmaker(APIBookmaker):
    name = "sxbet"
    title = "Sx.bet"
    async def place_bet(self, bet: Bet) -> Dict[str, Any]:
        # TODO: Implement Sx.bet API bet placement
        return {"status": "pending", "external_id": "mock_sxbet_id", "message": "Bet placed on Sx.bet (Mock)"}

class SportmarketBookmaker(APIBookmaker):
    name = "sportmarket"
    title = "Sportmarket"
    async def place_bet(self, bet: Bet) -> Dict[str, Any]:
        # TODO: Implement Sportmarket API bet placement
        return {"status": "pending", "external_id": "mock_sportmarket_id", "message": "Bet placed on Sportmarket (Mock)"}

# Register all
BookmakerFactory.register("smarkets", SmarketsBookmaker)
BookmakerFactory.register("betfair_ex_eu", BetfairExEUBookmaker)
BookmakerFactory.register("sxbet", SxBetBookmaker)
BookmakerFactory.register("sportmarket", SportmarketBookmaker)
BookmakerFactory.register("pinnacle", SimpleBookmaker) # Benchmark only usually

from app.core.config import settings

if settings.is_dev:
    print("DEV MODE: Registering CoralBookmakerSimulator")
    from app.services.bookmakers.coral import CoralBookmakerSimulator
    BookmakerFactory.register("coral", CoralBookmakerSimulator)
