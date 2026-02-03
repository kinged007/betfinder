from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, case, func
from app.api.deps import get_db
from app.db.models import Bet, Bookmaker, Event, Market, Preset, Sport, League
from app.domain import schemas
from app.core.config import settings
from app.core.enums import BetResult, BetStatus
import logging
from pydantic import BaseModel
from datetime import datetime, timezone
from app.core.preset_config import PRESET_OTHER_CONFIG_SCHEMA
from app.core.security import check_session
from typing import Optional

templates = Jinja2Templates(directory="app/web/templates")

router = APIRouter(dependencies=[Depends(check_session)])

@router.get("/")
async def dashboard_view(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Preset).where(Preset.active == True))
    presets = result.scalars().all()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "title": "Dashboard",
            "active": "dashboard",
            "presets": presets,
            "is_dev": settings.is_dev,
        }
    )

@router.get("/dashboard")
async def dashboard_redirect():
    return RedirectResponse(url="/")


@router.get("/trade-feed")
async def trade_feed_view(
    request: Request, 
    preset_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    # Fetch Presets for dropdown
    result = await db.execute(select(Preset).where(Preset.active == True))
    presets = result.scalars().all()
    
    current_preset = None
    if preset_id:
        current_preset = await db.get(Preset, preset_id)
    elif presets:
        # Auto-select the first preset by redirecting
        return RedirectResponse(url=f"/trade-feed?preset_id={presets[0].id}")
    
    return templates.TemplateResponse(
        "trade_feed.html", 
        {
            "request": request, 
            "title": "Trade Feed", 
            "active": "trade_feed",
            "presets": presets,
            "current_preset": current_preset,
            "is_dev": settings.is_dev,
        }
    )

@router.get("/presets-view")
async def presets_view(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Preset).options(selectinload(Preset.hidden_items)))
    presets_models = result.scalars().all()
    # Convert to dicts so tojson filter works reliably in templates
    presets = [schemas.PresetRead.model_validate(p).model_dump(mode='json') for p in presets_models]
    
    # Fetch dropdown data
    s_res = await db.execute(select(Sport).where(Sport.active == True).order_by(Sport.title))
    sports = s_res.scalars().all()
    
    b_res = await db.execute(select(Bookmaker).where(Bookmaker.active == True).order_by(Bookmaker.title))
    bookmakers = b_res.scalars().all()
    
    # Leagues (Optimization: Only fetch active ones, maybe restrict if too many?)
    l_res = await db.execute(select(League).where(League.active == True).order_by(League.title))
    leagues = l_res.scalars().all()
    
    # Fetch Presets for dropdown
    result_p = await db.execute(select(Preset).where(Preset.active == True))
    presets_drop = result_p.scalars().all()
    
    return templates.TemplateResponse(
        "presets.html", 
        {
            "request": request, 
            "title": "Presets", 
            "active": "presets", 
            "presets": presets,
            "presets_list": presets_drop, # Renamed to avoid shadowed presets var above
            "sports": sports,
            "bookmakers": bookmakers,
            "leagues": leagues,
            "other_config_schema": PRESET_OTHER_CONFIG_SCHEMA,
            "is_dev": settings.is_dev,
        }
    )

from sqlalchemy.orm import selectinload

