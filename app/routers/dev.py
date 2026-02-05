from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from app.db.session import AsyncSessionLocal
import asyncio
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.api.deps import get_db
from app.core.config import settings
from app.services.scheduler import (
    job_fetch_sports, 
    job_preset_sync, 
    job_analyze_odds, 
    job_settle_bets,
    job_cleanup_hidden_items,
    job_auto_trade,
    job_global_odds_live_sync,
    job_get_results
)
from app.db.models import Sport, Market, Mapping, Event, League, Odds, Bookmaker, Preset
from app.services.notifications.manager import NotificationManager
from app.services.analytics.trade_finder import TradeOpportunity
import random
from app.services.bookmakers.base import BookmakerFactory, APIBookmaker
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")

def check_dev_mode():
    if not settings.is_dev:
        raise HTTPException(status_code=403, detail="Development mode only")

class FetchLiveRequest(BaseModel):
    bookmaker_id: int
    future_only: bool = True

@router.post("/jobs/fetch-sports", dependencies=[Depends(check_dev_mode)])
async def trigger_fetch_sports():
    try:
        await job_fetch_sports()
        return {"status": "success", "message": "Fetch Sports job triggered"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/jobs/preset-sync", dependencies=[Depends(check_dev_mode)])
async def trigger_preset_sync(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("UPDATE preset SET last_sync_at = NULL WHERE active = true"))
        await db.commit()
        await job_preset_sync()
        return {"status": "success", "message": "Preset Sync job forced & triggered"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/jobs/analyze-odds", dependencies=[Depends(check_dev_mode)])
async def trigger_analyze_odds():
    try:
        await job_analyze_odds()
        return {"status": "success", "message": "Analyze Odds job triggered"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/jobs/settle-bets", dependencies=[Depends(check_dev_mode)])
async def trigger_settle_bets():
    try:
        await job_settle_bets()
        return {"status": "success", "message": "Bet Settlement job triggered"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/jobs/cleanup-hidden", dependencies=[Depends(check_dev_mode)])
async def trigger_cleanup_hidden():
    try:
        await job_cleanup_hidden_items()
        return {"status": "success", "message": "Cleanup Hidden Items job triggered"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/jobs/auto-trade", dependencies=[Depends(check_dev_mode)])
async def trigger_auto_trade():
    try:
        await job_auto_trade()
        return {"status": "success", "message": "Auto Trade job triggered"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/jobs/global-odds-sync", dependencies=[Depends(check_dev_mode)])
async def trigger_global_odds_sync():
    try:
        await job_global_odds_live_sync()
        return {"status": "success", "message": "Global Odds Sync job triggered"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/jobs/get-results", dependencies=[Depends(check_dev_mode)])
async def trigger_get_results():
    try:
        await job_get_results()
        return {"status": "success", "message": "Get Results job triggered"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/jobs/test-notification", dependencies=[Depends(check_dev_mode)])
async def trigger_test_notification(db: AsyncSession = Depends(get_db)):
    try:
        # Fetch a random odd to simulate a trade
        # Just grab one that has existing relations
        stmt = (
            select(Odds, Market, Event, Bookmaker, Sport, League)
            .join(Odds.market)
            .join(Market.event)
            .join(Odds.bookmaker)
            .outerjoin(Event.league)
            .outerjoin(League.sport)
            .limit(100) # Grab a few
        )
        result = await db.execute(stmt)
        rows = result.all()
        
        if not rows:
            return {"status": "error", "message": "No odds found to test with."}
            
        # Pick random
        row = random.choice(rows)
        odd, market, event, bookmaker, sport, league = row
        
        # Create a mock Preset
        # Using a specialized ID for testing so it might duplicate if we use real preset ID logic
        # But for test button, we just want to see the notif.
        
        mock_preset = Preset(
            id=999999,
            name="TEST NOTIFICATION PRESET",
            other_config={"notification_new_bet": "true"}
        )
        
        # Mock Trade Opportunity
        edge = 0.05
        if odd.true_odds:
            edge = (odd.price / odd.true_odds) - 1.0
            
        opp = TradeOpportunity(
            odd=odd,
            market=market,
            event=event,
            bookmaker=bookmaker,
            sport=sport,
            league=league,
            has_bet=False,
            edge=edge
        )
        
        # Send
        manager = NotificationManager(db)
        
        await manager.send_trade_notification(mock_preset, opp)
        
        return {"status": "success", "message": f"Test Notification Sent for {event.home_team} vs {event.away_team}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def render_table(request: Request, title: str, headers: list, rows: list):
    return templates.TemplateResponse(
        "dev_table.html",
        {
            "request": request,
            "title": title,
            "headers": headers,
            "rows": rows,
            "is_dev": settings.is_dev
        }
    )

@router.get("/sports", response_class=HTMLResponse, dependencies=[Depends(check_dev_mode)])
async def view_sports(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Sport).order_by(Sport.title))
    sports = result.scalars().all()
    headers = ["Key", "Title", "Group", "Active", "Has Outrights"]
    rows = [[s.key, s.title, s.group, s.active, s.has_outrights] for s in sports]
    return await render_table(request, "Sports", headers, rows)

@router.get("/markets", response_class=HTMLResponse, dependencies=[Depends(check_dev_mode)])
async def view_markets(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Market).order_by(Market.id.desc()).limit(100))
    markets = result.scalars().all()
    headers = ["ID", "Key", "Event ID"]
    rows = [[m.id, m.key, m.event_id] for m in markets]
    return await render_table(request, "Markets (Last 100)", headers, rows)

@router.get("/mappings", response_class=HTMLResponse, dependencies=[Depends(check_dev_mode)])
async def view_mappings(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Mapping).order_by(Mapping.id))
    mappings = result.scalars().all()
    headers = ["ID", "Source", "Type", "External Key", "Internal Key"]
    rows = [[m.id, m.source, m.type, m.external_key, m.internal_key] for m in mappings]
    return await render_table(request, "Mappings", headers, rows)

@router.get("/events", response_class=HTMLResponse, dependencies=[Depends(check_dev_mode)])
async def view_events(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Event).order_by(Event.commence_time.desc()).limit(100))
    events = result.scalars().all()
    headers = ["ID", "Sport Key", "League Key", "Commence Time", "Home", "Away", "Active"]
    rows = [[e.id, e.sport_key, e.league_key, e.commence_time, e.home_team, e.away_team, e.active] for e in events]
    return await render_table(request, "Events (Last 100)", headers, rows)

@router.get("/odds", response_class=HTMLResponse, dependencies=[Depends(check_dev_mode)])
async def view_odds(
    request: Request, 
    bookmaker_id: Optional[str] = None,
    future_only: bool = False,
    db: AsyncSession = Depends(get_db)
):
    if bookmaker_id == "":
        bookmaker_id = None
    
    if bookmaker_id is not None:
        try:
            bookmaker_id = int(bookmaker_id)
        except (ValueError, TypeError):
            bookmaker_id = None

    bm_res = await db.execute(select(Bookmaker).order_by(Bookmaker.title))
    bookmakers = bm_res.scalars().all()
    
    # Query Odds with explicit joins
    query = (
        select(Odds, Market, Event, Bookmaker)
        .join(Odds.market)
        .join(Market.event)
        .join(Odds.bookmaker)
    )
    
    if bookmaker_id:
        query = query.where(Odds.bookmaker_id == bookmaker_id)
        
    if future_only:
        from datetime import timezone
        # Use timezone-aware comparison
        now_utc = datetime.now(timezone.utc)
        buffer_time = now_utc - timedelta(minutes=120)
        query = query.where(Event.commence_time >= buffer_time)
        # For future only, sort by commence time (closest first)
        query = query.order_by(Event.commence_time.asc())
    else:
        # For all, sort by newest added
        query = query.order_by(Odds.id.desc())
        
    query = query.limit(500) # Increased limit to see more data
    result = await db.execute(query)
    rows_data = result.all()
    
    can_fetch = False
    if bookmaker_id:
        bm = await db.get(Bookmaker, bookmaker_id)
        if bm:
            bm_instance = BookmakerFactory.get_bookmaker(bm.key)
            if isinstance(bm_instance, APIBookmaker) and bm.key != "simple":
                can_fetch = True

    headers = ["ID", "Game", "Sport", "Market", "Selection", "Bookie", "Price", "Point", "Probability", "True Odds", "Edge %", "Actions"]
    
    rows = []
    for o, m, e, b in rows_data:
        edge = ((o.price / o.true_odds) - 1) * 100 if o.true_odds and o.true_odds > 0 else None
        
        rows.append({
            "id": o.id,
            "game": f"{e.home_team} vs {e.away_team}",
            "sport": e.sport_key,
            "start_time": e.commence_time.isoformat() if e.commence_time.tzinfo else e.commence_time.isoformat() + "Z",
            "market": m.key,
            "selection": o.selection,
            "selection_norm": o.normalized_selection,
            "bookie": b.title,
            "bookie_id": b.id,
            "event_id": e.id,
            "price": o.price,
            "point": o.point,
            "prob": round(o.implied_probability, 4) if o.implied_probability else None,
            "true_odds": round(o.true_odds, 2) if o.true_odds else None,
            "edge": round(edge, 2) if edge is not None else None
        })

    return templates.TemplateResponse("dev_odds.html", {
            "request": request, 
            "title": "Odds Explorer", 
            "headers": headers, 
            "rows": rows, 
            "bookmakers": bookmakers,
            "current_bookmaker_id": bookmaker_id, 
            "future_only": future_only, 
            "can_fetch": can_fetch, 
            "is_dev": settings.is_dev
        })

class QuickBetRequest(BaseModel):
    odd_id: int

@router.post("/odds/quick-bet", dependencies=[Depends(check_dev_mode)])
async def quick_bet(params: QuickBetRequest, db: AsyncSession = Depends(get_db)):
    # Fetch odd with relations
    stmt = (
        select(Odds, Market, Event, Bookmaker)
        .join(Odds.market)
        .join(Market.event)
        .join(Odds.bookmaker)
        .where(Odds.id == params.odd_id)
    )
    res = await db.execute(stmt)
    row = res.first()
    if not row:
        raise HTTPException(status_code=404, detail="Odd not found")
    
    o, m, e, b = row
    
    from app.db.models import Bet
    
    # Create snapshots
    event_snapshot = {
        "id": e.id,
        "sport_key": e.sport_key,
        "league_key": e.league_key,
        "commence_time": e.commence_time.isoformat() if e.commence_time else None,
        "home_team": e.home_team,
        "away_team": e.away_team
    }
    
    market_snapshot = {
        "key": m.key,
        "event_id": m.event_id
    }
    
    edge = (o.price / o.true_odds) - 1.0 if o.true_odds else 0.0
    
    odd_snapshot = {
        "selection": o.selection,
        "normalized_selection": o.normalized_selection,
        "price": o.price,
        "point": o.point,
        "url": o.url,
        "event_sid": o.event_sid,
        "market_sid": o.market_sid,
        "sid": o.sid,
        "implied_probability": o.implied_probability,
        "true_odds": o.true_odds,
        "edge": edge
    }
    
    new_bet = Bet(
        event_id=e.id,
        bookmaker_id=b.id,
        market_key=m.key,
        selection=o.normalized_selection,
        stake=10.0,
        price=o.price,
        status="open", # Direct to open because we are manually forcing it
        placed_at=datetime.now(timezone.utc),
        event_data=event_snapshot,
        market_data=market_snapshot,
        odd_data=odd_snapshot
    )
    
    db.add(new_bet)
    await db.commit()
    
    return {"status": "success", "bet_id": new_bet.id}


@router.post("/odds/fetch-live", dependencies=[Depends(check_dev_mode)])
async def fetch_live_odds(params: FetchLiveRequest, db: AsyncSession = Depends(get_db)):
    logs = []
    def log(msg): logs.append(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}")
    try:
        bm = await db.get(Bookmaker, params.bookmaker_id)
        if not bm: return {"status": "error", "message": "Bookmaker not found"}
        log(f"Initializing fetch for {bm.title} ({bm.key})")
        bm_instance = BookmakerFactory.get_bookmaker(bm.key, bm.config or {}, db)
        if not isinstance(bm_instance, APIBookmaker): return {"status": "error", "message": "Not an API instance"}
        log("Authorizing...")
        await bm_instance.authorize()
        log("Authorization successful")
        
        query = select(Event.id, Event.sport_key).join(Market).join(Odds).where(Odds.bookmaker_id == bm.id)
        if params.future_only:
            from datetime import timezone
            buffer_time = datetime.now(timezone.utc) - timedelta(minutes=120)
            query = query.where(Event.commence_time >= buffer_time)
        query = query.distinct().limit(50)
        result = await db.execute(query); events = result.all()
        if not events:
            log("No events found."); return {"status": "success", "logs": logs, "message": "No events"}
        log(f"Found {len(events)} events.")
        sports_map = {}
        for eid, sport_key in events:
            if sport_key not in sports_map:
                sports_map[sport_key] = []
            sports_map[sport_key].append(eid)
            
        total_updated = 0
        for sport_key, ext_ids in sports_map.items():
            log(f"Fetching {sport_key}...")
            try:
                # Use obtain_odds if implemented
                raw_odds = await bm_instance.obtain_odds(sport_key, ext_ids, log=log)
                log(f"Received {len(raw_odds)} odds.")
                for entry in raw_odds:
                    ext_event_id = entry.get("external_event_id")
                    mkt_key = entry.get("market_key")
                    sel = entry.get("selection")
                    new_price = entry.get("price")
                    new_point = entry.get("point")
                    
                    stmt = select(Odds).join(Market).join(Event).where(
                        Event.id == ext_event_id, 
                        Market.key == mkt_key, 
                        Odds.bookmaker_id == bm.id, 
                        Odds.selection == sel
                    )
                    odds_record = (await db.execute(stmt)).scalars().first()
                    if odds_record:
                        old_price = odds_record.price
                        odds_record.price = new_price
                        odds_record.point = new_point
                        odds_record.bet_limit = entry.get("bet_limit")
                        
                        # Update implied probability
                        # if "implied_probability" in entry:
                        #     odds_record.implied_probability = entry["implied_probability"]
                        # else:
                        #     odds_record.implied_probability = round(1.0 / new_price, 4) if new_price > 0 else None

                        # Persist discovered IDs
                        if entry.get("sid"): odds_record.sid = entry["sid"]
                        if entry.get("market_sid"): odds_record.market_sid = entry["market_sid"]
                        if entry.get("event_sid"): odds_record.event_sid = entry["event_sid"]
                        
                        # Recalculate Edge if True Odds exist
                        if odds_record.true_odds and odds_record.true_odds > 0:
                            # Edge = (Price / True Odds) - 1
                            # This is done in the view, but good to have in DB if we store it (none in model)
                            pass

                        # Also update timestamp if model supports it
                        if hasattr(odds_record, "updated_at"): 
                            odds_record.updated_at = datetime.now(timezone.utc)
                        
                        log(f"  UPDT: {ext_event_id} | {sel}: {old_price} -> {new_price}")
                        total_updated += 1
            except Exception as e:
                log(f"Error: {str(e)}")
        await db.commit()
        log(f"Sync complete. Updated: {total_updated}")
        return {"status": "success", "logs": logs}
    except Exception as e:
        log(f"CRITICAL: {str(e)}"); import traceback; log(traceback.format_exc())
        return {"status": "error", "message": str(e), "logs": logs}
@router.websocket("/odds/ws")
async def websocket_dev_odds(
    websocket: WebSocket,
    bookmaker_id: Optional[str] = None,
    future_only: str = "false",
    db: AsyncSession = Depends(get_db)
):
    await websocket.accept()
    
    # Parse params
    bm_id = None
    if bookmaker_id:
        try:
            bm_id = int(bookmaker_id)
        except:
            pass
            
    is_future_only = future_only.lower() == "true"
    
    try:
        while True:
            # Query logic similar to view_odds
            # We need a new session for each loop iteration if we want fresh data
            # Use AsyncSessionLocal directly like in trade.py
            async with AsyncSessionLocal() as session:
                query = (
                    select(Odds, Market, Event, Bookmaker)
                    .join(Odds.market)
                    .join(Market.event)
                    .join(Odds.bookmaker)
                )
                
                if bm_id:
                    query = query.where(Odds.bookmaker_id == bm_id)
                    
                if is_future_only:
                    from datetime import timezone
                    now_utc = datetime.now(timezone.utc)
                    buffer_time = now_utc - timedelta(minutes=120)
                    query = query.where(Event.commence_time >= buffer_time)
                    query = query.order_by(Event.commence_time.asc())
                else:
                    query = query.order_by(Odds.id.desc())
                    
                query = query.limit(500)
                
                result = await session.execute(query)
                rows_data = result.all()
                
                # Transform data for template
                rows = []
                for o, m, e, b in rows_data:
                    edge = ((o.price / o.true_odds) - 1) * 100 if o.true_odds and o.true_odds > 0 else None
                    rows.append({
                        "id": o.id,
                        "game": f"{e.home_team} vs {e.away_team}",
                        "sport": e.sport_key,
                        "start_time": e.commence_time.isoformat() if e.commence_time.tzinfo else e.commence_time.isoformat() + "Z",
                        "market": m.key,
                        "selection": o.selection,
                        "selection_norm": o.normalized_selection,
                        "bookie": b.title,
                        "bookie_id": b.id,
                        "event_id": e.id,
                        "price": o.price,
                        "point": o.point,
                        "prob": round(o.implied_probability, 4) if o.implied_probability else None,
                        "true_odds": round(o.true_odds, 2) if o.true_odds else None,
                        "edge": round(edge, 2) if edge is not None else None
                    })
                
                # Render Partial
                # We need to manually use jinja2 template
                template = templates.get_template("partials/dev_odds_rows.html")
                html_content = template.render(rows=rows)
                
                await websocket.send_json({"html": html_content})
            
            await asyncio.sleep(5)
            
            # Check for client close?
            # receive_text might block, so we rely on send_json raising error if closed
            # Correct approach: loop and sleep is aggressive
            # Better: use asyncio.wait_for(websocket.receive_text(), timeout) logic?
            # But we are PUSHING data.
            # We can check websocket.client_state?
            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Dev WS Error: {e}")
        try:
             await websocket.close()
        except:
             pass
