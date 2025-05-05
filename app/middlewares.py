from aiogram import BaseMiddleware
from aiogram.types import Message, Update
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Callable, Any, Awaitable
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory: Callable[[], Awaitable[AsyncSession]], trading_bot):
        self.session_factory = session_factory
        self.trading_bot = trading_bot

    async def __call__(
        self,
        handler: Callable[[Update, dict], Awaitable[Any]],
        event: Update,
        data: dict
    ) -> Any:
        async with self.session_factory() as session:
            try:
                data["db_session"] = session
                data["trading_bot"] = self.trading_bot
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception as e:
                logger.error(f"Ошибка в DbSessionMiddleware: {e}")
                await session.rollback()
                raise
            finally:
                if session:
                    await session.close()
                    logger.info("Сессия базы данных закрыта")