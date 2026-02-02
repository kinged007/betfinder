
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db, get_ingester
from app.core.security import get_api_key
from app.db.models import Sport, League, Event
from app.domain import schemas
from app.services.ingester import DataIngester

router = APIRouter(dependencies=[Depends(get_api_key)])

@router.get("/sports", response_model=List[schemas.SportRead])
async def read_sports(
    skip: int = 0, 
    limit: int = 100, 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Sport).offset(skip).limit(limit))
    return result.scalars().all()

@router.get("/events", response_model=List[schemas.EventRead])
async def read_events(
    skip: int = 0, 
    limit: int = 100, 
    sport_key: str = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(Event)
    if sport_key:
        query = query.where(Event.sport_key == sport_key)
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()
