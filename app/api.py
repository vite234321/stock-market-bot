import os
import logging
from fastapi import FastAPI, Depends
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.handlers import router
from app.middlewares import DatabaseSessionMiddleware
from app.database import get_session_pool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Stock
import asyncio

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Инициализация бота
bot = Bot(
    token=os.getenv("BOT_TOKEN"),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Регистрация роутеров и middleware
dp.include_router(router)
dp.message.middleware(DatabaseSessionMiddleware(get_session_pool()))

async def on_startup():
    logger.info("Запуск бота...")
    # Очистка вебхука
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Вебхук удален, очередь обновлений очищена")
    # Запуск polling в одном процессе
    if os.getenv("HEROKU_APP_NAME"):
        logger.info("Запуск polling на Heroku")
        await dp.start_polling(bot, polling_timeout=30)

@app.on_event("startup")
async def startup_event():
    # Запускаем polling в отдельной задаче
    asyncio.create_task(on_startup())

@app.get("/test-db")
async def test_db(session: AsyncSession = Depends(get_session_pool())):
    try:
        result = await session.execute(select(Stock))
        stocks = result.scalars().all()
        return {"status": "success", "stocks": [stock.ticker for stock in stocks]}
    except Exception as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        return {"status": "error", "message": str(e)}