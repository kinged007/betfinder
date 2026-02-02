
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.db.session import AsyncSessionLocal
from app.services.ingester import DataIngester
from app.services.the_odds_api import TheOddsAPIClient
from app.services.standardizer import DataStandardizer
from app.repositories.mapping import MappingRepository
from app.db.models import Preset, PresetHiddenItem, Bet, Bookmaker, Event, Odds, Market
from sqlalchemy import select, delete, update, and_, or_
from datetime import datetime, timezone, timedelta
from app.core.config import settings
from app.services.analysis import OddsAnalysisService
from app.services.bookmakers.base import BookmakerFactory
from sqlalchemy.orm import selectinload
from app.services.analytics.trade_finder import TradeFinderService
from app.core.enums import BetResult, BetStatus
import logging
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

async def job_fetch_sports():
    async with AsyncSessionLocal() as db:
        # Manually instantiate dependencies since we are outside request context
        client = TheOddsAPIClient()
        mapping_repo = MappingRepository()
        standardizer = DataStandardizer(mapping_repo)
        ingester = DataIngester(client, standardizer)
        
        await ingester.sync_sports(db)

async def job_analyze_odds():
    async with AsyncSessionLocal() as db:
        await OddsAnalysisService.calculate_benchmark_values(db)

async def job_preset_sync():
    
    logger.info("Starting scheduled Preset Data Sync job...")
    
    async with AsyncSessionLocal() as db:
        # 1. Fetch active presets
        result = await db.execute(select(Preset).where(Preset.active == True))
        presets = result.scalars().all()
        
        now = datetime.now(timezone.utc)
        interval = timedelta(hours=settings.PRESET_SYNC_INTERVAL_HOURS)
        
        synced_any = False
        
        for preset in presets:
            # Check if sync is due
            should_sync = False
            if not preset.last_sync_at:
                should_sync = True
            else:
                # Handle timezone aware comparison
                last_sync = preset.last_sync_at
                if last_sync.tzinfo is None:
                    last_sync = last_sync.replace(tzinfo=timezone.utc)
                
                if now - last_sync >= interval:
                    should_sync = True
            
            if should_sync:
                client = TheOddsAPIClient()
                mapping_repo = MappingRepository()
                standardizer = DataStandardizer(mapping_repo)
                ingester = DataIngester(client, standardizer)
                
                try:
                    await ingester.sync_data_for_preset(db, preset)
                    preset.last_sync_at = now
                    db.add(preset)
                    await db.commit()
                    synced_any = True
                except Exception as e:
                    logger.error(f"Failed to sync for preset {preset.name}: {e}")
        
        if synced_any:
            logger.info("New data fetched, triggering analysis...")
            await OddsAnalysisService.calculate_benchmark_values(db)
        else:
            logger.info("No presets due for sync.")

async def job_cleanup_hidden_items():
    async with AsyncSessionLocal() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        result = await db.execute(delete(PresetHiddenItem).where(PresetHiddenItem.expiry_at < cutoff))
        await db.commit()
        if result.rowcount > 0:
            logger.info(f"Cleaned up {result.rowcount} expired hidden items.")

async def job_auto_trade():
    """Execute auto-trades for presets with auto_trade enabled."""
    from app.services.auto_trade import AutoTradeService
    
    async with AsyncSessionLocal() as db:
        try:
            stats = await AutoTradeService.execute_auto_trades(db)
            if stats["bets_placed"] > 0 or stats["errors"] > 0:
                logger.info(
                    f"Auto-trade completed: {stats['bets_placed']} bets placed, "
                    f"{stats['errors']} errors across {stats['presets_processed']} presets"
                )
        except Exception as e:
            logger.error(f"Auto-trade job failed: {e}", exc_info=True)

async def job_global_odds_live_sync():
    """Update odds for all API bookmakers and future events."""
    async with AsyncSessionLocal() as db:
        try:
            service = TradeFinderService()
            await service.sync_all_api_bookmaker_odds(db)
        except Exception as e:
            logger.error(f"Global live sync job failed: {e}", exc_info=True)

