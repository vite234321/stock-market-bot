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
        async with get_db() as session:
            data["session"] = session
            return await handler(event, data)