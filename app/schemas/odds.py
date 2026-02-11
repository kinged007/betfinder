from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field

class OddsSport(BaseModel):
    key: str
    group: str
    title: str
    active: bool = True
    has_outrights: bool = False
    details: Optional[Dict[str, Any]] = None # Extra info like internal IDs

class OddsOutcome(BaseModel):
    selection: str
    normalized_selection: str
    price: float
    point: Optional[float] = None
    url: Optional[str] = None
    sid: Optional[str] = None
    market_sid: Optional[str] = None
    event_sid: Optional[str] = None
    bet_limit: Optional[float] = None

class OddsMarket(BaseModel):
    key: str
    outcomes: List[OddsOutcome]
    sid: Optional[str] = None
    link: Optional[str] = None
    last_update: Optional[datetime] = None

class OddsBookmaker(BaseModel):
    key: str
    title: str
    markets: List[OddsMarket]
    last_update: datetime
    sid: Optional[str] = None
    link: Optional[str] = None

class OddsEvent(BaseModel):
    id: str  # External ID from source or Generated ID
    sport_key: str
    sport_title: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmakers: List[OddsBookmaker]
