
from typing import Dict, Any, List
from app.services.bookmakers.base import SimpleBookmaker, BookmakerFactory, APIBookmaker
from app.db.models import Bet

from app.services.bookmakers.smarkets import SmarketsBookmaker
from app.services.bookmakers.sx_bet import SXBetBookmaker

# class BetfairExEUBookmaker(APIBookmaker):
#     name = "betfair_ex_eu"
#     title = "Betfair Exchange EU"
#     async def place_bet(self, bet: Bet) -> Dict[str, Any]:
#         # TODO: Implement Betfair API bet placement
#         return {"status": "pending", "external_id": "mock_betfair_id", "message": "Bet placed on Betfair (Mock)"}

# class SportmarketBookmaker(APIBookmaker):
#     name = "sportmarket"
#     title = "Sportmarket"
#     async def place_bet(self, bet: Bet) -> Dict[str, Any]:
#         # TODO: Implement Sportmarket API bet placement
#         return {"status": "pending", "external_id": "mock_sportmarket_id", "message": "Bet placed on Sportmarket (Mock)"}

# Register all
BookmakerFactory.register("smarkets", SmarketsBookmaker)
BookmakerFactory.register("sx_bet", SXBetBookmaker)
# BookmakerFactory.register("betfair_ex_eu", BetfairExEUBookmaker)
# BookmakerFactory.register("sportmarket", SportmarketBookmaker)
BookmakerFactory.register("pinnacle", SimpleBookmaker) # Benchmark only usually

from app.core.config import settings

if settings.is_dev:
    print("DEV MODE: Registering CoralBookmakerSimulator")
    from app.services.bookmakers.coral import CoralBookmakerSimulator
    BookmakerFactory.register("coral", CoralBookmakerSimulator)
