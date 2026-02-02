from fastapi import APIRouter, Request, Depends, Query
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, distinct, case
from app.api.deps import get_db
from app.db.models import Event, Sport, League, Bookmaker, Market, Odds
from app.core.config import settings
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy.orm import selectinload
from app.core.security import check_session

router = APIRouter(dependencies=[Depends(check_session)])
templates = Jinja2Templates(directory="app/web/templates")


@router.get("/fixtures", tags=["Web"])
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

@router.get("/api/fixtures/list", tags=["API"])
async def get_fixtures_list(
    db: AsyncSession = Depends(get_db),
    sports: Optional[List[str]] = Query(None),
    leagues: Optional[List[str]] = Query(None),
    bookmakers: Optional[List[str]] = Query(None),
    is_live: bool = False
):
    # Time filter: now() - 120 min (to include games that started recently and are likely still live)
    cutoff_time = datetime.utcnow() - timedelta(minutes=120)
    
    # Base query for Events
    stmt = select(Event).options(selectinload(Event.league)).where(
        Event.active == True,
        Event.commence_time >= cutoff_time
    )

    if sports:
        stmt = stmt.where(Event.sport_key.in_(sports))
    
    if leagues:
        stmt = stmt.where(Event.league_key.in_(leagues))
    
    if is_live:
        # Strictly "Live" usually means started in the past, but the prompt says 
        # "all events from now()-120min and active" as the BASE list.
        # "checkbox for live only = only events that are currently live" implies 
        # events that have ALREADY started (commence_time < now).
        now = datetime.utcnow()
        stmt = stmt.where(Event.commence_time <= now)
        # And ensure they aren't TOO old? The base filter >= cutoff_time handles that mostly.
    
    # Ordering: Soonest to Latest
    stmt = stmt.order_by(Event.commence_time.asc())

    # We need to aggregate stats: Bookmaker Count, Odds Count, Market Badges.
    # This is complex to do efficiently in one query if we also filter by bookmaker for the COUNTS.
    # If the user filters by Bookmaker X, the definition of "Bookmaker Count" should likely act
    # on the filtered set? "show ... count for bookmakers for that event ... allow filter options .. dropdown of bookmakers"
    # Usually filters reduce the rows. And the counts should reflect the visible data.
    
    # Let's execute the event query first to get the list of relevant events.
    # Pagination might be needed if there are too many, but let's assume < 500 for now.
    result = await db.execute(stmt)
    events = result.scalars().all()

    # Determine Active Bookmaker IDs for filtering
    target_bookmaker_ids = None
    if bookmakers:
        bm_res = await db.execute(select(Bookmaker.id).where(Bookmaker.key.in_(bookmakers)))
        target_bookmaker_ids = bm_res.scalars().all()

    events_data = []
    
    # Ideally we batch load relation data. 
    # But we need aggressive filtering on the relations (Bookmakers -> Odds).
    # Doing N+1 with Python filtering is easier to write but slower.
    # Creating a CTE or complex Join is faster but harder to maintain.
    # Let's try to fetch all relevant Market/Odds data in a second query or use selectinload with filtering? 
    # SQLAlchemy's selectinload doesn't easily support ad-hoc filtering on the loaded collection without "contains_eager" or similar.
    
    # Strategy: Fetch Event IDs from first query.
    event_ids = [e.id for e in events]
    
    if not event_ids:
        return []

    # Query to get Aggregates
    # Group by EventID
    # Count Distinct Bookmakers (linked via Odds)
    # Count Odds
    # Array Agg Markets (or just distinct market keys)
    
    # Join structure: Market -> Odds
    q_agg = (
        select(
            Market.event_id,
            func.count(distinct(Odds.bookmaker_id)).label("bookmaker_count"),
            func.count(Odds.id).label("odds_count"),
            # In PostgreSQL we can use array_agg. SQLite (dev) uses group_concat.
            # Let's just select distinct market keys in a separate way or assume we iterate?
            # Let's retrieve all minimal Odds info? No, too heavy.
            # Let's Group by EventID.
        )
        .join(Market.odds) # Inner join, so only markets with odds count
        .where(Market.event_id.in_(event_ids))
    )
    
    if target_bookmaker_ids:
        q_agg = q_agg.where(Odds.bookmaker_id.in_(target_bookmaker_ids))
        
    q_agg = q_agg.group_by(Market.event_id)
    
    # Also need distinct markets per event.
    # Let's just do a big raw query or a well crafted SA query to fetch aggregations.
    # Actually, getting the list of markets active is nice for badges.
    
    # Revised Strategy for Aggregates:
    # 1. Fetch Aggregates per event (BM Count, Odds Count)
    # 2. Fetch Distinct Markets per event
    
    # 1. Aggregates
    agg_run = await db.execute(q_agg)
    agg_map = {row.event_id: {"bm_count": row.bookmaker_count, "odds_count": row.odds_count} for row in agg_run}
    
    # 2. Markets
    q_markets = (
        select(Market.event_id, Market.key)
        .join(Market.odds)
        .where(Market.event_id.in_(event_ids))
    )
    if target_bookmaker_ids:
        q_markets = q_markets.where(Odds.bookmaker_id.in_(target_bookmaker_ids))
    
    q_markets = q_markets.distinct()
    mk_run = await db.execute(q_markets)
    
    market_map = {}
    for row in mk_run:
        if row.event_id not in market_map:
            market_map[row.event_id] = []
        market_map[row.event_id].append(row.key)

    # Assemble response
    for e in events:
        stats = agg_map.get(e.id, {"bm_count": 0, "odds_count": 0})
        markets = market_map.get(e.id, [])
        
        # If user filtered by bookmakers and count is 0, should we hide the event?
        # The prompt says: "allow filter options... " usually implies filtering the ROWS.
        # If I selected "Pinnacle" and this event has NO odds from Pinnacle, should it show?
        # Probably NOT.
        if target_bookmaker_ids and stats["bm_count"] == 0:
            continue
            
        events_data.append({
            "id": e.id,
            "start_time": e.commence_time.isoformat(),
            "home": e.home_team,
            "away": e.away_team,
            "sport": e.sport_key,
            "league": e.league.title if e.league else e.league_key, # Use title if loaded?
            # N+1 warning for e.league. We should options(selectinload(Event.league)) in the main query
            "bookmaker_count": stats["bm_count"],
            "odds_count": stats["odds_count"],
            "markets": sorted(markets)
        })

    # Fix N+1 for league: we didn't add eager load.
    # But e.league might be lazy loaded or fail in async if not bound?
    # Actually, we should eager load `league` in the first query.
    
    return events_data


