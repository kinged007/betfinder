
from typing import List, Optional
from datetime import datetime
from sqlalchemy import String, Boolean, ForeignKey, Integer, Float, JSON, DateTime, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, TimestampMixin

class Sport(Base, TimestampMixin):
    key: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    group: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    has_outrights: Mapped[bool] = mapped_column(Boolean, default=False)
    
    leagues: Mapped[List["League"]] = relationship(back_populates="sport")

class League(Base, TimestampMixin):
    key: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    group: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    has_outrights: Mapped[bool] = mapped_column(Boolean, default=False)
    popular: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    sport_key: Mapped[str] = mapped_column(ForeignKey("sport.key"))
    
    sport: Mapped["Sport"] = relationship(back_populates="leagues")
    events: Mapped[List["Event"]] = relationship(back_populates="league")

class Event(Base, TimestampMixin):
    id: Mapped[str] = mapped_column(String, primary_key=True) # The-Odds-API Event ID
    sport_key: Mapped[str] = mapped_column(String, index=True)
    league_key: Mapped[Optional[str]] = mapped_column(ForeignKey("league.key"), nullable=True)
    
    commence_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    home_team: Mapped[str] = mapped_column(String)
    away_team: Mapped[str] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    league: Mapped[Optional["League"]] = relationship(back_populates="events")
    markets: Mapped[List["Market"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    bets: Mapped[List["Bet"]] = relationship(back_populates="event")

class Bookmaker(Base, TimestampMixin):
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, unique=True, index=True) # e.g., 'pinnacle', 'smarkets'
    title: Mapped[str] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Configuration
    model_type: Mapped[str] = mapped_column(String, default="simple") # simple, api, source
    config: Mapped[Optional[dict]] = mapped_column(JSON, default={}) # Encrypted keys/tokens could be handled elsewhere or here
    balance: Mapped[float] = mapped_column(Float, default=0.0)
        
    last_update: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    odds_entries: Mapped[List["Odds"]] = relationship(back_populates="bookmaker")
    bets: Mapped[List["Bet"]] = relationship(back_populates="bookmaker")

class Market(Base, TimestampMixin):
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String) # e.g., 'h2h', 'spreads', 'totals'
    event_id: Mapped[str] = mapped_column(ForeignKey("event.id"))
    
    event: Mapped["Event"] = relationship(back_populates="markets")
    odds: Mapped[List["Odds"]] = relationship(back_populates="market", cascade="all, delete-orphan")

    __table_args__ = (
        Index('ix_market_event_key', 'event_id', 'key'),
    )

class Odds(Base, TimestampMixin):
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("market.id"))
    bookmaker_id: Mapped[int] = mapped_column(ForeignKey("bookmaker.id"))
    
    # Selection/Outcome info (e.g. 'Home', 'Over', 'Under')
    selection: Mapped[str] = mapped_column(String) 
    normalized_selection: Mapped[str] = mapped_column(String) # Standardized: 'home', 'away', 'draw', 'over', 'under'
    
    price: Mapped[float] = mapped_column(Float)
    point: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # For spreads/totals
    url: Mapped[Optional[str]] = mapped_column(String, nullable=True) # Deep link to bookmaker
    
    # Extended Data
    event_sid: Mapped[Optional[str]] = mapped_column(String, nullable=True) # Bookmaker's Event ID
    market_sid: Mapped[Optional[str]] = mapped_column(String, nullable=True) # Bookmaker's Market ID
    sid: Mapped[Optional[str]] = mapped_column(String, nullable=True) # Bookmaker's Selection/Outcome ID
    bet_limit: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # Max bet amount
    
    implied_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    true_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    margin: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Result
    result: Mapped[Optional[str]] = mapped_column(String, nullable=True) # See BetResult enum: won, lost, void

    market: Mapped["Market"] = relationship(back_populates="odds")
    bookmaker: Mapped["Bookmaker"] = relationship(back_populates="odds_entries")

