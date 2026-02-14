from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.requests import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, cast, Float, and_, or_
from sqlalchemy.orm import selectinload
from app.api.deps import get_db
from app.db.models import Bet, Bookmaker, Event, Preset, Sport, League
from app.core.config import settings
from app.core.security import check_session
from typing import List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel
from collections import defaultdict

router = APIRouter(dependencies=[Depends(check_session)])
templates = Jinja2Templates(directory="app/web/templates")

class AnalyticsFilterSchema(BaseModel):
    presets: Optional[List[int]] = []
    bookmakers: Optional[List[int]] = []
    sports: Optional[List[str]] = []
    leagues: Optional[List[str]] = []
    markets: Optional[List[str]] = []
    
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    
    min_odds: Optional[float] = None
    max_odds: Optional[float] = None
    
    min_edge: Optional[float] = None
    max_edge: Optional[float] = None
    
    min_prob: Optional[float] = None
    max_prob: Optional[float] = None
    
    sort_by: Optional[str] = "settled_at"
    sort_desc: Optional[bool] = True
    
    page: Optional[int] = 1
    limit: Optional[int] = 50

@router.get("/analytics")
async def analytics_view(request: Request, db: AsyncSession = Depends(get_db)):
    # Fetch filter options
    presets = (await db.execute(select(Preset).where(Preset.active == True))).scalars().all()
    bookmakers = (await db.execute(select(Bookmaker).where(Bookmaker.active == True).order_by(Bookmaker.title))).scalars().all()
    sports = (await db.execute(select(Sport).where(Sport.active == True).order_by(Sport.title))).scalars().all()
    # Leagues could be many, maybe fetch on demand or top active ones? For now all active.
    leagues = (await db.execute(select(League).where(League.active == True).order_by(League.title))).scalars().all()
    
    return templates.TemplateResponse(
        "analytics.html",
        {
            "request": request,
            "title": "Analytics",
            "active": "analytics",
            "presets": presets,
            "bookmakers": bookmakers,
            "sports": sports,
            "leagues": leagues,
            "is_dev": settings.is_dev,
        }
    )

