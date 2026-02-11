
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.core.security import get_api_key
from app.db.models import League
from app.domain import schemas

router = APIRouter(prefix="/leagues", tags=["Leagues"], dependencies=[Depends(get_api_key)])

@router.post("/{key}/toggle-popular")
async def toggle_popular_league(
    key: str,
    db: AsyncSession = Depends(get_db)
):
    league = await db.get(League, key)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    
    league.popular = not league.popular
    db.add(league)
    await db.commit()
    await db.refresh(league)
    
    return {"key": league.key, "popular": league.popular}
