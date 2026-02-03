from typing import Dict, List, Any
from fastapi import WebSocket
import logging
import asyncio
from app.services.analytics.trade_finder import TradeFinderService
from app.db.session import AsyncSessionLocal
from app.core.config import settings

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        # Key: preset_id, Value: List of WebSockets
        self.active_connections: Dict[int, List[WebSocket]] = {}
        self.my_bets_connections: List[WebSocket] = []
        # Key: preset_id, Value: Cache of last sent data
        self.polling_tasks: Dict[int, asyncio.Task] = {}
        self.sync_tasks: Dict[int, asyncio.Task] = {} # Track running sync tasks
        self.trade_finder = TradeFinderService()
        self.is_running = False
        self._loop_task: asyncio.Task | None = None

    async def connect(self, websocket: WebSocket, preset_id: int):
        await websocket.accept()
        if preset_id not in self.active_connections:
            self.active_connections[preset_id] = []
            
        self.active_connections[preset_id].append(websocket)
        logger.info(f"Client connected to preset {preset_id}. Total clients: {len(self.active_connections[preset_id])}")

        if not self.is_running:
            self.start_global_loop()
    
    async def connect_my_bets(self, websocket: WebSocket):
        await websocket.accept()
        self.my_bets_connections.append(websocket)
        logger.info(f"Client connected to My Bets. Total clients: {len(self.my_bets_connections)}")
        
    def disconnect_my_bets(self, websocket: WebSocket):
        if websocket in self.my_bets_connections:
            self.my_bets_connections.remove(websocket)
            logger.info("Client disconnected from My Bets.")

    def disconnect(self, websocket: WebSocket, preset_id: int):
        if preset_id in self.active_connections:
            if websocket in self.active_connections[preset_id]:
                self.active_connections[preset_id].remove(websocket)
            
            if not self.active_connections[preset_id]:
                del self.active_connections[preset_id]
                
        logger.info(f"Client disconnected from preset {preset_id}.")
    
    async def broadcast_my_bets(self, message: Dict[str, Any]):
        to_remove = []
        for connection in self.my_bets_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending to My Bets client: {e}")
                to_remove.append(connection)
        
        for conn in to_remove:
            self.disconnect_my_bets(conn)

    async def broadcast(self, preset_id: int, message: Dict[str, Any]):
        if preset_id not in self.active_connections:
            return
            
        to_remove = []
        for connection in self.active_connections[preset_id]:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending to client: {e}")
                to_remove.append(connection)
        
        for conn in to_remove:
            self.disconnect(conn, preset_id)

    async def stop(self):
        logger.info("Stopping Connection Manager...")
        self.is_running = False
        
        # Cancel Global Loop
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
            
        # Cancel all running sync tasks
        for preset_id, task in self.sync_tasks.items():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.sync_tasks.clear()
        logger.info("Connection Manager Stopped.")

    def start_global_loop(self):
        if self.is_running:
            return
        self.is_running = True
        self._loop_task = asyncio.create_task(self._global_poll_loop())

    async def _global_poll_loop(self):
        logger.info("Starting Global Trade Feed Polling Loop")
        
        # Cache to track previous odds for change detection per preset
        # Key: preset_id, Value: Dict[opp_key, price]
        previous_odds_cache: Dict[int, Dict[str, float]] = {}

        while True:
            try:
                if not self.active_connections:
                    # If no one is connected anywhere, sleep longer?
                    # Or just wait standard time.
                    await asyncio.sleep(1) # check more often if empty to be responsive? 
                    # Actually no, if empty, we just loop and do nothing.
                    continue

                active_presets = list(self.active_connections.keys())
                
                async with AsyncSessionLocal() as db:
                    for preset_id in active_presets:
                        # Double check if still has connections (async gap)
                        if preset_id not in self.active_connections:
                            continue

                        try:
                            # 1. Scan
                            opportunities = await self.trade_finder.scan_opportunities(db, preset_id)
                            
                            # 2. Process Changes & Serialize
                            opportunities_json = []
                            odds_increased = []
                            odds_decreased = []
                            
                            if preset_id not in previous_odds_cache:
                                previous_odds_cache[preset_id] = {}
                            
                            current_cache = previous_odds_cache[preset_id]
                            new_cache = {}

                            for opp in opportunities:
                                opp_key = f"{opp.event.id}_{opp.bookmaker.key}_{opp.market.key}_{opp.odd.normalized_selection}"
                                current_price = opp.odd.price
                                
                                # Check changes
                                if opp_key in current_cache:
                                    prev_price = current_cache[opp_key]
                                    if current_price > prev_price:
                                        odds_increased.append(opp_key)
                                    elif current_price < prev_price:
                                        odds_decreased.append(opp_key)
                                
                                new_cache[opp_key] = current_price
                                
                                opp_dict = opp.to_dict()
                                opp_dict["row_id"] = opp_key
                                opportunities_json.append(opp_dict)
                            
                            # Update cache
                            previous_odds_cache[preset_id] = new_cache

                            # 3. Broadcast
                            response_data = {
                                "opportunities": opportunities_json,
                                "odds_increased": odds_increased,
                                "odds_decreased": odds_decreased
                            }
                            
                            await self.broadcast(preset_id, response_data)

                            # So we CANNOT pass 'db' here because 'db' belongs to this context manager.
                            # 4. Trigger Sync (Background)
                            # Prevent overlapping sync tasks for the same preset
                            # If the previous sync is still running, we skip triggering a new one
                            current_task = self.sync_tasks.get(preset_id)
                            
                            if opportunities:
                                if not current_task or current_task.done():
                                    # Clean up referenced done task? (Not strictly needed but good for purity)
                                    task = asyncio.create_task(self._safe_sync(opportunities))
                                    self.sync_tasks[preset_id] = task
                                else:
                                    logger.debug(f"Skipping sync for preset {preset_id} - previous sync still running.")

                        except Exception as e:
                            logger.error(f"Error processing preset {preset_id}: {e}")
                            
            except Exception as e:
                logger.error(f"Global Loop Critical Error: {e}")
                await asyncio.sleep(5)
            
            await asyncio.sleep(5)

    async def _safe_sync(self, opportunities):
        """Helper to run sync with its own session"""
        try:
             async with AsyncSessionLocal() as db:
                await self.trade_finder.sync_live_odds(db, opportunities)
        except Exception as e:
            logger.error(f"Error in background sync: {e}")

manager = ConnectionManager()
