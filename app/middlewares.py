from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

class DbSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        # Извлекаем db из контекста (передаётся через dp.feed_update)
        db: AsyncSession = data.get("db")
        if db:
            try:
                result = await handler(event, data)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise
            finally:
                await db.close()
        return await handler(event, data)
