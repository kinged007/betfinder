
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.core.security import get_api_key
from app.db.models import Bet, Bookmaker, Event, Market, Odds
from app.domain import schemas
from app.core.enums import BetResult, BetStatus
from app.repositories.base import BaseRepository
from app.services.bookmakers.base import BookmakerFactory, AbstractBookmaker

# Import implementations to register them
import app.services.bookmakers.implementations

router = APIRouter(dependencies=[Depends(get_api_key)])

@router.get("/bets", response_model=List[schemas.BetRead])
async def read_bets(
    skip: int = 0, 
    limit: int = 100, 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Bet).offset(skip).limit(limit))
    return result.scalars().all()

@router.post("/bets", response_model=schemas.BetRead)
async def place_bet(
    bet_in: schemas.BetCreate,
    db: AsyncSession = Depends(get_db)
):
    # 1. Retrieve Bookmaker config
    bm_repo = BaseRepository(Bookmaker)
    bookmaker_model = await bm_repo.get(db, bet_in.bookmaker_id)
    if not bookmaker_model:
        raise HTTPException(status_code=404, detail="Bookmaker not found")

    # 2. Instantiate Bookmaker Service
    # Factory uses 'key' e.g. 'smarkets'
    bm_service: AbstractBookmaker = BookmakerFactory.get_bookmaker(
        bookmaker_model.key, 
        config=bookmaker_model.config or {},
        db=db
    )
    
    # Fetch Snapshot Data
    # Fetch Event
    event_result = await db.execute(select(Event).where(Event.id == bet_in.event_id))
    event_obj = event_result.scalar_one_or_none()
    
    event_snapshot = None
    if event_obj:
        event_snapshot = {
            "id": event_obj.id,
            "sport_key": event_obj.sport_key,
            "league_key": event_obj.league_key,
            "commence_time": event_obj.commence_time.isoformat() if event_obj.commence_time else None,
            "home_team": event_obj.home_team,
            "away_team": event_obj.away_team
        }

    # Fetch Odds/Market
    stmt = (
        select(Odds, Market)
        .join(Market, Odds.market_id == Market.id)
        .where(
            Market.event_id == bet_in.event_id,
            Market.key == bet_in.market_key,
            Odds.bookmaker_id == bet_in.bookmaker_id,
            Odds.normalized_selection == bet_in.selection
        )
    )
    odds_result = await db.execute(stmt)
    row = odds_result.first()
    
    market_snapshot = None
    odd_snapshot = None
    
    if row:
        odds_obj, market_obj = row
        
        market_snapshot = {
            "id": market_obj.id,
            "key": market_obj.key,
            "event_id": market_obj.event_id
        }
        
        edge = 0.0
        if odds_obj.true_odds:
             edge = (odds_obj.price / odds_obj.true_odds) - 1.0
             
        odd_snapshot = {
            "id": odds_obj.id,
            "selection": odds_obj.selection,
            "normalized_selection": odds_obj.normalized_selection,
            "price": odds_obj.price,
            "point": odds_obj.point,
            "url": odds_obj.url,
            "event_sid": odds_obj.event_sid,
            "market_sid": odds_obj.market_sid,
            "sid": odds_obj.sid,
            "implied_probability": odds_obj.implied_probability,
            "true_odds": odds_obj.true_odds,
            "edge": edge
        }

    # 3. Create Bet Record (Pending)
    bet_repo = BaseRepository(Bet)
    
    bet_data = bet_in.model_dump()
    bet_data["status"] = BetStatus.PENDING.value
    # Add snapshots
    bet_data["event_data"] = event_snapshot
    bet_data["market_data"] = market_snapshot
    bet_data["odd_data"] = odd_snapshot
    
    bet_obj = await bet_repo.create(db, obj_in=bet_data)

    # 4. Place Bet via API
    try:
        response = await bm_service.place_bet(bet_obj)
        
        # 5. Update Bet Record
        update_data = {}
        if response.get("status") == "success" or response.get("status") == "pending":
            update_data["status"] = BetStatus.OPEN.value # Or pending if async confirmation
            update_data["external_id"] = response.get("external_id")
        else:
            update_data["status"] = "error"
            # Log error?
            
        await bet_repo.update(db, db_obj=bet_obj, obj_in=update_data)
        
    except Exception as e:
        await bet_repo.update(db, db_obj=bet_obj, obj_in={"status": "failed"})
        raise HTTPException(status_code=500, detail=str(e))
        
    return bet_obj