@router.get("/my-bets")
async def bets_view(request: Request, db: AsyncSession = Depends(get_db)):
    from datetime import timedelta, datetime
    now = datetime.utcnow()
    live_cutoff_past = now - timedelta(minutes=120) # Define settled statuses for tabs logic
    SETTLED_STATUSES = [BetResult.WON.value, BetResult.LOST.value, BetResult.VOID.value]

    # 1. Live Bets (Currently playing)
    # Starts between now-120min and now.
    live_stmt = (
        select(Bet)
        .outerjoin(Event, Bet.event_id == Event.id)
        .options(selectinload(Bet.event).selectinload(Event.league), selectinload(Bet.bookmaker))
        .where(
            Bet.status.notin_(SETTLED_STATUSES),
            Event.commence_time >= live_cutoff_past,
            Event.commence_time <= now
        )
        .order_by(Event.commence_time.asc())
    )

    # 2. Open Bets (Future / Upcoming)
    # Starts in future (> now)
    open_stmt = (
        select(Bet)
        .outerjoin(Event, Bet.event_id == Event.id)
        .options(selectinload(Bet.event).selectinload(Event.league), selectinload(Bet.bookmaker))
        .where(
            Bet.status.notin_(SETTLED_STATUSES),
            Event.commence_time > now
        )
        .order_by(Event.commence_time.asc())
    )
    
    # 3. Unsettled Bets (Past due, no result)
    # Started more than 120 mins ago (< now-120)
    unsettled_stmt = (
        select(Bet)
        .outerjoin(Event, Bet.event_id == Event.id)
        .options(selectinload(Bet.event).selectinload(Event.league), selectinload(Bet.bookmaker))
        .where(
            Bet.status.notin_(SETTLED_STATUSES),
            Event.commence_time < live_cutoff_past
        )
        .order_by(Event.commence_time.desc())
    )

    # 4. Settled
    settled_stmt = (
        select(Bet)
        .outerjoin(Event, Bet.event_id == Event.id)
        .options(selectinload(Bet.event).selectinload(Event.league), selectinload(Bet.bookmaker))
        .where(Bet.status.in_(SETTLED_STATUSES))
        .order_by(Bet.settled_at.desc() if hasattr(Bet, 'settled_at') else Bet.placed_at.desc())
        .limit(100)
    )

    live_bets = (await db.execute(live_stmt)).scalars().all()
    open_bets = (await db.execute(open_stmt)).scalars().all()
    unsettled_bets = (await db.execute(unsettled_stmt)).scalars().all()
    settled_bets = (await db.execute(settled_stmt)).scalars().all()

    result_p = await db.execute(select(Preset).where(Preset.active == True))
    presets = result_p.scalars().all()

    return templates.TemplateResponse(
         "bets.html", 
         {
             "request": request, 
             "title": "My Bets", 
             "active": "bets", 
             "live_bets": live_bets,
             "open_bets": open_bets,
             "unsettled_bets": unsettled_bets,
             "settled_bets": settled_bets,
             "presets": presets,
             "now": now,
             "timedelta": timedelta,
             "is_dev": settings.is_dev,
         }
     )

@router.get("/bookmakers")
async def config_view(request: Request, db: AsyncSession = Depends(get_db)):
    result_p = await db.execute(select(Preset).where(Preset.active == True))
    presets = result_p.scalars().all()
    
    result_b = await db.execute(select(Bookmaker).order_by(Bookmaker.active.desc(), Bookmaker.title))
    bookmakers_models = result_b.scalars().all()
    bookmakers = [schemas.BookmakerRead.model_validate(b).model_dump(mode='json') for b in bookmakers_models]
    
    from app.services.bookmakers.base import BookmakerFactory
    bookmaker_schemas = BookmakerFactory.get_all_schemas()
    
    return templates.TemplateResponse(
        "bookmakers.html", 
        {
            "request": request, 
            "title": "Configuration", 
            "active": "config",
            "presets": presets,
            "bookmakers": bookmakers,
            "bookmaker_schemas": bookmaker_schemas,
            "is_dev": settings.is_dev,
        }
    )

class ManualBetRequest(BaseModel):
    event_id: str
    bookmaker: str
    market: str
    selection: str
    price: float
    stake: float
    true_odds: float
    preset_id: Optional[int] = None

