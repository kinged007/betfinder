import asyncio
import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, text
from app.db.base import Base
from app.db.models import Sport, League, Event, Bookmaker, Market, Odds
from app.services.ingester import DataIngester

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MockApiClient:
    pass

async def verify_update():
    # Setup in-memory DB
    engine = create_async_engine('sqlite+aiosqlite:///', echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    # Mock Data
    sport_key = "soccer"
    league_key = "soccer_epl"
    event_id = "event_1"
    bk_key = "bookie_1"
    market_key = "h2h"
    selection_name = "Home Team"
    
    # Initial Odds Data
    initial_price = 1.5
    updated_price = 1.6
    
    async with async_session() as db:
        # Seed Prerequisites
        db.add(Sport(key=sport_key, title="Soccer", group="Soccer"))
        db.add(League(key=league_key, title="EPL", group="Soccer", sport_key=sport_key))
        db.add(Event(
            id=event_id, 
            sport_key=sport_key, 
            league_key=league_key, 
            commence_time=datetime.utcnow(), 
            home_team="Home", 
            away_team="Away"
        ))
        await db.commit()
        
        # Instantiate Ingester
        ingester = DataIngester(api_client=MockApiClient())
        
        # 1. Simulate First Ingestion (Create)
        logger.info("--- 1. Processing Initial Odds (Create) ---")
        odds_data_1 = [{
            "id": event_id,
            "sport_key": league_key,
            "commence_time": "2024-01-01T12:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "bookmakers": [{
                "key": bk_key,
                "title": "Bookie 1",
                "last_update": "2024-01-01T10:00:00Z",
                "markets": [{
                    "key": market_key,
                    "outcomes": [{
                        "name": selection_name,
                        "price": initial_price
                    }]
                }]
            }]
        }]
        
        await ingester._process_odds_data(db, odds_data_1)
        
        # Verify Creation
        result = await db.execute(select(Odds))
        odd = result.scalar_one()
        odd_id_initial = odd.id
        logger.info(f"Created Odd ID: {odd.id}, Price: {odd.price}")
        assert odd.price == initial_price
        
        # 2. Simulate Second Ingestion (Update)
        logger.info("--- 2. Processing Updated Odds (Update) ---")
        odds_data_2 = [{
            "id": event_id,
            "sport_key": league_key, # Same event
            "commence_time": "2024-01-01T12:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "bookmakers": [{
                "key": bk_key, # Same bookmaker
                "title": "Bookie 1",
                "last_update": "2024-01-01T10:05:00Z",
                "markets": [{
                    "key": market_key, # Same market
                    "outcomes": [{
                        "name": selection_name, # Same selection
                        "price": updated_price # NEW PRICE
                    }]
                }]
            }]
        }]
        
        await ingester._process_odds_data(db, odds_data_2)
        
        # Verify Update
        result = await db.execute(select(Odds))
        odd_updated = result.unique().scalar_one() # Should still be one record
        
        logger.info(f"Updated Odd ID: {odd_updated.id}, Price: {odd_updated.price}")
        
        if odd_updated.id == odd_id_initial:
            logger.info("SUCCESS: Odds ID persisted (Update logic worked).")
        else:
            logger.error(f"FAILURE: Odds ID changed! Initial: {odd_id_initial}, New: {odd_updated.id} (Delete/Create logic happened).")
            
        assert odd_updated.id == odd_id_initial, "Odds ID should not change"
        assert odd_updated.price == updated_price, "Price should be updated"

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(verify_update())
