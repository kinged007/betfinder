from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, case, func, or_
from sqlalchemy.orm import selectinload
from app.api.deps import get_db
from app.db.models import Bet, Bookmaker, Event, Market, Preset, Sport, League, Mapping
from app.domain import schemas
from app.core.config import settings
from app.core.enums import BetResult, BetStatus
import logging
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from app.core.preset_config import PRESET_OTHER_CONFIG_SCHEMA
from app.core.security import check_session
from typing import Optional

# Constants for bet status
BET_STATUS_WON = 'won'
BET_STATUS_LOST = 'lost'
BET_STATUS_VOID = 'void'
SETTLED_STATUSES = [BET_STATUS_WON, BET_STATUS_LOST, BET_STATUS_VOID]

templates = Jinja2Templates(directory="app/web/templates")

router = APIRouter(dependencies=[Depends(check_session)])

@router.get("/")
async def dashboard_view(request: Request, db: AsyncSession = Depends(get_db)):
    # Fetch active presets
    result = await db.execute(select(Preset).where(Preset.active == True))
    presets = result.scalars().all()
    
    # Auto-select first active preset for trade feed widget
    first_preset = presets[0] if presets else None
    
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "title": "Dashboard",
            "active": "dashboard",
            "presets": presets,
            "first_preset": first_preset,
            "is_dev": settings.is_dev,
        }
    )

@router.get("/dashboard")
async def dashboard_redirect():
    return RedirectResponse(url="/")


@router.get("/api/dashboard/bet-stats")
async def get_dashboard_bet_stats(db: AsyncSession = Depends(get_db)):
    """Get summary statistics for the bet stats widget"""
    # Query settled bets
    query = select(Bet).where(
        Bet.status.in_(SETTLED_STATUSES)
    )
    
    result = await db.execute(query)
    bets = result.scalars().all()
    
    # Calculate stats
    total_bets = len(bets)
    total_staked = 0.0
    total_profit = 0.0
    wins = 0
    losses = 0
    
    for bet in bets:
        total_staked += bet.stake
        if bet.status == BET_STATUS_WON:
            if bet.payout is not None:
                pnl = bet.payout - bet.stake
            else:
                pnl = (bet.stake * bet.price) - bet.stake
            total_profit += pnl
            wins += 1
        elif bet.status == BET_STATUS_LOST:
            total_profit -= bet.stake
            losses += 1
    
    roi = (total_profit / total_staked * 100) if total_staked > 0 else 0.0
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0
    
    # Build chart data
    # Calculate Starting Balance
    result_bk = await db.execute(select(Bookmaker).where(Bookmaker.active == True))
    bookmakers = result_bk.scalars().all()
    
    total_starting_balance = 0.0
    for bk in bookmakers:
        cfg = bk.config or {}
        starting_val = cfg.get("starting_balance")
        if starting_val is None:
            starting = 0.0
        else:
            try:
                starting = float(starting_val)
            except (ValueError, TypeError):
                starting = 0.0
        total_starting_balance += starting

    running_balance = total_starting_balance
    net_profit = 0.0
    
    # Build chart data
    daily_pnl = defaultdict(float)
    
    for bet in bets:
        pnl = 0.0
        if bet.status == BET_STATUS_WON:
            if bet.payout is not None:
                pnl = bet.payout - bet.stake
            else:
                pnl = (bet.stake * bet.price) - bet.stake
        elif bet.status == BET_STATUS_LOST:
            pnl = -bet.stake
        
        ts = bet.settled_at if bet.settled_at else bet.placed_at
        date_str = ts.strftime('%Y-%m-%d')
        daily_pnl[date_str] += pnl
        net_profit += pnl # Accumulate Net Profit
        
    # Build chart data
    sorted_dates = sorted(daily_pnl.keys())
    cumulative_balance = total_starting_balance # Start chart at Bankroll start
    chart_data = []
    
    for date_str in sorted_dates:
        day_pnl = daily_pnl[date_str]
        cumulative_balance += day_pnl
        chart_data.append({
            'x': date_str,
            'y': round(cumulative_balance, 2),
            'pnl': round(day_pnl, 2)
        })
    
    # Current Bankroll is whatever running_balance ended up at? 
    # No, running_balance was just initialized. We need to add net_profit to it.
    current_bankroll = total_starting_balance + net_profit
    return {
        "total_bets": total_bets,
        "roi": round(roi, 1),
        "win_rate": round(win_rate, 1),
        "total_profit": round(net_profit, 2), # Maintain key 'total_profit' as 'Net Profit' for now? 
        # Actually user wants BOTH. 
        # existing 'total_profit' in dashboard.html is not displayed? 
        # Wait, dashboard.html doesn't show 'total_profit' in the list!
        # It shows Total Bets, ROI, Win Rate.
        # I will add new keys for safety.
        "net_profit": round(net_profit, 2),
        "bankroll": round(current_bankroll, 2),
        "chart_data": chart_data
    }