@router.patch("/bets/bulk", response_model=List[schemas.BetRead])
async def bulk_update_bets(
    bulk_update: schemas.BetBulkUpdate,
    db: AsyncSession = Depends(get_db)
):
    repo = BaseRepository(Bet)
    updated_bets = []
    
    for bet_id in bulk_update.bet_ids:
        # Load bookmaker for balance update
        stmt = select(Bet).options(selectinload(Bet.bookmaker)).where(Bet.id == bet_id)
        result = await db.execute(stmt)
        bet = result.scalar_one_or_none()
        
        if bet:
            old_status = bet.status
            update_data = bulk_update.model_dump(exclude={"bet_ids"}, exclude_unset=True)
            new_status = update_data.get("status")

            # For bulk, we might want to default payout if not provided and status is lost/void/won
            if "payout" not in update_data and new_status in [BetResult.LOST.value, BetResult.VOID.value, BetResult.WON.value]:
                if new_status == BetResult.LOST.value:
                    update_data["payout"] = 0.0
                elif new_status == BetResult.VOID.value:
                    update_data["payout"] = bet.stake
                elif new_status == BetResult.WON.value:
                    update_data["payout"] = bet.stake * bet.price
            
            new_payout = update_data.get("payout", bet.payout or 0.0)

            # Update Balance logic
            settled_statuses = [BetResult.WON.value, BetResult.LOST.value, BetResult.VOID.value]
            # 1. Moving from Pending/Open to Settled
            if old_status not in settled_statuses and new_status in settled_statuses:
                if new_status == BetResult.WON.value:
                    bet.bookmaker.balance += new_payout
                elif new_status == BetResult.VOID.value:
                    bet.bookmaker.balance += bet.stake
                # Lost: nothing to do, stake already deducted
            
            # 2. Reversing from Settled to Open
            elif old_status in settled_statuses and new_status == BetStatus.OPEN.value:
                if old_status == BetResult.WON.value:
                    bet.bookmaker.balance -= (bet.payout or 0.0)
                elif old_status == BetResult.VOID.value:
                    bet.bookmaker.balance -= bet.stake
                elif old_status == "lost":
                    # Reverting lost means we should give stake back? 
                    # Actually if they put it back to open, they probably want to "un-place" it or re-settle.
                    # But if we deducted stake at placement, and it stays placed but just 'open', 
                    # then balance shouldn't change for lost->open.
                    pass

            # Update settled_at
            if new_status in settled_statuses and not update_data.get("settled_at"):
                from datetime import datetime, timezone
                update_data["settled_at"] = datetime.now(timezone.utc)
            elif new_status == "open":
                update_data["settled_at"] = None

            updated_bet = await repo.update(db, db_obj=bet, obj_in=update_data)
            updated_bets.append(updated_bet)
    
    return updated_bets

@router.patch("/bets/{bet_id}", response_model=schemas.BetRead)
async def update_bet(
    bet_id: int,
    bet_update: schemas.BetUpdate,
    db: AsyncSession = Depends(get_db)
):
    repo = BaseRepository(Bet)
    # Load with bookmaker for potential balance adjustment
    stmt = select(Bet).options(selectinload(Bet.bookmaker)).where(Bet.id == bet_id)
    result = await db.execute(stmt)
    bet = result.scalar_one_or_none()
    
    if not bet:
        raise HTTPException(status_code=404, detail="Bet not found")
    
    old_status = bet.status
    update_data = bet_update.model_dump(exclude_unset=True)
    new_status = update_data.get("status")

    if new_status and new_status != old_status:
        settled_statuses = [BetResult.WON.value, BetResult.LOST.value, BetResult.VOID.value]
        payout = update_data.get("payout", bet.payout or 0.0)
        
        # Moving from unsettled to settled
        if old_status not in settled_statuses and new_status in settled_statuses:
            if new_status == BetResult.WON.value:
                bet.bookmaker.balance += payout
            elif new_status == BetResult.VOID.value:
                bet.bookmaker.balance += bet.stake
        
        # Reversing from settled to open
        elif old_status in settled_statuses and new_status == BetStatus.OPEN.value:
            if old_status == BetResult.WON.value:
                bet.bookmaker.balance -= (bet.payout or 0.0)
            elif old_status == BetResult.VOID.value:
                bet.bookmaker.balance -= bet.stake

    updated_bet = await repo.update(db, db_obj=bet, obj_in=update_data)
    return updated_bet

@router.delete("/bets/{bet_id}")
async def delete_bet(
    bet_id: int,
    db: AsyncSession = Depends(get_db)
):
    repo = BaseRepository(Bet)
    bet = await repo.get(db, bet_id)
    if not bet:
        raise HTTPException(status_code=404, detail="Bet not found")
    
    await repo.delete(db, id=bet_id)
    return {"status": "success", "message": "Bet deleted"}
