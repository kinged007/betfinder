import logging
import asyncio
import os
import uvicorn
from app.core.config import settings
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from app.routers import sports, bookmakers, presets, bets, views, events, public_views, ws, analytics
from app.routers.views import templates
from app.core.security import AppStartupFailedException, AppStartupLoadingException, NotAuthenticatedException
from app.services.notifications.telegram import TelegramNotifier
from app.services.scheduler import start_scheduler, stop_scheduler
from starlette.middleware.sessions import SessionMiddleware
from app.services.bookmakers.base import BookmakerFactory

# Configure Logging
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.mapping import MappingRepository
from app.services.the_odds_api import TheOddsAPIClient
from app.services.standardizer import DataStandardizer
from app.services.ingester import DataIngester
from app.services.analysis import OddsAnalysisService
from app.db.models import Sport, Bookmaker
from sqlalchemy import select
from app.api.deps import get_db
from app.db.session import engine, AsyncSessionLocal
from app.services.scheduler import job_preset_sync

async def check_and_sync_initial_data():

    async for db in get_db():
        # Check Sports
        s_res = await db.execute(select(Sport).limit(1))
        if not s_res.scalars().first():
            logger.info("Initializing database: Sports table empty. Syncing...")
            so_client = TheOddsAPIClient()
            mapping_repo = MappingRepository()
            standardizer = DataStandardizer(mapping_repo)
            ingester = DataIngester(so_client, standardizer)
            await ingester.sync_sports(db)
            
        # Check Bookmakers
        b_res = await db.execute(select(Bookmaker).limit(1))
        if not b_res.scalars().first():
            logger.info("Initializing database: Bookmakers empty. Fetching Bookmakers...")
            so_client = TheOddsAPIClient()
            mapping_repo = MappingRepository()
            standardizer = DataStandardizer(mapping_repo)
            ingester = DataIngester(so_client, standardizer)
            await ingester.sync_bookmakers(db)

        # Auto-detect specialized bookmakers and set model_type to 'api'
        all_bm_res = await db.execute(select(Bookmaker))
        all_bm = all_bm_res.scalars().all()
        registered_keys = BookmakerFactory.get_registered_keys()
        
        updated_count = 0
        credential_fields = ["api_key", "api_token", "username", "password", "session_token"]
        
        for bm in all_bm:
            # Case 1: Has specialized code implementation
            has_impl = bm.key in registered_keys
            # Case 2: Has credentials configured
            has_creds = any((bm.config or {}).get(f) for f in credential_fields)
            
            if (has_impl or has_creds) and bm.model_type == 'simple':
                bm.model_type = 'api'
                updated_count += 1
        
        if updated_count > 0:
            logger.info(f"Bootstrap: Auto-promoted {updated_count} bookmakers to 'api' (Implementation or Credentials detected)")
            await db.commit()

        # Always run analysis after sync check
        # Changed this to run from scheduler next_run_time
        break

@asynccontextmanager
async def lifespan(app: FastAPI):

    # Initialize startup state
    app.state.startup_status = "starting" # starting, ready, failed
    app.state.startup_error = None

    async def run_startup_tasks():
        try:
            # Check for missing configuration first
            if os.environ.get("MISSING_ENV_FILE") == "1":
                error_msg = (
                    "Settings file (.env) is missing.<br><br>"
                    "Please relaunch the app or re-install if that does not work."
                )
                app.state.startup_error = error_msg
                # We don't raise here immediately because we want to allow the app to actually boot 
                # so it can serve the error page. But we skip the sync logic.
            else:
                await check_and_sync_initial_data()
                start_scheduler(run_immediately=True)
                app.state.startup_status = "ready"
                logger.info("Background Startup: Complete. App is ready.")
            
        except Exception as e:
            logger.error(f"Background Startup failed: {str(e)}", exc_info=True)
            app.state.startup_status = "failed"
            app.state.startup_error = str(e)

    # Start startup tasks in background
    asyncio.create_task(run_startup_tasks())
    
    yield

    try:
        stop_scheduler()
    except:
        pass

    try:
        from app.services.connection_manager import manager
        await manager.stop()
    except Exception as e:
        logger.error(f"Error stopping connection manager: {e}")
    
    # Close database engine pool
    logger.info("Disposing database engine...")
    await engine.dispose()
    
    logger.info("Shutting down complete.")


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

# Mount static files
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

# Include Routers - API
app.include_router(sports.router, prefix=settings.API_V1_STR, tags=["Sports"])
app.include_router(bookmakers.router, prefix=settings.API_V1_STR, tags=["Bookmakers"])
app.include_router(presets.router, prefix=settings.API_V1_STR, tags=["Presets"])
app.include_router(bets.router, prefix=settings.API_V1_STR, tags=["Bets"])
from app.routers import leagues
app.include_router(leagues.router, prefix=settings.API_V1_STR, tags=["Leagues"])
app.include_router(ws.router, prefix="/ws", tags=["Websocket"])


if settings.is_dev:
    from app.routers import dev
    app.include_router(dev.router, prefix="/dev", tags=["Dev"])


# Include Routers - Frontend
app.include_router(analytics.router)
app.include_router(views.router, tags=["Views"])
app.include_router(public_views.router, tags=["Views"])
app.include_router(events.router, tags=["Events"])

@app.exception_handler(NotAuthenticatedException)
async def auth_exception_handler(request: Request, exc: NotAuthenticatedException):
    return RedirectResponse(url="/login")

@app.exception_handler(AppStartupFailedException)
async def startup_exception_handler(request: Request, exc: AppStartupFailedException):
    return templates.TemplateResponse(
        "app_startup_error.html",
        {"request": request, "error_message": exc.message},
        status_code=503
    )

@app.exception_handler(AppStartupLoadingException)
async def loading_exception_handler(request: Request, exc: AppStartupLoadingException):
    return templates.TemplateResponse(
        "app_startup_loading.html",
        {"request": request},
        status_code=503
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    notifier = TelegramNotifier()
    await notifier.send_message(f"🔥 Error in {request.url.path}: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )

@app.get("/health")
def health_check():
    return {"status": "ok", "project": settings.PROJECT_NAME}

def run_dev():
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)

def run_prod():
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT)
