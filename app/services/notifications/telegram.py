
import httpx
import hashlib
from datetime import datetime, timedelta, timezone
from app.core.config import settings
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class TelegramNotifier:
    BASE_URL = "https://api.telegram.org/bot"
    _cache = {} # Simple in-memory cache for deduplication

    def __init__(self, token: Optional[str] = settings.TELEGRAM_BOT_TOKEN, chat_id: Optional[str] = settings.TELEGRAM_CHAT_ID):
        self.token = token
        self.chat_id = chat_id

    async def send_message(self, message: str, dedupe_window_seconds: int = 300):
        if not self.token or not self.chat_id:
            logger.info("Telegram token or chat_id not configured")
            return

        # Deduplication
        msg_hash = hashlib.md5(message.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        
        if msg_hash in self._cache:
            last_sent = self._cache[msg_hash]
            if now - last_sent < timedelta(seconds=dedupe_window_seconds):
                logger.info("Skipping duplicate notification")
                return
        
        self._cache[msg_hash] = now
        
        # TODO Cleanup cache if too big? For now simple dict.

        url = f"{self.BASE_URL}{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
            except Exception as e:
                logger.error(f"Failed to send telegram message: {e}")
