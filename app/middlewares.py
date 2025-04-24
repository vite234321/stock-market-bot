from aiogram import BaseMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from typing import Callable, Dict, Any, Awaitable

class DbSessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        # Получаем генератор сессии
        session_gen = get_db()
        # Извлекаем сессию из генератора
        session = await anext(session_gen)
        try:
            # Передаём сессию в обработчик через data
            data["session"] = session
            result = await handler(event, data)
            # Подтверждаем транзакцию, если всё прошло успешно
            await session.commit()
            return result
        except Exception as e:
            # Откатываем транзакцию в случае ошибки
            await session.rollback()
            raise e
        finally:
            # Закрываем сессию
            await session.close()
            # Закрываем генератор
            try:
                await anext(session_gen)
            except StopAsyncIteration:
                pass