import logging
import json
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, cast
from sqlalchemy.dialects.postgresql import JSONB
from app.db.models import Notification, Preset
from app.services.notifications.telegram import TelegramNotifier
from app.services.analytics.trade_finder import TradeOpportunity

logger = logging.getLogger(__name__)

class NotificationManager:
    """
    Centralized manager for sending notifications to various channels (Telegram, Browser/WS).
    Handles deduplication to prevent spamming the same trade.
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.telegram = TelegramNotifier()

    async def send_trade_notification(self, preset: Preset, trade: TradeOpportunity):
        """
        Sends a notification for a new trade opportunity if enabled in preset config.
        Checks for duplicates before sending.
        """
        # 1. Check Config
        if not preset.other_config:
            return

        # Handle string "true"/"false" from the select config
        notif_enabled = preset.other_config.get("notification_new_bet")
        if notif_enabled != "true":
            # Defaults to True? No, defaults to "true" string in config schema, 
            # but if key missing, we might assume False or True. 
            # Plan said "defaults to True". 
            # But if it's explicitly "false", return.
            # If missing, we can check logic. Schema default is "true".
            if notif_enabled == "false":
                return
            if notif_enabled is None:
                # Fallback to schema default or safe default
                # Let's assume enabled by default if not strictly disabled?
                pass 

        # 2. Deduplication Check
        # Unique Key: (preset_id, odd_id)
        # We store this in Notification.data
        dedupe_key = {
            "preset_id": preset.id,
            "odd_id": trade.odd.id
        }
        
        # Dialect check for JSON storage
        # SQLite stores JSON as Text (mostly), Postgres has native JSON/JSONB
        # 'Notification.data' is defined as JSON type in models.
        # Check dialect to decide if we need casting for containment check.
        # Use get_bind() to retrieve the engine/connection and check the dialect name.
        dialect_name = "sqlite" # Default
        try:
             # Try to get dialect name safely
             if self.db.bind:
                 dialect_name = self.db.bind.dialect.name
        except Exception:
             pass

        if dialect_name == "postgresql":
            stmt = select(Notification).where(
                Notification.type == "trade_alert",
                cast(Notification.data, JSONB).contains(dedupe_key)
            )
        else:
            stmt = select(Notification).where(
                Notification.type == "trade_alert",
                Notification.data.contains(dedupe_key)
            )
        result = await self.db.execute(stmt)
        existing = result.first()
        
        if existing:
            logger.debug(f"Skipping duplicate trade notification for Preset {preset.id}, Odd {trade.odd.id}")
            return

        # 3. Construct Message
        
        prob_str = f"{trade.odd.implied_probability:.1%}" if trade.odd.implied_probability else "-"
        edge_str = f"{trade.edge*100:.1f}%" if trade.edge is not None else "-"
        
        # Format commence time nicely
        start_time = trade.event.commence_time.strftime("%d %b %H:%M") 
        
        # Emoji Map
        sport_emojis = {
            "soccer": "⚽",
            "basketball": "🏀",
            "tennis": "🎾",
            "americanfootball": "🏈",
            "baseball": "⚾",
            "icehockey": "🏒",
            "golf": "⛳",
            "boxing": "🥊",
            "mma": "🥋",
            "rugby": "🏉",
            "cricket": "🏏"
        }
        # Fallback to generic trophy if not found
        # trade.sport.key is usually lowercased compacted e.g. 'americanfootball'
        sport_icon = sport_emojis.get(trade.sport.key, "🏆")
        
        # Bookmaker Link
        bookmaker_display = trade.bookmaker.title
        if trade.odd.url:
            bookmaker_display = f"[{trade.bookmaker.title}]({trade.odd.url})"

        # League Line
        league_line = f"{trade.league.title}" if trade.league else "Unknown League"

        message = (
            f"*{preset.name} - New Trade*\n"
            f"{sport_icon} {league_line}\n"
            f"`{trade.event.home_team}` vs `{trade.event.away_team}`\n"
            f"⏰ {start_time} GMT\n"
            f"{trade.market.key.upper()} - `{trade.odd.selection}` @{trade.odd.price} ({bookmaker_display})\n"
            f"Prob: {prob_str}\n"
            f"Edge: {edge_str}"
        )

        # 4. Send Notifications
        
        # Telegram
        await self.telegram.send_message(message)
        
        # 5. Record Notification
        new_notification = Notification(
            type="trade_alert",
            message=message,
            data=dedupe_key,
            sent=True, # Assessing it as sent if we fired the tasks. 
            processed_at=datetime.now(timezone.utc)
        )
        self.db.add(new_notification)
        await self.db.commit()

    async def send_error_notification(self, title: str, message: str):
        """
        Sends an error/alert notification to the user (Telegram).
        """
        full_message = f"🚨 *{title}* 🚨\n\n{message}"
        
        # Telegram
        await self.telegram.send_message(full_message)
        
        # Database Record
        new_notification = Notification(
            type="error",
            message=full_message,
            data={},
            sent=True,
            processed_at=datetime.now(timezone.utc)
        )
        self.db.add(new_notification)
        await self.db.commit()
