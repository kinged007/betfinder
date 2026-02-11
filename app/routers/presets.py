
from typing import List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.core.security import get_api_key
from app.db.models import Preset
from app.domain import schemas
from app.repositories.base import BaseRepository

router = APIRouter(dependencies=[Depends(get_api_key)])

from sqlalchemy.orm import selectinload
from app.db.models import Preset, PresetHiddenItem
from app.services.scheduler import job_preset_sync

@router.get("/presets", response_model=List[schemas.PresetRead])
async def read_presets(
    skip: int = 0, 
    limit: int = 100, 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Preset).options(selectinload(Preset.hidden_items)).offset(skip).limit(limit))
    return result.scalars().all()

@router.post("/presets", response_model=schemas.PresetRead)
async def create_preset(
    preset_in: schemas.PresetCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    # BaseRepository handles simple mapping.
    # We might want to validate that 'sports' keys exist in DB?
    # For now trust the frontend/schema.
    # Note: Explicitly loading hidden_items for return schema consistency, though empty for new preset
    repo = BaseRepository(Preset)
    new_preset = await repo.create(db, obj_in=preset_in.model_dump())
    # Re-fetch with relationship
    result = await db.execute(select(Preset).options(selectinload(Preset.hidden_items)).where(Preset.id == new_preset.id))
    preset = result.scalar_one()
    background_tasks.add_task(job_preset_sync)
    return preset

@router.delete("/presets/{preset_id}")
async def delete_preset(
    preset_id: int,
    db: AsyncSession = Depends(get_db)
):
    repo = BaseRepository(Preset)
    obj = await repo.delete(db, id=preset_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"status": "success"}

@router.patch("/presets/{preset_id}", response_model=schemas.PresetRead)
async def update_preset(
    preset_id: int,
    preset_in: schemas.PresetCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    repo = BaseRepository(Preset)
    obj = await repo.get(db, id=preset_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Preset not found")
    
    # BaseRepository update handles model_dump and attributes update
    update_data = preset_in.model_dump(exclude_unset=True)
    
    # Only reset sync time if fields affecting the sync are modified AND changed
    sync_reset_fields = ["sports", "leagues", "show_popular_leagues"] # "markets",
    has_changes = False
    for field in sync_reset_fields:
        if field in update_data:
            # Compare current value with new value
            current_val = getattr(obj, field)
            new_val = update_data[field]
            if current_val != new_val:
                has_changes = True
                break
                
    if has_changes:
        update_data["last_sync_at"] = None
        
    updated_obj = await repo.update(db, db_obj=obj, obj_in=update_data)
    
    result = await db.execute(select(Preset).options(selectinload(Preset.hidden_items)).where(Preset.id == updated_obj.id))
    preset = result.scalar_one()
    background_tasks.add_task(job_preset_sync)
    return preset

@router.post("/presets/{preset_id}/hidden-items", response_model=schemas.PresetHiddenItemRead)
async def create_hidden_item(
    preset_id: int,
    item_in: schemas.PresetHiddenItemCreate,
    db: AsyncSession = Depends(get_db)
):
    # Verify preset exists
    preset = await db.get(Preset, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
        
    repo = BaseRepository(PresetHiddenItem)
    # Ensure preset_id in payload matches path
    data = item_in.model_dump()
    data['preset_id'] = preset_id
    
    return await repo.create(db, obj_in=data)

@router.delete("/presets/{preset_id}/hidden-items/{hidden_id}")
async def delete_hidden_item(
    preset_id: int,
    hidden_id: int,
    db: AsyncSession = Depends(get_db)
):
    repo = BaseRepository(PresetHiddenItem)
    obj = await repo.delete(db, id=hidden_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Hidden item not found")
    return {"status": "success"}
