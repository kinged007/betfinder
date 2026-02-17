
from typing import List, Optional, Any, Dict
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class SportBase(BaseModel):
    key: str
    group: str
    title: str
    active: bool
    has_outrights: bool

class SportRead(SportBase):
    model_config = ConfigDict(from_attributes=True)

class LeagueBase(BaseModel):
    key: str
    group: str
    title: str
    active: bool
    has_outrights: bool
    sport_key: str

class LeagueRead(LeagueBase):
    model_config = ConfigDict(from_attributes=True)

class EventBase(BaseModel):
    id: str
    sport_key: str
    league_key: Optional[str] = None
    commence_time: datetime
    home_team: str
    away_team: str
    active: bool

class EventRead(EventBase):
    model_config = ConfigDict(from_attributes=True)

class OddsBase(BaseModel):
    market_id: int
    bookmaker_id: int
    selection: str
    normalized_selection: str
    price: float
    point: Optional[float] = None
    url: Optional[str] = None
    implied_probability: Optional[float] = None

class OddsRead(OddsBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

class BookmakerBase(BaseModel):
    key: str
    title: str
    active: bool
    model_type: str
    last_update: Optional[datetime] = None
    balance: float = 0.0
    config: Optional[dict] = None

class BookmakerUpdateConfig(BaseModel):
    active: Optional[bool] = None
    balance: Optional[float] = None
    config: Optional[dict] = None

class BookmakerRead(BookmakerBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

class PresetBase(BaseModel):
    name: str
    active: bool = True
    auto_trade: bool = False
    sports: List[str]
    bookmakers: List[str]
    leagues: Optional[List[str]] = None
    markets: Optional[List[str]] = None
    selections: Optional[List[str]] = None
    
    min_edge: Optional[float] = None
    max_edge: Optional[float] = None
    min_odds: Optional[float] = None
    max_odds: Optional[float] = None
    min_probability: Optional[float] = None
    max_probability: Optional[float] = None
    
    is_live: bool = False
    ignore_benchmarks: bool = False
    hours_before_min: Optional[int] = None
    hours_before_max: Optional[int] = None
    default_stake: Optional[float] = None
    simulate: bool = False
    
    # Staking Strategy
    staking_strategy: str = "fixed"  # Options: fixed, risk, kelly
    percent_risk: Optional[float] = None  # For risk strategy: % of bankroll to risk
    kelly_multiplier: Optional[float] = None  # For kelly strategy: multiplier to reduce volatility
    max_stake: Optional[float] = None  # Maximum stake amount for risk/kelly strategies
    
    show_popular_leagues: bool = False
    after_trade_action: str = "keep"
    other_config: Optional[Dict[str, Any]] = None
    
    time_window_hours: int = 24

class PresetCreate(PresetBase):
    pass

class PresetHiddenItemBase(BaseModel):
    event_id: str
    market_key: Optional[str] = None
    selection_norm: Optional[str] = None
    expiry_at: datetime

class PresetHiddenItemCreate(PresetHiddenItemBase):
    pass

class PresetHiddenItemRead(PresetHiddenItemBase):
    id: int
    preset_id: int
    model_config = ConfigDict(from_attributes=True)

class PresetRead(PresetBase):
    id: int
    created_at: datetime
    updated_at: datetime
    hidden_items: List[PresetHiddenItemRead] = []
    model_config = ConfigDict(from_attributes=True)

class BetBase(BaseModel):
    event_id: str
    bookmaker_id: int
    market_key: str
    selection: str
    stake: float
    price: float
    preset_id: Optional[int] = None

class BetCreate(BetBase):
    pass

class BetUpdate(BaseModel):
    status: Optional[str] = None
    payout: Optional[float] = None
    settled_at: Optional[datetime] = None
    stake: Optional[float] = None
    price: Optional[float] = None

class BetBulkUpdate(BaseModel):
    bet_ids: List[int]
    status: str
    payout: Optional[float] = None
    settled_at: Optional[datetime] = None

class BetRead(BetBase):
    id: int
    status: str
    external_id: Optional[str] = None
    placed_at: datetime
    settled_at: Optional[datetime] = None
    payout: Optional[float] = None
    
    event_data: Optional[dict] = None
    market_data: Optional[dict] = None
    odd_data: Optional[dict] = None
    
    model_config = ConfigDict(from_attributes=True)
