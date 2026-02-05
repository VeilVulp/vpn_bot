import asyncio
import logging
from typing import List
from telegram import Bot
from telegram.error import TelegramError, Forbidden, RetryAfter
from sqlalchemy import select
from database import AsyncSessionLocal
from models import User

logger = logging.getLogger("vpn_bot.notifications")

class NotificationManager:
    """Handles mass and targeted notifications."""
    
    def __init__(self, bot: Bot):
        self.bot = bot

    async def send_to_user(self, telegram_id: int, message: str, parse_mode: str = 'Markdown') -> bool:
        """Sends a message to a specific user."""
        try:
            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                parse_mode=parse_mode
            )
            return True
        except Forbidden:
            logger.warning(f"User {telegram_id} blocked the bot.")
            return False
        except TelegramError as e:
            logger.error(f"Failed to send message to {telegram_id}: {e}")
            return False

    async def broadcast_to_all(self, message: str, parse_mode: str = 'Markdown') -> dict:
        """Sends a message to all active users in the database."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.is_active == True))
            users = result.scalars().all()
            
        total = len(users)
        success_count = 0
        blocked_count = 0
        failed_count = 0
        
        logger.info(f"Starting broadcast to {total} users...")
        
        for user in users:
            try:
                await self.bot.send_message(
                    chat_id=user.telegram_id,
                    text=message,
                    parse_mode=parse_mode
                )
                success_count += 1
                # Small delay to respect rate limits (approx 30 msgs per second)
                await asyncio.sleep(0.05) 
            except Forbidden:
                blocked_count += 1
                logger.warning(f"User {user.telegram_id} has blocked the bot.")
            except RetryAfter as e:
                logger.warning(f"Flood limit reached. Waiting {e.retry_after} seconds...")
                await asyncio.sleep(e.retry_after)
                # Retry once
                try:
                    await self.bot.send_message(chat_id=user.telegram_id, text=message, parse_mode=parse_mode)
                    success_count += 1
                except: failed_count += 1
            except TelegramError as e:
                failed_count += 1
                logger.error(f"Error broadcasting to {user.telegram_id}: {e}")
                
        logger.info(f"Broadcast complete: {success_count} success, {blocked_count} blocked, {failed_count} failed.")
        
        return {
            'total': total,
            'success': success_count,
            'blocked': blocked_count,
            'failed': failed_count
        }
