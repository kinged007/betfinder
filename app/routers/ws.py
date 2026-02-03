from typing import Dict, Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from app.services.connection_manager import manager
from app.core.security import check_session
import logging

router = APIRouter(dependencies=[Depends(check_session)])
logger = logging.getLogger(__name__)

@router.websocket("/my-bets")
async def my_bets_ws(websocket: WebSocket):
    await manager.connect_my_bets(websocket)
    try:
        while True:
            # We just keep the connection open. Clients don't send anything for now.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_my_bets(websocket)
    except Exception as e:
        logger.error(f"MyBets WebSocket error: {e}")
        manager.disconnect_my_bets(websocket)

@router.websocket("/tradefeed/{preset_id}")
async def trade_feed_ws(websocket: WebSocket, preset_id: int):
    # Hand off connection to manager
    await manager.connect(websocket, preset_id)
    try:
        # Keep connection open until client disconnects
        while True:
            # We don't expect messages from client, but we need to wait
            # If client sends close, this raises WebSocketDisconnect
            await websocket.receive_text()
            
    except WebSocketDisconnect:
        manager.disconnect(websocket, preset_id)
    except Exception as e:
        logger.error(f"TradeFeed WebSocket Error: {e}")
        manager.disconnect(websocket, preset_id)
        # Try to close cautiously
        try:
             await websocket.close(code=1011)
        except:
             pass
