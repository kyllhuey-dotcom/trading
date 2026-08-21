import httpx
import logging
import os
import asyncio
from typing import Optional, Dict, Any

logger = logging.getLogger("NotificationEngine")

class NotificationEngine:
    """
    Handles real-time alerts via Telegram and Discord (Rule: Lot 10).
    """
    def __init__(self, telegram_token: Optional[str] = None, telegram_chat_id: Optional[str] = None):
        self.telegram_token = telegram_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK_URL")
        self.enabled = bool(self.telegram_token and self.telegram_chat_id)

    async def send_telegram(self, message: str):
        if not self.enabled:
            return
        
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        data = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(url, json=data, timeout=10.0)
                if res.status_code != 200:
                    logger.error(f"Telegram API Error: {res.text}")
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

    async def send_discord(self, message: str):
        if not self.discord_webhook:
            return
            
        try:
            async with httpx.AsyncClient() as client:
                await client.post(self.discord_webhook, json={"content": message}, timeout=10.0)
        except Exception as e:
            logger.error(f"Failed to send Discord notification: {e}")

    async def notify(self, event_type: str, data: Dict[str, Any]):
        """
        Unified notification dispatcher.
        """
        message = ""
        emoji = "ℹ️"
        
        if event_type == "SIGNAL":
            emoji = "🚨"
            message = (
                f"<b>{emoji} NEW SIGNAL</b>\n"
                f"Market: {data.get('symbol')}\n"
                f"Direction: {data.get('direction')}\n"
                f"Price: {data.get('price')}\n"
                f"Score: {data.get('score')}%\n"
                f"Strategy: {data.get('strategy')}"
            )
        elif event_type == "ORDER_OPEN":
            emoji = "🚀"
            message = (
                f"<b>{emoji} POSITION OPENED</b>\n"
                f"Market: {data.get('symbol')}\n"
                f"Entry: {data.get('entry_price')}\n"
                f"Qty: {data.get('quantity')}"
            )
        elif event_type == "ORDER_CLOSE":
            pnl = data.get('pnl', 0)
            emoji = "💰" if pnl >= 0 else "📉"
            message = (
                f"<b>{emoji} POSITION CLOSED</b>\n"
                f"Market: {data.get('symbol')}\n"
                f"PnL: {pnl:.2f}€\n"
                f"Reason: {data.get('metadata', {}).get('close_reason', 'N/A')}"
            )
        elif event_type == "EMERGENCY_STOP":
            emoji = "🛑"
            message = f"<b>{emoji} EMERGENCY STOP ACTIVATED</b>\nReason: {data.get('reason')}"
        elif event_type == "ERROR":
            emoji = "⚠️"
            message = f"<b>{emoji} SYSTEM ALERT</b>\n{data.get('message')}"

        if message:
            # Run both in parallel
            await asyncio.gather(
                self.send_telegram(message),
                self.send_discord(message)
            )

    def update_config(self, token: str, chat_id: str):
        self.telegram_token = token
        self.telegram_chat_id = chat_id
        self.enabled = bool(token and chat_id)
