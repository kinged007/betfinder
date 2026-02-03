
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    PROJECT_NAME: str = "Sports Bet Finder"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "production"
    
    @property
    def is_dev(self) -> bool:
        return self.ENVIRONMENT.lower() == "development"
    
    # Security
    API_ACCESS_KEY: Optional[str] = None
    SECRET_KEY: str = "super-secret-key-change-it"
    
    # Database
    DATABASE_URL: str
    
    # External APIs
    THE_ODDS_API_KEY: str = "changeme"
    THE_ODDS_API_REGIONS: str = "eu,us,uk"
    
    # Notifications
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    
    # Logging
    LOG_LEVEL: str = "WARNING"

    # Scheduler
    PRESET_SYNC_INTERVAL_HOURS: int = 6
    
    # Server
    PORT: int = 8123

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

settings = Settings()
