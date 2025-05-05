from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.handlers import router
from app.middlewares import DbSessionMiddleware
from app.database import init_db, async_session, engine, dispose_engine
from app.trading import TradingBot
from app.models import User, Stock
from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging
import os
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

logger.info(f"Используемая версия aiogram: {aiogram.__version__}")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения")

ADMIN_ID = os.getenv("ADMIN_ID")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher()
dp.include_router(router)
trading_bot = TradingBot(bot)
dp.update.middleware(DbSessionMiddleware(async_session, trading_bot))

dp.startup_timeout = 120
dp.shutdown_timeout = 120
dp.retry_times = 10
dp.retry_interval = 10

scheduler = AsyncIOScheduler()

async def notify_admin(message: str):
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, message, parse_mode="HTML")
            logger.info(f"Уведомление отправлено администратору: {message}")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление администратору: {e}")
    else:
        logger.warning("ADMIN_ID не установлен, уведомление не отправлено")

@dp.errors()
async def on_error(update, exception):
    logger.error(f"Ошибка в диспетчере: {exception}")
    if isinstance(exception, aiogram.exceptions.TelegramNetworkError):
        tryings = getattr(exception, 'tryings', 0)
        if tryings >= dp.retry_times:
            error_message = f"⚠️ Превышено количество попыток подключения к Telegram API ({tryings}). Бот может работать нестабильно."
            await notify_admin(error_message)

async def start_streaming_for_users():
    logger.info("Запуск стриминга для всех пользователей")
    async with async_session() as session:
        try:
            result = await session.execute(
                select(User).where(
                    (User.moex_token != None) & (User.autotrading_enabled == True)
                )
            )
            users = result.scalars().all()
            if not users:
                logger.info("Нет пользователей с включённой автоторговлей")
                return
            for user in users:
                logger.info(f"Запуск стриминга для пользователя {user.user_id}")
                task = asyncio.create_task(trading_bot.stream_and_trade(user.user_id))
                trading_bot.stream_tasks[user.user_id] = task
        except Exception as e:
            logger.error(f"Ошибка при запуске стриминга: {e}")

async def send_daily_reports():
    logger.info("Отправка дневных отчётов")
    async with async_session() as session:
        try:
            result = await session.execute(
                select(User).where(
                    (User.moex_token != None) & (User.autotrading_enabled == True)
                )
            )
            users = result.scalars().all()
            for user in users:
                await trading_bot.send_daily_profit_report(session, user.user_id)
        except Exception as e:
            logger.error(f"Ошибка при отправке дневных отчётов: {e}")

@app.on_event("startup")
async def on_startup():
    logger.info("Запуск бота...")
    try:
        await init_db()
        logger.info("База данных успешно инициализирована.")
    except Exception as e:
        logger.error(f"Не удалось инициализировать базу данных: {e}")
        raise
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Вебхук удалён, очередь обновлений очищена")
    logger.info("Запуск polling на Heroku")
    asyncio.create_task(dp.start_polling(bot))
    
    await start_streaming_for_users()
    
    scheduler.add_job(send_daily_reports, "cron", hour=22, minute=0)
    scheduler.start()
    logger.info("Планировщик запущен")

@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Остановка бота...")
    trading_bot.stop_streaming()
    if trading_bot.stream_tasks:
        await asyncio.gather(*[task for task in trading_bot.stream_tasks.values()], return_exceptions=True)
        logger.info("Все задачи стриминга завершены")
    scheduler.shutdown()
    await dp.stop_polling()
    await bot.session.close()
    await dispose_engine()
    logger.info("Бот полностью остановлен")

@app.post("/signals")
async def receive_signal(signal: dict):
    ticker = signal.get("ticker")
    signal_type = signal.get("signal_type")
    value = signal.get("value")
    logger.info(f"Получен сигнал: {ticker} - {signal_type} - {value}")
    return {"status": "received"}