async def job_get_results():
    """
    Fetch results for past events from a designated 'source' bookmaker.
    Update the Odds table with these results.
    """
    logger.info("Starting scheduled Get Results job...")
    async with AsyncSessionLocal() as db:
        # 1. Find the bookmaker configured for results
        result = await db.execute(select(Bookmaker).where(Bookmaker.active == True, Bookmaker.model_type == 'api'))
        bookmakers = result.scalars().all()
        
        source_bookie_model = None
        for b in bookmakers:
            if b.config and b.config.get("use_for_results") == True:
                source_bookie_model = b
                break
        
        if not source_bookie_model:
            logger.info("No bookmaker configured with use_for_results=True. Skipping result fetch.")
            return

        # 2. Find events: commence_time < now - 120mins, and are missing a result
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=100)
        
        stmt = (
            select(Event)
            .join(Market, Event.id == Market.event_id)
            .join(Odds, Market.id == Odds.market_id)
            .where(
                Odds.bookmaker_id == source_bookie_model.id,
                Odds.result == None,
                Event.commence_time < cutoff,
                Event.commence_time > now - timedelta(days=7)
            )
            .distinct()
        )
        
        result = await db.execute(stmt)
        events_to_check = result.scalars().all()
        
        if not events_to_check:
            logger.info("No past events found needing results.")
            return

        logger.info(f"Checking results for {len(events_to_check)} events using {source_bookie_model.title}...")
        
        service = BookmakerFactory.get_bookmaker(source_bookie_model.key, source_bookie_model.config or {}, db)
        
        # Batch Fetch
        event_ids = [str(e.id) for e in events_to_check]
        try:
            results = await service.get_events_results(event_ids)
            
            if not results:
                logger.info("No results returned from bookmaker.")
                return

            logger.info(f"Received {len(results)} result entries. Updating database...")
            
            # Group updates by event to be cleaner or just bulk update?
            # We can iterate results and update.
            count = 0
            for result_item in results:
                res_status = result_item.get("result") # win, loss, void
                sel_norm = result_item.get("selection") 
                mkt_key = result_item.get("market_key") 
                ev_id = result_item.get("event_id") # Internal ID string
                
                if res_status and sel_norm and ev_id and mkt_key:
                    # Update all odds for this event, market, selection
                    subquery = select(Market.id).where(Market.event_id == ev_id)
                    subquery = subquery.where(Market.key == mkt_key)
                        
                    res = await db.execute(
                        update(Odds)
                        .where(
                            Odds.market_id.in_(subquery),
                            Odds.normalized_selection == sel_norm
                        )
                        .values(result=res_status)
                    )
                    count += res.rowcount
                print(f"Updated Odds with Result:", result_item)
            logger.info(f"Updated {count} odds entries with results.")

        except Exception as e:
            logger.error(f"Error in batch fetching results: {e}")

        await db.commit()