@router.post("/analytics/data")
async def analytics_data(
    request: Request,
    filters: AnalyticsFilterSchema,
    db: AsyncSession = Depends(get_db)
):
    # Base query: Settled bets only
    query = (
        select(Bet)
        .outerjoin(Event, Bet.event_id == Event.id)
        .outerjoin(Bookmaker, Bet.bookmaker_id == Bookmaker.id)
        .where(
            Bet.status.in_(['won', 'lost', 'void']) # Settled statuses
        )
        .options(
            selectinload(Bet.event).selectinload(Event.league),
            selectinload(Bet.bookmaker)
        )
    )
    
    # Apply Filters
    if filters.presets:
        query = query.where(Bet.preset_id.in_(filters.presets))
        
    if filters.bookmakers:
        query = query.where(Bet.bookmaker_id.in_(filters.bookmakers))
        
    if filters.sports:
        # Event sport_key
        query = query.where(Event.sport_key.in_(filters.sports))
        
    if filters.leagues:
        query = query.where(Event.league_key.in_(filters.leagues))
        
    if filters.markets:
        # Assuming exact match on market_key
        query = query.where(Bet.market_key.in_(filters.markets))

    if filters.date_from:
        query = query.where(Bet.placed_at >= filters.date_from)
    if filters.date_to:
        query = query.where(Bet.placed_at <= filters.date_to)

    # JSON Filters
    if filters.min_odds is not None:
        query = query.where(Bet.price >= filters.min_odds)
    if filters.max_odds is not None:
        query = query.where(Bet.price <= filters.max_odds)
        
    # Edge is in odd_data['edge']
    if filters.min_edge is not None:
        query = query.where(cast(Bet.odd_data['edge'], Float) >= filters.min_edge)
    if filters.max_edge is not None:
        query = query.where(cast(Bet.odd_data['edge'], Float) <= filters.max_edge)
        
    # Implied Prob is in odd_data['implied_probability']
    if filters.min_prob is not None:
        query = query.where(cast(Bet.odd_data['implied_probability'], Float) >= filters.min_prob)
    if filters.max_prob is not None:
        query = query.where(cast(Bet.odd_data['implied_probability'], Float) <= filters.max_prob)

    # Ordering (Always Chronological for Chart)
    query = query.order_by(Bet.settled_at.asc())
    
    result = await db.execute(query)
    bets = result.scalars().all()
    
    # Calculate Bankroll over time
    chart_data = []
    daily_pnl = defaultdict(float)
    
    running_balance = 0.0
    total_staked = 0.0
    total_returned = 0.0
    
    wins = 0
    losses = 0
    voids = 0
    
    rows_html_data = [] # Data to pass to template for rows

    for bet in bets:
        # Profit/Loss
        pnl = 0.0
        if bet.status == 'won':
            if bet.payout is not None:
                pnl = bet.payout - bet.stake
            else:
                pnl = (bet.stake * bet.price) - bet.stake
            wins += 1
        elif bet.status == 'lost':
            pnl = -bet.stake
            losses += 1
        elif bet.status == 'void':
            pnl = 0.0
            voids += 1
            
        # Accumulate global stats
        running_balance += pnl
        total_staked += bet.stake
        
        # Aggregate daily PnL
        ts = bet.settled_at if bet.settled_at else bet.placed_at
        date_str = ts.strftime('%Y-%m-%d')
        daily_pnl[date_str] += pnl
        
        rows_html_data.append(bet)

    # Build Chart Data from Aggregated Daily PnL
    sorted_dates = sorted(daily_pnl.keys())
    cumulative_balance = 0.0
    
    for date_str in sorted_dates:
        day_pnl = daily_pnl[date_str]
        cumulative_balance += day_pnl
        
        chart_data.append({
            'x': date_str,
            'y': round(cumulative_balance, 2),
            'pnl': round(day_pnl, 2)
        })
        
    # Python Sort for Table Display
    def get_sort_key(b):
        if filters.sort_by == "event_date":
             # Handle event relationship potential None
             if b.event and b.event.commence_time:
                 return b.event.commence_time
             return datetime.min.replace(tzinfo=timezone.utc)
        if filters.sort_by == "settled_at":
             return b.settled_at or datetime.min.replace(tzinfo=timezone.utc)
        if filters.sort_by == "placed_at":
             return b.placed_at
        if filters.sort_by == "price":
             return b.price
        if filters.sort_by == "stake":
             return b.stake
        if filters.sort_by == "payout": 
             return b.payout or 0
        if filters.sort_by == "bookmaker":
             return b.bookmaker.title if b.bookmaker else ""
        # Default
        if b.event and b.event.commence_time:
             return b.event.commence_time
        return b.settled_at or datetime.min

    rows_html_data.sort(key=get_sort_key, reverse=filters.sort_desc)
    
    # Loop already executed above to build chart_data and rows_html_data
    # stats dictionary construction logic should be moved or variables reused.
    
    # The previous replace block replaced the first half of the logic.
    # We need to ensure variables like total_staked, wins, etc are available for 'stats'.
    # They were defined and populated in the replacement block.
    
    # However, the previous tool call replaced lines 124-216 with the loop.
    # But the original file had lines 227+ that ALSO did the loop?
    # Ah, lines 179-216 in the original file (Wait, I view_file output showed two loops??)
    # Let me check view_file output again.
    # Line 181: rows_html_data = []
    # Line 183: for bet in bets: ...
    # Line 227: rows_html_data = [] ...
    # Line 229: for bet in bets: ...
    
    # YES! In step 196 I seemingly duplicated the loop logic by accident or it was already there?
    # Looking at Step 196 diff:
    # It added the sorting logic at line 117-170, but it seems there was already logic below it?
    # I see "Calculate Bankroll over time" at line 122 in Step 196 output.
    # It seems I pasted the loop TWICE in previous edits or the `view_file` showed it twice?
    
    # In Step 241 output:
    # Lines 124-216 is the "Ordering" + "Sort map" + "rows_html_data" loop logic I just added/modified.
    # Lines 217-262 is "Calculate Bankroll over time" ... AGAIN.
    
    # I need to REMOVE the second implementation (Lines 217-262) because my replacement above (124-216) 
    # now handles BOTH chart data and table data.
    
    # So I will remove lines 217-262.

    # Reverse for table display (newest first)
    # rows_html_data.reverse() # Removed, handled by explicit sort above

    # Slice for Pagination
    total_items = len(rows_html_data)
    page = filters.page if filters.page and filters.page > 0 else 1
    limit = filters.limit if filters.limit and filters.limit > 0 else 50
    start_index = (page - 1) * limit
    end_index = start_index + limit
    
    paginated_rows = rows_html_data[start_index:end_index]
    total_pages = (total_items + limit - 1) // limit

    # Render Table Rows
    table_html = templates.TemplateResponse(
        "partials/analytics_rows.html",
        {
            "request": request,
            "bets": paginated_rows
        }
    ).body.decode("utf-8")
    
    stats = {
        "total_bets": len(bets),
        "total_staked": round(total_staked, 2),
        "total_profit": round(running_balance, 2),
        "roi": round((running_balance / total_staked * 100), 2) if total_staked > 0 else 0.0,
        "win_rate": round((wins / (wins + losses) * 100), 2) if (wins + losses) > 0 else 0.0
    }

    return {
        "chart_data": chart_data,
        "table_html": table_html,
        "stats": stats,
        "pagination": {
            "page": page,
            "limit": limit,
            "total_items": total_items,
            "total_pages": total_pages
        }
    }