@router.get("/api/dashboard/upcoming-fixtures")
async def get_dashboard_upcoming_fixtures(db: AsyncSession = Depends(get_db)):
    """Get next 20 upcoming fixtures including live matches"""
    # Get events from 2 hours ago (for live matches) to future
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=2)
    
    query = (
        select(Event)
        .options(selectinload(Event.league))
        .where(
            Event.active == True,
            Event.commence_time >= cutoff_time
        )
        .order_by(Event.commence_time.asc())
        .limit(20)
    )
    
    result = await db.execute(query)
    events = result.scalars().all()
    
    fixtures = []
    now = datetime.now(timezone.utc)
    
    for event in events:
        # Ensure commence_time is timezone-aware for comparison
        event_time = event.commence_time
        if event_time.tzinfo is None:
            # If naive, assume UTC
            event_time = event_time.replace(tzinfo=timezone.utc)
        
        is_live = event_time <= now
        fixtures.append({
            "id": event.id,
            "home_team": event.home_team,
            "away_team": event.away_team,
            "commence_time": event_time.isoformat(),
            "commence_time_utc": event_time.strftime('%Y-%m-%d %H:%M UTC'),  # For tooltip
            "league_title": event.league.title if event.league else event.sport_key,
            "sport_key": event.sport_key,
            "is_live": is_live
        })
    
    return fixtures


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

@router.get("/active-leagues")
async def active_leagues_view(request: Request, db: AsyncSession = Depends(get_db)):
    # Fetch active sports
    s_res = await db.execute(select(Sport).where(Sport.active == True).order_by(Sport.title))
    sports = s_res.scalars().all()

    # Fetch active leagues
    l_res = await db.execute(select(League).where(League.active == True).order_by(League.title))
    leagues = l_res.scalars().all()

    # Group in Python
    leagues_by_sport = {}
    for l in leagues:
        if l.sport_key not in leagues_by_sport:
            leagues_by_sport[l.sport_key] = []
        leagues_by_sport[l.sport_key].append(l)

    # Structure data: List of {sport: Sport, leagues: [League]}
    grouped_leagues = []
    
    # Iterate sports to keep sorted order
    for sport in sports:
        s_leagues = leagues_by_sport.get(sport.key, [])
        if s_leagues:
            grouped_leagues.append({
                "sport": sport,
                "leagues": s_leagues
            })

    # Presets for Navbar
    p_res = await db.execute(select(Preset).where(Preset.active == True))
    presets = p_res.scalars().all()

    return templates.TemplateResponse(
        "active_leagues.html",
        {
            "request": request,
            "title": "Active Leagues",
            "active": "active_leagues",
            "sports": sports,
            # "bookmakers": bookmakers, # Removed as per user request
            "leagues": leagues, # For dropdown
            "grouped_leagues": grouped_leagues, # For table
            "presets": presets,
            "is_dev": settings.is_dev,
        }
    )

@router.get("/fixtures")
async def fixtures_page(request: Request, db: AsyncSession = Depends(get_db)):
    # Fetch initial filter data
    s_res = await db.execute(select(Sport).where(Sport.active == True).order_by(Sport.title))
    sports = s_res.scalars().all()

    b_res = await db.execute(select(Bookmaker).order_by(Bookmaker.title))
    bookmakers = b_res.scalars().all()
    
    # Leagues (active only)
    l_res = await db.execute(select(League).where(League.active == True).order_by(League.title))
    leagues = l_res.scalars().all()

    return templates.TemplateResponse(
        "fixtures.html",
        {
            "request": request,
            "title": "Fixtures",
            "active": "fixtures",
            "sports": sports,
            "bookmakers": bookmakers,
            "leagues": leagues,
            "is_dev": settings.is_dev,
        }
    )

