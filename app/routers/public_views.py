from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, case, func
from app.api.deps import get_db
from app.db.models import Bet, Bookmaker, Event, Market, Preset, Sport, League
from app.domain import schemas
from app.core.config import settings
from app.core.enums import BetResult, BetStatus
import logging
from pydantic import BaseModel
from datetime import datetime, timezone
from app.core.preset_config import PRESET_OTHER_CONFIG_SCHEMA
from app.core.security import check_session
from typing import Optional

templates = Jinja2Templates(directory="app/web/templates")

router = APIRouter()


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "title": "Login", "is_dev": settings.is_dev})

@router.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if password == settings.API_ACCESS_KEY:
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "title": "Login", "error": "Invalid Key"})

@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
