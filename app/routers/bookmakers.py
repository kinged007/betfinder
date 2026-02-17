
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.core.security import get_api_key
from app.db.models import Bookmaker
from app.domain import schemas
from app.repositories.base import BaseRepository

router = APIRouter(dependencies=[Depends(get_api_key)])

@router.get("/bookmakers", response_model=List[schemas.BookmakerRead])
async def read_bookmakers(
    skip: int = 0, 
    limit: int = 100, 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Bookmaker)
        .order_by(Bookmaker.active.desc(), Bookmaker.title)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()

@router.post("/bookmakers", response_model=schemas.BookmakerRead)
async def create_bookmaker(
    bookmaker_in: schemas.BookmakerBase,
    db: AsyncSession = Depends(get_db)
):
    repo = BaseRepository(Bookmaker)
    # Check if exists
    existing = await repo.get(db, bookmaker_in.key) # Assuming key is PK? No id is PK.
    # We need check by key.
    result = await db.execute(select(Bookmaker).where(Bookmaker.key == bookmaker_in.key))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Bookmaker with this key already exists")
    
    return await repo.create(db, obj_in=bookmaker_in.model_dump())

@router.patch("/bookmakers/{bookmaker_id}", response_model=schemas.BookmakerRead)
async def update_bookmaker(
    bookmaker_id: int,
    bookmaker_in: schemas.BookmakerUpdateConfig,
    db: AsyncSession = Depends(get_db)
):
    repo = BaseRepository(Bookmaker)
    bookmaker = await repo.get(db, bookmaker_id)
    if not bookmaker:
        raise HTTPException(status_code=404, detail="Bookmaker not found")
    
    update_data = bookmaker_in.model_dump(exclude_unset=True)
    
    if "config" in update_data and update_data["config"] is not None:
        # We MUST create a brand new dict object for SQLAlchemy to detect the change in a JSON column
        current_config = dict(bookmaker.config or {})
        current_config.update(update_data["config"])
        update_data["config"] = current_config
        
    return await repo.update(db, db_obj=bookmaker, obj_in=update_data)

from app.services.bookmakers.base import BookmakerFactory

@router.post("/bookmakers/{bookmaker_id}/test-connection")
async def test_connection(
    bookmaker_id: int,
    db: AsyncSession = Depends(get_db)
):
    repo = BaseRepository(Bookmaker)
    bookmaker = await repo.get(db, bookmaker_id)
    if not bookmaker:
        raise HTTPException(status_code=404, detail="Bookmaker not found")
    
    # Instantiate the bookmaker class
    config = bookmaker.config or {}
    try:
        bm_instance = BookmakerFactory.get_bookmaker(bookmaker.key, config, db)
        success = await bm_instance.test_connection()
        
        if success:
            # Sync balance and other data
            try:
                balance_data = await bm_instance.get_account_balance()
                bookmaker.balance = balance_data.get("balance", bookmaker.balance)
                
                # Update config with other metadata
                # Use dict() to ensure we have a fresh object for change detection
                current_config = dict(bookmaker.config or {})
                # Map specific fields into config if they exist in balance_data
                for key, value in balance_data.items():
                    if key in ["commission", "currency", "account_id"]:
                        current_config[key] = value
                
                bookmaker.config = current_config
                from datetime import datetime, timezone
                bookmaker.last_update = datetime.now(timezone.utc)
                
                await db.commit()
                await db.refresh(bookmaker)
                
                return {
                    "status": "success", 
                    "connected": True, 
                    "balance": bookmaker.balance,
                    "message": "Connected and synchronized account data"
                }
            except Exception as sync_err:
                # If sync fails but connection was theoretically okay (though unlikely if balance fails)
                return {"status": "success", "connected": True, "message": f"Connected, but sync failed: {str(sync_err)}"}

        return {"status": "success", "connected": success}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/bookmakers/key/{key}/balance")
async def get_bookmaker_balance_by_key(
    key: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Fetch the latest balance and currency for a bookmaker by its key.
    Used by the bet modal to show available funds.
    """
    result = await db.execute(select(Bookmaker).where(Bookmaker.key == key))
    bookmaker = result.scalar_one_or_none()
    
    if not bookmaker:
        raise HTTPException(status_code=404, detail=f"Bookmaker with key '{key}' not found")
    
    # Try to get live balance if it's an API bookmaker and active
    balance = bookmaker.balance
    currency = (bookmaker.config or {}).get("currency", "EUR")
    
    # We return the stored balance for speed, but we could trigger a refresh here.
    # Given the user wants it in the modal, a stored balance is usually sufficient 
    # as balance is synced periodically or on bet placement.
    return {
        "balance": balance,
        "currency": currency,
        "key": key,
        "title": bookmaker.title
    }
