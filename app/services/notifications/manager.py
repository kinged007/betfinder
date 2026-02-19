import logging
import json
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, cast
from sqlalchemy.dialects.postgresql import JSONB
from app.db.models import Notification, Preset, Bet
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

    async def send_bet_notification(self, preset: Preset, bet: Bet):
        """
        Sends a notification when a bet is placed (manually or auto).
        Checks if "notification_on_bet" is enabled in preset config.
        """
        # 1. Check Config
        config = preset.other_config or {}
        notif_enabled = config.get("notification_on_bet", "true")
        
        logger.info(f"Checking notification for preset '{preset.name}' (ID: {preset.id}). Config: {config}. Enabled: {notif_enabled}")

        # Default is True (matches schema)
        if notif_enabled != "true":
            logger.info("Notification disabled in config.")
            return

        # 2. Construct Message
        # Similar to trade notification but indicates "Bet Placed"
        
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
        
        # Extract sport key from event_data snapshot or relationship
        sport_key = "unknown"
        if bet.event_data and "sport_key" in bet.event_data:
            sport_key = bet.event_data["sport_key"]
        
        sport_icon = sport_emojis.get(sport_key, "🏆")
        
        # Details
        home_team = bet.event_data.get("home_team") if bet.event_data else "Unknown"
        away_team = bet.event_data.get("away_team") if bet.event_data else "Unknown"
        
        market_key = bet.market_key.upper()
        selection = bet.selection
        price = bet.price
        bookmaker_name = bet.bookmaker.title if bet.bookmaker else "Unknown Bookmaker"
        
        # Bookmaker Link (similar to new trade notification)
        bookmaker_display = bookmaker_name
        if bet.odd_data and bet.odd_data.get("url"):
            bookmaker_display = f"[{bookmaker_name}]({bet.odd_data['url']})"
        
        # Edge/Prob if available
        # They should be in odd_data
        edge_str = "-"
        prob_str = "-"
        if bet.odd_data:
            if bet.odd_data.get("edge"):
                edge_str = f"{bet.odd_data['edge']*100:.1f}%"
            if bet.odd_data.get("implied_probability"):
                prob_str = f"{bet.odd_data['implied_probability']:.1%}"

        message = (
            f"✅ *{preset.name} - Bet Placed*\n"
            f"{sport_icon} `{home_team}` vs `{away_team}`\n"
            f"{market_key} - `{selection}` @{price} ({bookmaker_display})\n"
            f"Stake: {bet.stake}\n"
            f"Prob: {prob_str}\n"
            f"Edge: {edge_str}"
        )

        # 3. Send Notification
        logger.info(f"Sending Telegram notification for bet {bet.id}...")
        try:
            await self.telegram.send_message(message)
            logger.info("Telegram notification sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}", exc_info=True)
        
        # 4. Record Notification
        # Unique Key: (bet_id)
        dedupe_key = {
            "type": "bet_placed",
            "bet_id": bet.id
        }
        
        new_notification = Notification(
            type="bet_alert",
            message=message,
            data=dedupe_key,
            sent=True,
            processed_at=datetime.now(timezone.utc)
        )
        self.db.add(new_notification)
        await self.db.commit()