class Bet(Base, TimestampMixin):
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("event.id"))
    bookmaker_id: Mapped[int] = mapped_column(ForeignKey("bookmaker.id"))
    
    market_key: Mapped[str] = mapped_column(String)
    selection: Mapped[str] = mapped_column(String)
    
    stake: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    
    status: Mapped[str] = mapped_column(String, default="pending") # See BetStatus enum: pending, open, settled, won, lost, void
    external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True) # Bet ID from the bookmaker
    
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    event: Mapped["Event"] = relationship(back_populates="bets")
    bookmaker: Mapped["Bookmaker"] = relationship(back_populates="bets")

    # Snapshot data
    event_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    market_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    odd_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    payout: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    preset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("preset.id"), nullable=True)
    preset: Mapped[Optional["Preset"]] = relationship()

class PresetHiddenItem(Base, TimestampMixin):
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    preset_id: Mapped[int] = mapped_column(ForeignKey("preset.id"))
    
    # Scope of hiding
    event_id: Mapped[str] = mapped_column(String, index=True)
    market_key: Mapped[Optional[str]] = mapped_column(String, nullable=True) # If null, hide whole event
    selection_norm: Mapped[Optional[str]] = mapped_column(String, nullable=True) # If null (and market set), hide whole market.
    
    expiry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    
    preset: Mapped["Preset"] = relationship(back_populates="hidden_items")

class Preset(Base, TimestampMixin):
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_trade: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Configuration for what to fetch/scan
    sports: Mapped[List[str]] = mapped_column(JSON) # List of sport keys
    bookmakers: Mapped[List[str]] = mapped_column(JSON) # List of bookmaker keys
    leagues: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True) # List of league keys
    markets: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True) # List of market keys
    selections: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True) # List of normalized selections: 'home', 'away', 'draw', 'over', 'under'
    
    # Criteria for opportunities
    min_edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # Edge from
    max_edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # Edge to
    min_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # Odds from
    max_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # Odds to
    min_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # Probability from (%)
    max_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # Probability to (%)
    
    # Timing & Game State
    is_live: Mapped[bool] = mapped_column(Boolean, default=False) # False=Pre-Game, True=Live
    ignore_benchmarks: Mapped[bool] = mapped_column(Boolean, default=False)
    
    time_window_hours: Mapped[int] = mapped_column(Integer, default=24) # Legacy field, maybe keep for backward compat or remove? TODO Remove.
    # User requested specific "Hours before game time: from (int) and to (int)" for pre-game.
    hours_before_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hours_before_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Betting Config
    default_stake: Mapped[Optional[float]] = mapped_column(Float, default=10.0)
    simulate: Mapped[bool] = mapped_column(Boolean, default=False) # If true, bets are placed virtually (no API call, no balance check)
    
    # Staking Strategy
    staking_strategy: Mapped[str] = mapped_column(String, default="fixed") # Options: fixed, risk, kelly
    percent_risk: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # For risk strategy: % of bankroll to risk
    kelly_multiplier: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # For kelly strategy: multiplier to reduce volatility
    max_stake: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # Maximum stake amount for risk/kelly strategies
    
    # UI/Logic Flags
    show_popular_leagues: Mapped[bool] = mapped_column(Boolean, default=False)
    after_trade_action: Mapped[str] = mapped_column(String, default="keep") # remove_match, remove_trade, keep, remove_line
    
    other_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    hidden_items: Mapped[List["PresetHiddenItem"]] = relationship(back_populates="preset", cascade="all, delete-orphan")

class Notification(Base, TimestampMixin):
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String) # error, info, trade, mapping
    message: Mapped[str] = mapped_column(String)
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

class Mapping(Base, TimestampMixin):
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String) # e.g., 'smarkets'
    type: Mapped[str] = mapped_column(String) # 'team', 'league', 'market'
    external_key: Mapped[str] = mapped_column(String) # The value from the external source
    internal_key: Mapped[str] = mapped_column(String) # The value in our system (The-Odds-API standard)
    external_name: Mapped[Optional[str]] = mapped_column(String, nullable=True) # Human-readable name from source (e.g. "Copa Libertadores")
    
    __table_args__ = (
        Index('ix_mapping_source_type_external', 'source', 'type', 'external_key', unique=True),
    )
