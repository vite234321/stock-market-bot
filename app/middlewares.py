# app/middlewares.py
from aiogram import BaseMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Callable, Dict, Any, Awaitable

class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory, trading_bot):
        super().__init__()
        self.session_factory = session_factory
        self.trading_bot = trading_bot

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            data["trading_bot"] = self.trading_bot  # Передаём trading_bot в data
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception as e:
                await session.rollback()
                raise e