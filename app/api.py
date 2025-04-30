# app/api.py
from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import aiogram
from app.handlers import router
from app.middlewares import DbSessionMiddleware
from app.database import init_db, async_session
from app.trading import TradingBot
from app.models import User
from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging
import os
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Выводим версию aiogram в логи
logger.info(f"Используемая версия aiogram: {aiogram.__version__}")

# Инициализация бота
bot = Bot(
    token=os.getenv("BOT_TOKEN"),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

# Инициализация диспетчера
dp = Dispatcher()

# Регистрируем router в диспетчере
dp.include_router(router)

# Регистрируем middleware через диспетчер
dp.update.middleware(DbSessionMiddleware())

# Инициализация торгового бота
trading_bot = TradingBot()

# Инициализация планировщика
scheduler = AsyncIOScheduler()

async def run_autotrading():
    logger.info("Запуск автоторговли для всех пользователей")
    async with async_session() as session:
        try:
            result = await session.execute(
                select(User.user_id).where(
                    (User.tinkoff_token != None) & (User.autotrading_enabled == True)
                ).distinct()
            )
            user_ids = [row[0] for row in result.fetchall()]
            for user_id in user_ids:
                await trading_bot.analyze_and_trade(session, user_id)
                try:
                    await bot.send_message(user_id, "🤖 Запущена автоторговля для ваших акций!")
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
        except Exception as e:
            logger.error(f"Ошибка при запуске автоторговли: {e}")

@app.on_event("startup")
async def on_startup():
    logger.info("Запуск бота...")
    # Инициализация базы данных
    try:
        await init_db()
        logger.info("База данных успешно инициализирована.")
    except Exception as e:
        logger.error(f"Не удалось инициализировать базу данных: {e}")
        logger.warning("Продолжаем работу без базы данных. Некоторые функции могут быть недоступны.")
    # Удаляем вебхук и начинаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Вебхук удалён, очередь обновлений очищена")
    logger.info("Запуск polling на Heroku")
    # Запускаем polling через диспетчер
    asyncio.create_task(dp.start_polling(bot))
    # Запускаем автоторговлю каждые 5 минут
    scheduler.add_job(run_autotrading, "interval", minutes=5)
    scheduler.start()

@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Остановка бота...")
    scheduler.shutdown()
    await bot.session.close()

@app.post("/signals")
async def receive_signal(signal: dict):
    ticker = signal.get("ticker")
    signal_type = signal.get("signal_type")
    value = signal.get("value")
    logger.info(f"Получен сигнал: {ticker} - {signal_type} - {value}")
    return {"status": "received"}