async def job_settle_bets():
    """
    Settle bets based on results present in the Odds table.
    """
    logger.info("Starting scheduled Bet Settlement job...")
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        # Look for bets on events that started at least 100 mins ago
        start_cutoff = now - timedelta(minutes=100)
        
        stmt = (
             select(Bet)
             .join(Event)
             .join(Bookmaker)
             .options(selectinload(Bet.bookmaker))
             .where(
                #  Bookmaker.model_type == "api",
                 Bet.status.in_(["pending", "open", "placed", "manual", "auto"]),
                 Event.commence_time < start_cutoff,
                 Event.commence_time > now - timedelta(days=7)
             )
        )
        
        result = await db.execute(stmt)
        bets = result.scalars().all()
        
        if not bets:
            logger.info("No bets due for settlement check.")
            return
            
        logger.info(f"Checking settlement for {len(bets)} bets...")

        # Map bookmaker_id to amount to CREDIT (add) to balance
        bookmakers_credits = {} 

        for bet in bets:
            try:
                # Check for result in Odds table
                # Match by event_id, market_key, selection (exact or normalized?)
                # Try exact selection match primarily (normalized is safer if we have it on bet)
                check_stmt = (
                    select(Odds.result)
                    .join(Market)
                    .where(
                        Market.event_id == bet.event_id,
                        Market.key == bet.market_key,
                        Odds.normalized_selection == bet.selection,
                        Odds.result.is_not(None)
                    )
                    .limit(1)
                )
                res = await db.execute(check_stmt)
                outcome = res.scalar_one_or_none()

                logger.info(f"Bet {bet.id} outcome: {outcome}")

                if outcome:
                    new_status = outcome.lower()
                    payout_val = 0.0
                    balance_credit = 0.0
                    
                    if new_status == BetResult.WON.value:
                        payout_val = bet.stake * bet.price
                        balance_credit = payout_val
                    elif new_status == BetResult.VOID.value:
                        payout_val = bet.stake
                        balance_credit = payout_val
                    elif new_status == BetResult.LOST.value:
                        payout_val = -bet.stake
                        balance_credit = 0.0
                    
                    if new_status != bet.status:
                         logger.info(f"Settling bet {bet.id} as {new_status} with payout {payout_val}")
                         bet.status = new_status
                         bet.payout = payout_val
                         bet.settled_at = now
                         db.add(bet)
                         
                         # Track credit for bookmaker balance update
                         if balance_credit > 0:
                             current = bookmakers_credits.get(bet.bookmaker.id, 0.0)
                             bookmakers_credits[bet.bookmaker.id] = current + balance_credit
                             
            except Exception as e:
                logger.error(f"Error settling bet {bet.id}: {e}")
        
        await db.commit()
        
        # Update Balances
        # We iterate over all bookmakers that had activity (credits) OR just all active ones?
        # The prompt implies updating those we touched. 
        # But if we rely on API, we might want to update anyway. 
        # For efficiency, let's update those we have credits for.
        
        for bk_id, credit_amount in bookmakers_credits.items():
            try:
                # Re-fetch bookmaker to avoid session issues
                bk = await db.get(Bookmaker, bk_id)
                if not bk: continue
                
                service = BookmakerFactory.get_bookmaker(bk.key, bk.config or {}, db)
                
                # Try API Fetch
                api_balance = None
                try:
                    bal_res = await service.get_account_balance()
                    if bal_res and "balance" in bal_res:
                        api_balance = float(bal_res["balance"])
                except Exception:
                    pass # Fallback to manual
                
                if api_balance is not None:
                     bk.balance = api_balance
                     logger.info(f"Updated balance for {bk.title} from API: {bk.balance}")
                else:
                     # Manual Update
                     bk.balance += credit_amount
                     logger.info(f"Updated balance for {bk.title} via calculation: +{credit_amount} (New: {bk.balance})")
                
                db.add(bk)
            except Exception as e:
                logger.error(f"Failed to update balance for bookmaker {bk_id}: {e}")
        
        await db.commit()

def start_scheduler(run_immediately: bool = False):
    """
    Start the background scheduler.
    If run_immediately is True, all jobs will trigger their first execution right now.
    """
    def next_time(delta=0):
        if run_immediately:
            return datetime.now(timezone.utc) + timedelta(minutes=delta)
        return None
    
    scheduler.add_job(job_preset_sync, 'interval', minutes=15, next_run_time=next_time(1))
    scheduler.add_job(job_global_odds_live_sync, 'interval', minutes=25, next_run_time=next_time(2))
    scheduler.add_job(job_analyze_odds, 'interval', minutes=30, next_run_time=next_time(3))
    
    # New jobs
    # get_results runs every 30 mins
    scheduler.add_job(job_get_results, 'interval', minutes=30, next_run_time=next_time(4))
    # settle_bets runs every 35 mins
    scheduler.add_job(job_settle_bets, 'interval', minutes=35, next_run_time=next_time(5))
    
    scheduler.add_job(job_auto_trade, 'interval', minutes=5, next_run_time=next_time(6))
    scheduler.add_job(job_cleanup_hidden_items, 'interval', hours=12, next_run_time=next_time(7))
    # makes API requests on each run, so we should minimise this. Maybe schedule to run on mondays only? 
    scheduler.add_job(job_fetch_sports, 'interval', weeks=1)
    
    scheduler.start()

def stop_scheduler():
    logger.info("Stopping scheduler...")
    try:
        scheduler.remove_all_jobs()
        scheduler.shutdown(wait=False)
    except Exception as e:
        logger.error(f"Error during scheduler shutdown: {e}")