@router.post("/trade-feed/bet")
async def register_manual_bet(
    bet_in: ManualBetRequest,
    db: AsyncSession = Depends(get_db)
):
    from app.db.models import Market, Odds

    # Lookup bookmaker by title
    result = await db.execute(select(Bookmaker).where(Bookmaker.title == bet_in.bookmaker))
    bm = result.scalars().first()
    
    if not bm:
        # Try key match
        result = await db.execute(select(Bookmaker).where(Bookmaker.key == bet_in.bookmaker))
        bm = result.scalars().first()
        
    if not bm:
        raise HTTPException(status_code=400, detail=f"Bookmaker '{bet_in.bookmaker}' not found")

    # Update balance (Manual bet, no check required as per user request)
    bm.balance -= bet_in.stake
    db.add(bm)

    # Fetch Snapshot Data
    # Fetch Event
    event_result = await db.execute(select(Event).where(Event.id == bet_in.event_id))
    event_obj = event_result.scalar_one_or_none()
    
    event_snapshot = None
    if event_obj:
        event_snapshot = {
            "id": event_obj.id,
            "sport_key": event_obj.sport_key,
            "league_key": event_obj.league_key,
            "commence_time": event_obj.commence_time.isoformat() if event_obj.commence_time else None,
            "home_team": event_obj.home_team,
            "away_team": event_obj.away_team
        }

    # Fetch Odds/Market for snapshot
    stmt = (
        select(Odds, Market)
        .join(Market, Odds.market_id == Market.id)
        .where(
            Market.event_id == bet_in.event_id,
            Market.key == bet_in.market,
            Odds.bookmaker_id == bm.id,
            Odds.normalized_selection == bet_in.selection
        )
    )
    odds_result = await db.execute(stmt)
    row = odds_result.first()
    
    market_snapshot = None
    odd_snapshot = None
    
    if row:
        odds_obj, market_obj = row
        market_snapshot = {
            "key": market_obj.key,
            "event_id": market_obj.event_id
        }
        odd_snapshot = {
            "selection": odds_obj.selection,
            "normalized_selection": odds_obj.normalized_selection,
            "price": odds_obj.price,
            "point": odds_obj.point,
            "url": odds_obj.url,
            "event_sid": odds_obj.event_sid,
            "market_sid": odds_obj.market_sid,
            "sid": odds_obj.sid,
            "implied_probability": odds_obj.implied_probability,
            "true_odds": odds_obj.true_odds,
            "edge": bet_in.price / bet_in.true_odds - 1.0 if bet_in.true_odds else 0.0
        }

    new_bet = Bet(
        event_id=bet_in.event_id,
        bookmaker_id=bm.id,
        market_key=bet_in.market,
        selection=bet_in.selection,
        stake=bet_in.stake,
        price=bet_in.price,
        status="manual",
        placed_at=datetime.utcnow(),
        event_data=event_snapshot,
        market_data=market_snapshot,
        odd_data=odd_snapshot,
        preset_id=bet_in.preset_id
    )
    
    db.add(new_bet)
    await db.commit()
    await db.refresh(new_bet)
    
    return {"status": "success", "bet_id": new_bet.id}

from app.services.analytics.trade_finder import TradeFinderService

@router.get("/trade-feed/hidden-items")
async def get_hidden_items(
    preset_id: int,
    db: AsyncSession = Depends(get_db)
):
    trade_finder = TradeFinderService()
    hidden_opportunities = await trade_finder.scan_hidden_opportunities(db, preset_id)
    return hidden_opportunities


@router.get("/partials/bets")
async def bets_partial_view(
    request: Request, 
    tab: str,
    db: AsyncSession = Depends(get_db)
):
    from datetime import timedelta, datetime
    now = datetime.utcnow()
    live_cutoff_past = now - timedelta(minutes=120) 
    SETTLED_STATUSES = [BetResult.WON.value, BetResult.LOST.value, BetResult.VOID.value]

    stmt = None
    bets = []
    
    # Common Loader Options
    options = [selectinload(Bet.event).selectinload(Event.league), selectinload(Bet.bookmaker)]
    
    if tab == 'live':
        stmt = (
            select(Bet)
            .outerjoin(Event, Bet.event_id == Event.id)
            .options(*options)
            .where(
                Bet.status.notin_(SETTLED_STATUSES),
                Event.commence_time >= live_cutoff_past,
                Event.commence_time <= now
            )
            .order_by(Event.commence_time.asc())
        )
    elif tab == 'open':
        stmt = (
            select(Bet)
            .outerjoin(Event, Bet.event_id == Event.id)
            .options(*options)
            .where(
                Bet.status.notin_(SETTLED_STATUSES),
                Event.commence_time > now
            )
            .order_by(Event.commence_time.asc())
        )
    elif tab == 'unsettled':
        stmt = (
            select(Bet)
            .outerjoin(Event, Bet.event_id == Event.id)
            .options(*options)
            .where(
                Bet.status.notin_(SETTLED_STATUSES),
                Event.commence_time < live_cutoff_past
            )
            .order_by(Event.commence_time.desc())
        )
    elif tab == 'settled':
         stmt = (
            select(Bet)
            .outerjoin(Event, Bet.event_id == Event.id)
            .options(*options)
            .where(Bet.status.in_(SETTLED_STATUSES))
            .order_by(Bet.settled_at.desc() if hasattr(Bet, 'settled_at') else Bet.placed_at.desc())
            .limit(100)
        )
    
    if stmt:
        bets = (await db.execute(stmt)).scalars().all()
        
    return templates.TemplateResponse(
        "partials/bet_rows.html",
        {
            "request": request,
            "bets": bets,
            "now": now,
            "timedelta": timedelta,
        }
    )