from sqlalchemy.orm import selectinload

@router.get("/my-bets")
async def bets_view(request: Request, db: AsyncSession = Depends(get_db)):
    from datetime import timedelta, datetime
    now = datetime.now(timezone.utc)
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
        placed_at=datetime.now(timezone.utc),
        event_data=event_snapshot,
        market_data=market_snapshot,
        odd_data=odd_snapshot,
        preset_id=bet_in.preset_id
    )
    
    db.add(new_bet)
    await db.commit()
    await db.refresh(new_bet)
    
    # Send Notification if preset attached
    if bet_in.preset_id:
        preset = await db.get(Preset, bet_in.preset_id)
        if preset:
            from app.services.notifications.manager import NotificationManager
            nm = NotificationManager(db)
            # Fire and forget? ideally background task, but this is simple enough to await
            try:
                await nm.send_bet_notification(preset, new_bet)
            except Exception as e:
                logging.getLogger(__name__).error(f"Failed to send manual bet notification: {e}")

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
    now = datetime.now(timezone.utc)
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
    
    if stmt is not None:
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

@router.get("/mappings")
async def mappings_view(
    request: Request,
    status: str = "pending", # Default to pending as that's the primary use case
    source: str = "all",
    m_type: str = "all",
    search: str = "",
    page: int = 1,
    db: AsyncSession = Depends(get_db)
):
    # 1. Base Query
    query = select(Mapping)
    
    # 2. Filters
    if status == "pending":
        query = query.where(Mapping.internal_key == "PENDING")
    elif status == "mapped":
         query = query.where(Mapping.internal_key != "PENDING")
    
    if source != "all":
        query = query.where(Mapping.source == source)
    
    if m_type != "all":
        query = query.where(Mapping.type == m_type)
        
    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                Mapping.external_name.ilike(search_term),
                Mapping.external_key.ilike(search_term),
                Mapping.internal_key.ilike(search_term)
            )
        )
    
    # 3. Pagination & Sorting
    query = query.order_by(Mapping.source, Mapping.type, Mapping.external_name)
    
    # Execute
    result = await db.execute(query)
    mappings_models = result.scalars().all()
    
    # Convert to dicts for JSON serialization in template
    mappings = [
        {
            "id": m.id,
            "source": m.source,
            "type": m.type,
            "external_key": m.external_key,
            "internal_key": m.internal_key,
            "external_name": m.external_name
        }
        for m in mappings_models
    ]
    
    # Filter options
    sources_res = await db.execute(select(Mapping.source).distinct())
    sources = sources_res.scalars().all()
    
    types_res = await db.execute(select(Mapping.type).distinct())
    types = types_res.scalars().all()
    
    # Fetch presets for navbar
    result_p = await db.execute(select(Preset).where(Preset.active == True))
    presets = result_p.scalars().all()
    
    return templates.TemplateResponse(
        "mappings.html",
        {
            "request": request,
            "title": "Mappings",
            "active": "mappings",
            "mappings": mappings,
            "filter_status": status,
            "filter_source": source,
            "filter_type": m_type,
            "filter_search": search,
            "sources": sources,
            "types": types,
            "presets": presets,
            "is_dev": settings.is_dev,
        }
    )

class MappingUpdateParams(BaseModel):
    internal_key: str
    external_key: str

@router.post("/mappings/{mapping_id}/update")
async def update_mapping(
    mapping_id: int,
    params: MappingUpdateParams,
    db: AsyncSession = Depends(get_db)
):
    mapping = await db.get(Mapping, mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")
    
    mapping.internal_key = params.internal_key
    mapping.external_key = params.external_key
    
    db.add(mapping)
    await db.commit()
    await db.refresh(mapping)
    
    return {"status": "success", "internal_key": mapping.internal_key, "external_key": mapping.external_key}

