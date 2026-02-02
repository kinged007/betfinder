from fastapi import Security, HTTPException, status, Request
from fastapi.security import APIKeyHeader
from app.core.config import settings
from starlette.requests import HTTPConnection

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

class NotAuthenticatedException(Exception):
    pass

class AppStartupFailedException(Exception):
    def __init__(self, message: str):
        self.message = message

class AppStartupLoadingException(Exception):
    pass



async def get_api_key(request: Request, api_key_header: str = Security(api_key_header)):
    # Check Header (for programmatic API use)
    if not settings.API_ACCESS_KEY:
        return True
    
    if api_key_header == settings.API_ACCESS_KEY:
        return api_key_header
    
    # Check Session (for frontend use)
    if request.session.get("authenticated"):
        return True
        
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Could not validate credentials",
    )


async def check_session(request: HTTPConnection):
    # Check for startup status first
    if hasattr(request.app.state, "startup_status"):
        if request.app.state.startup_status == "starting":
            raise AppStartupLoadingException()
        elif request.app.state.startup_status == "failed":
            error_msg = getattr(request.app.state, "startup_error", "Unknown error")
            raise AppStartupFailedException(error_msg)

    if not settings.API_ACCESS_KEY:
        return True

    if not request.session.get("authenticated"):
        raise NotAuthenticatedException()