@router.get("/api/events/{event_id}/bookmakers", tags=["API"])
async def get_event_bookmakers(
    event_id: str, 
    db: AsyncSession = Depends(get_db)
):
    # Determine which bookmakers have odds for this event, what markets, etc.
    # Format: Table of Bookmaker | Markets (badges) | Odds Count
    
    # Query: Select Bookmaker, Market.key, Count(Odds)
    # Group by Bookmaker, Market? Or just Bookmaker and aggregate markets?
    
    # Let's get raw data and process in python.
    stmt = (
        select(Bookmaker.title, Market.key, func.count(Odds.id))
        .join(Odds.bookmaker)
        .join(Odds.market)
        .where(Market.event_id == event_id)
        .group_by(Bookmaker.title, Market.key)
        .order_by(Bookmaker.title)
    )
    
    result = await db.execute(stmt)
    rows = result.all()
    
    # Process into: { "BookieName": { "markets": ["h2h", "totals"], "odds_count": 5 } }
    bm_map = {}
    
    for bm_title, market_key, odds_count in rows:
        if bm_title not in bm_map:
            bm_map[bm_title] = {"name": bm_title, "markets": [], "odds_count": 0}
        
        bm_map[bm_title]["markets"].append(market_key)
        bm_map[bm_title]["odds_count"] += odds_count
        
    return list(bm_map.values())
