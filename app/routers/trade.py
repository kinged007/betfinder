
import asyncio
from typing import Dict, Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import AsyncSessionLocal
from app.services.analytics.trade_finder import TradeFinderService
import random
from app.core.security import check_session

router = APIRouter(dependencies=[Depends(check_session)])

@router.websocket("/tradefeed/{preset_id}")
async def trade_feed(websocket: WebSocket, preset_id: int):
    await websocket.accept()
    
    trade_finder = TradeFinderService()
    # Cache to track previous odds for change detection
    # Key: f"{event_id}_{bookmaker_key}_{market}_{selection}"
    previous_odds_cache: Dict[str, float] = {}
    
    try:
        while True:
            # Create a new session for each scan to ensure fresh data and avoiding stale sessions in loop
            async with AsyncSessionLocal() as db:
                # Get opportunities as structured objects
                opportunities = await trade_finder.scan_opportunities(db, preset_id)

                # Serialize to JSON for frontend and detect changes
                opportunities_json = []
                odds_increased = []
                odds_decreased = []

                for opp in opportunities:
                    # Create unique key for this opportunity
                    opp_key = f"{opp.event.id}_{opp.bookmaker.key}_{opp.market.key}_{opp.odd.normalized_selection}"
                    current_price = opp.odd.price
                    
                    # Check if odds changed
                    if opp_key in previous_odds_cache:
                        previous_price = previous_odds_cache[opp_key]
                        if current_price > previous_price:
                            odds_increased.append(opp_key)
                        elif current_price < previous_price:
                            odds_decreased.append(opp_key)
                    
                    # Update cache
                    previous_odds_cache[opp_key] = current_price
                    
                    # Serialize opportunity with the unique key
                    opp_dict = opp.to_dict()
                    opp_dict["row_id"] = opp_key
                    opportunities_json.append(opp_dict)
                
                # Send data with change indicators
                response_data = {
                    "opportunities": opportunities_json,
                    "odds_increased": odds_increased,
                    "odds_decreased": odds_decreased
                }
                await websocket.send_json(response_data)
                
                # Trigger background sync (non-blocking)
                # This will update the DB with live odds from API bookmakers
                if opportunities:
                    asyncio.create_task(trade_finder.sync_live_odds(db, opportunities))

            # Sleep for X seconds before next scan
            await asyncio.sleep(5) 
            
    except WebSocketDisconnect as e:
        # Client disconnected
        print("Client disconnected", e)
        pass
    except Exception as e:
        # Reason must be <= 123 bytes for a close frame
        reason = str(e)[:120]
        print("Client disconnected", reason)
        await websocket.close(code=1011, reason=reason)
