
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
from app.services.analytics.trade_finder import TradeFinderService
from app.core.enums import BetResult, BetStatus
from app.services.notifications.manager import NotificationManager
import logging
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=timezone.utc)

async def job_heartbeat():
    logger.debug(f"Scheduler Heartbeat: {datetime.now(timezone.utc)}")

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
        # Optimization: Filter due presets in SQL
        now = datetime.now(timezone.utc)
        interval = timedelta(hours=settings.PRESET_SYNC_INTERVAL_HOURS)
        cutoff = now - interval
        
        stmt = select(Preset).where(
            Preset.active == True,
            or_(
                Preset.last_sync_at == None,
                Preset.last_sync_at < cutoff
            )
        )
        result = await db.execute(stmt)
        presets = result.scalars().all()
        
        if not presets:
            logger.info("No presets due for sync.")
            return

        logger.info(f"Found {len(presets)} presets due for sync.")

        # Optimization: Instantiate dependencies ONCE
        client = TheOddsAPIClient()
        mapping_repo = MappingRepository()
        standardizer = DataStandardizer(mapping_repo)
        ingester = DataIngester(client, standardizer)
        
        # Fetch active Bookmakers ONCE
        bk_res = await db.execute(select(Bookmaker).where(Bookmaker.active == True, Bookmaker.model_type == 'api'))
        active_bookmakers = bk_res.scalars().all()
        
        # --- Aggregation Phase ---
        # Map: league_key -> { 'presets': [Preset], 'markets': set() }
        league_map = {}
        
        for preset in presets:
            # Determine leagues for this preset
            leagues = preset.leagues or []
            # Check if we should auto-include popular leagues for these sports
            # Since leagues may contain data from previous save, we empty it if 
            # preset wants popular leagues instead of specific leagues
            if preset.show_popular_leagues:
                leagues = []
            
            if not leagues and not preset.sports:
                leagues = ['upcoming']
            elif not leagues and preset.sports and not preset.show_popular_leagues: 
                # Preset has no selected leagues.... 
                # Fetching all leagues will consume a lot of api credits
                # So, we can set to upcoming and skip
                leagues = ['upcoming']
            elif preset.sports and preset.show_popular_leagues:
                # Preset wants popular leagues for sport. Ignore leagues list.
                leagues = []
                logger.info(f"Preset {preset.name} has sports but no leagues. Fetching popular leagues...")
                # Fetch popular leagues for these sports
                # We need to query the League table
                # We can cache this or query per preset. Querying is safer.
                
                # We need a synchronous-style query execution here or just await
                # preset.sports is a list of sport keys
                
                # We need to handle this efficiently. multiple presets might need this.
                # But let's do it simple first.
                stmt_pop = select(League.key).where(
                    League.sport_key.in_(preset.sports),
                    League.popular == True,
                    League.active == True
                )
                pop_res = await db.execute(stmt_pop)
                pop_leagues = pop_res.scalars().all()
                
                if pop_leagues:
                    leagues = list(pop_leagues)
                    logger.info(f"  -> Added {len(leagues)} popular leagues for preset {preset.name}")
                else:
                    logger.debug(f"  -> No popular leagues found for sports: {preset.sports}")
            
            # Determine markets
            p_markets = set(preset.markets) if preset.markets else {"h2h", "spreads", "totals"}
            
            for league_key in leagues:
                if league_key not in league_map:
                    league_map[league_key] = {
                        'presets': [],
                        'markets': set()
                    }
                league_map[league_key]['presets'].append(preset)
                league_map[league_key]['markets'].update(p_markets)
        
        # --- Execution Phase ---
        synced_any = False
        
        for league_key, data in league_map.items():
            combined_markets = list(data['markets'])
            markets_str = ",".join(combined_markets)
            associated_presets = data['presets']
            preset_names = [p.name for p in associated_presets]
            
            try:
                # Sync League (One Request)
                await ingester.sync_league(
                    db, 
                    league_key=league_key, 
                    markets=markets_str, 
                    active_bookmakers=active_bookmakers,
                    preset_names=preset_names
                )
                
                # Update last_sync_at for all associated presets
                # We do this individually to be safe, but can commit in batch
                for p in associated_presets:
                    p.last_sync_at = datetime.now(timezone.utc)
                    db.add(p)
                
                await db.commit()
                synced_any = True
                
            except Exception as e:
                logger.error(f"Failed to sync league {league_key}: {e}")
                # We don't rollback everything, just skip updating timestamps for these presets?
                # Actually, ingester exceptions might have already rolled back internally or not.
                # ingester methods usually do their own commits for data.
                # If ingester failed, we probably shouldn't update last_sync_at.
                # But we should continue to next league.
                pass

        if synced_any:
            logger.info("New data fetched, triggering analysis...")
            await OddsAnalysisService.calculate_benchmark_values(db)
            
            # --- Notification Phase ---
            logger.info("Analysis complete. Checking for trade notifications...")
            # We iterate ALL synced presets.
            # Since presets might be in multiple leagues, we might process them multiple times?
            # Let's simple iterate the original list 'presets' and check if they were updated?
            # Or just check all due presets again?
            # Efficient way: unique set of preset IDs that were part of successful syncs.
            # But simpler: just check all 'presets' we loaded initially.
            
            try:
                for preset_obj in presets:
                    # Reload to get fresh state if needed, though mostly config we need
                    preset = await db.get(Preset, preset_obj.id)
                    if not preset: continue
                    
                    # Only check if it was actually synced?
                    # If we failed to sync its league, we probably shouldn't notify?
                    # But checking opportunities is harmless (just won't find new ones if no data).
                    
                    notif_enabled = preset.other_config.get("notification_new_bet", "true")
                    if notif_enabled == "true":
                        trade_finder = TradeFinderService()
                        opportunities = await trade_finder.scan_opportunities(db, preset.id)
                        
                        if opportunities:
                            logger.info(f"Found {len(opportunities)} potential trades for preset {preset.name}")
                            notification_manager = NotificationManager(db)
                            for opp in opportunities:
                                await notification_manager.send_trade_notification(preset, opp)
            except Exception as e:
                logger.error(f"Error in notification phase: {e}")

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

        # Optimization: Batch fetch results
        # Collect unique composites (event_id, market_key, normalized_selection)
        candidates = []
        for bet in bets:
             candidates.append((bet.event_id, bet.market_key, bet.selection))
        
        if not candidates:
             return

        # Build a query for all relevant Odds with results
        # We need to filter by combinations.
        # Efficient way: WHERE (event_id, market_key, normalized_selection) IN (...) is not standard SQL cross-db.
        # Safer way: fetch all ODDS with results for these Events and create a map.
        
        relevant_event_ids = list(set([b.event_id for b in bets]))
        
        odds_stmt = (
            select(Odds, Market.event_id, Market.key)
            .join(Market)
            .where(
                Market.event_id.in_(relevant_event_ids),
                Odds.result.is_not(None)
            )
        )
        odds_res = await db.execute(odds_stmt)
        odds_rows = odds_res.all()
        
        # Create Result Map: (event_id, market_key, selection_norm) -> Result
        result_map = {}
        for odd, ev_id, mkt_key in odds_rows:
             key = (ev_id, mkt_key, odd.normalized_selection)
             result_map[key] = odd.result

        # Map bookmaker_id to amount to CREDIT (add) to balance
        bookmakers_credits = {} 

        total_settled = 0
        for bet in bets:
            try:
                # Lookup Result
                # Try exact normalized selection
                outcome = result_map.get((bet.event_id, bet.market_key, bet.selection))
                
                # logger.debug(f"Bet {bet.id} outcome: {outcome}")

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
                         
                         total_settled += 1
                         
                         # Track credit for bookmaker balance update
                         if balance_credit > 0:
                             current = bookmakers_credits.get(bet.bookmaker.id, 0.0)
                             bookmakers_credits[bet.bookmaker.id] = current + balance_credit
                             
            except Exception as e:
                logger.error(f"Error settling bet {bet.id}: {e}")
        
        await db.commit()
        logger.info(f"Settled {total_settled} bets.")
        
        if total_settled > 0:
             # Broadcast update to frontend
             try:
                 from app.services.connection_manager import manager
                 await manager.broadcast_my_bets({"type": "bets_updated"})
             except Exception as e:
                 logger.error(f"Failed to broadcast bet updates: {e}")
        
        # Update Balances
        for bk_id, credit_amount in bookmakers_credits.items():
            try:
                # Re-fetch bookmaker to avoid session issues
                bk = await db.get(Bookmaker, bk_id)
                if not bk: continue
                
                # Check API Balance if API bookmaker
                # We can do this check safely
                service = None
                if bk.model_type == 'api' and bk.config:
                    try:
                        service = BookmakerFactory.get_bookmaker(bk.key, bk.config, db)
                    except:
                        pass
                
                api_balance = None
                if service:
                    try:
                        bal_res = await service.get_account_balance()
                        if bal_res and "balance" in bal_res:
                            api_balance = float(bal_res["balance"])
                    except Exception:
                        pass
                
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
    If run_immediately is True, all jobs will trigger their first execution right now (staggered).
    If False, they will run after their first interval passes.
    """
    logger.info(f"Initializing Scheduler (UTC time: {datetime.now(timezone.utc)})")
    
    def get_trigger_args(delta_minutes=0):
        if run_immediately:
            # Stagger start by delta_minutes
            run_time = datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)
            return {"next_run_time": run_time}
        return {}
    
    # Heartbeat every minute - Always start soon for diagnostics
    scheduler.add_job(job_heartbeat, 'interval', minutes=1, next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10))

    scheduler.add_job(job_preset_sync, 'interval', minutes=15, **get_trigger_args(1))
    scheduler.add_job(job_global_odds_live_sync, 'interval', minutes=25, **get_trigger_args(2))
    scheduler.add_job(job_analyze_odds, 'interval', minutes=30, **get_trigger_args(3))
    
    # New jobs
    # get_results runs every 30 mins
    scheduler.add_job(job_get_results, 'interval', minutes=30, **get_trigger_args(4))
    # settle_bets runs every 35 mins
    scheduler.add_job(job_settle_bets, 'interval', minutes=35, **get_trigger_args(5))
    
    scheduler.add_job(job_auto_trade, 'interval', minutes=5, **get_trigger_args(6))
    scheduler.add_job(job_cleanup_hidden_items, 'interval', hours=12, **get_trigger_args(7))
    
    # makes API requests on each run, so we should minimise this. Maybe schedule to run on mondays only? 
    # For weekly jobs, we probably don't want to run immediately on every restart if run_immediately is set? 
    # But adhering to the flag:
    scheduler.add_job(job_fetch_sports, 'interval', weeks=1, **get_trigger_args(8))
    
    scheduler.start()
    logger.info("Scheduler started successfully.")
    for job in scheduler.get_jobs():
        logger.info(f"Scheduled job {job.name} - Next run: {job.next_run_time}")

def stop_scheduler():
    logger.info("Stopping scheduler...")
    try:
        scheduler.remove_all_jobs()
        scheduler.shutdown(wait=False)
    except Exception as e:
        logger.error(f"Error during scheduler shutdown: {e}")
