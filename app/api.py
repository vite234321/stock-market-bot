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
from app.models import User, Stock
from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from tinkoff.invest import AsyncClient, InstrumentIdType
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
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

# Инициализация диспетчера
dp = Dispatcher()

# Регистрируем router в диспетчере
dp.include_router(router)

# Регистрируем middleware через диспетчер
dp.update.middleware(DbSessionMiddleware(async_session))

# Инициализация торгового бота
trading_bot = TradingBot(bot)

# Инициализация планировщика
scheduler = AsyncIOScheduler()

async def run_autotrading():
    logger.info("Запуск автоторговли для всех пользователей")
    async with async_session() as session:
        try:
            result = await session.execute(
                select(User).where(
                    (User.tinkoff_token != None) & (User.autotrading_enabled == True)
                )
            )
            users = result.scalars().all()
            if not users:
                logger.info("Нет пользователей с включённой автоторговлей")
                return
            for user in users:
                logger.info(f"Обработка пользователя {user.user_id}")
                await trading_bot.analyze_and_trade(session, user.user_id)
                logger.info(f"Статус бота для пользователя {user.user_id}: {trading_bot.get_status()}")
        except Exception as e:
            logger.error(f"Ошибка при запуске автоторговли: {e}")

async def update_figi_for_all_stocks():
    logger.info("Запуск обновления FIGI для всех акций")
    async with async_session() as session:
        try:
            # Получаем первого пользователя с токеном для запросов к API
            user_result = await session.execute(
                select(User).where(User.tinkoff_token != None).limit(1)
            )
            user = user_result.scalars().first()
            if not user:
                logger.warning("Не найден пользователь с токеном Tinkoff API для обновления FIGI")
                return

            async with AsyncClient(user.tinkoff_token) as client:
                stocks_result = await session.execute(
                    select(Stock).where(Stock.figi == None)
                )
                stocks = stocks_result.scalars().all()
                if not stocks:
                    logger.info("Все акции уже имеют FIGI")
                    return

                for stock in stocks:
                    try:
                        response = await client.instruments.share_by(
                            id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                            class_code="TQBR",
                            id=stock.ticker
                        )
                        stock.figi = response.instrument.figi
                        session.add(stock)
                        logger.info(f"FIGI для {stock.ticker} обновлён: {stock.figi}")
                    except Exception as e:
                        logger.error(f"Не удалось обновить FIGI для {stock.ticker}: {e}")
                        continue
                await session.commit()
                logger.info("Обновление FIGI завершено")
        except Exception as e:
            logger.error(f"Ошибка при обновлении FIGI: {e}")

@app.on_event("startup")
async def on_startup():
    logger.info("Запуск бота...")
    # Инициализация базы данных
    try:
        await init_db()
        logger.info("База данных успешно инициализирована.")
    except Exception as e:
        logger.error(f"Не удалось инициализировать базу данных: {e}")
        raise
    # Удаляем вебхук и начинаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Вебхук удалён, очередь обновлений очищена")
    logger.info("Запуск polling на Heroku")
    # Запускаем polling через диспетчер
    asyncio.create_task(dp.start_polling(bot))
    # Запускаем автоторговлю каждые 5 минут
    scheduler.add_job(run_autotrading, "interval", minutes=5)
    # Запускаем обновление FIGI каждый час
    scheduler.add_job(update_figi_for_all_stocks, "interval", hours=1)
    scheduler.start()
    logger.info("Планировщик запущен")

@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Остановка бота...")
    scheduler.shutdown()
    await dp.stop_polling()
    await bot.session.close()

@app.post("/signals")
async def receive_signal(signal: dict):
    ticker = signal.get("ticker")
    signal_type = signal.get("signal_type")
    value = signal.get("value")
    logger.info(f"Получен сигнал: {ticker} - {signal_type} - {value}")
    return {"status": "received"}