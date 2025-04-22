import os
import logging
from fastapi import FastAPI, HTTPException
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from app.database import get_session
from app.handlers import router as handlers_router
from app.middlewares import DbSessionMiddleware
from app.models import Subscription
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация FastAPI
app = FastAPI(title="Stock Market Bot")

# Инициализация Telegram-бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения")

session = AiohttpSession()
bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(handlers_router)
dp.message.middleware(DbSessionMiddleware())

# Модель для сигналов
class Signal(BaseModel):
    ticker: str
    signal_type: str
    value: float

# Запуск polling при старте приложения
@app.on_event("startup")
async def on_startup():
    logger.info("Запуск бота...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Вебхук удален, очередь обновлений очищена")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка при запуске polling: {e}")
        raise

# Эндпоинт для проверки подключения к базе данных
@app.get("/test-db")
async def test_db():
    try:
        async with get_session() as session:
            result = await session.execute(select(Subscription))
            await session.commit()
            return {"status": "Database connection successful", "subscriptions_count": len(result.scalars().all())}
    except Exception as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# Эндпоинт для получения сигналов
@app.post("/signals")
async def receive_signal(signal: Signal, session: AsyncSession = Depends(get_session)):
    try:
        # Найти подписчиков для тикера
        subscriptions = await session.execute(
            select(Subscription).where(Subscription.ticker == signal.ticker)
        )
        subscriptions = subscriptions.scalars().all()

        # Отправить уведомления
        for sub in subscriptions:
            message = f"📊 Сигнал для {signal.ticker}: {signal.signal_type} (значение: {signal.value})"
            try:
                await bot.send_message(chat_id=sub.user_id, text=message)
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения пользователю {sub.user_id}: {e}")

        return {"status": "Signal processed", "notified_users": len(subscriptions)}
    except Exception as e:
        logger.error(f"Ошибка обработки сигнала: {e}")
        raise HTTPException(status_code=500, detail=f"Signal processing error: {str(e)}")
