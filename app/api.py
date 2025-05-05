from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import aiogram
from app.handlers import router
from app.middlewares import DbSessionMiddleware
from app.database import init_db, get_session, dispose_engine
from app.trading import TradingBot
from app.models import User, Stock, FigiStatus
from sqlalchemy import select
from sqlalchemy.sql import text, func
from sqlalchemy.exc import DBAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging
import os
import asyncio
import uvicorn

# Проверка установки tinkoff-invest
try:
    import tinkoff
    import tinkoff.invest
    from tinkoff.invest import AsyncClient, InstrumentIdType
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info(f"Модуль tinkoff-invest успешно импортирован, версия: {tinkoff.invest.__version__}")
except ImportError as e:
    logging.basicConfig(level=logging.ERROR)
    logger = logging.getLogger(__name__)
    logger.error("Ошибка импорта tinkoff.invest. Убедитесь, что tinkoff-invest установлен в requirements.txt.")
    raise ImportError("Ошибка импорта tinkoff.invest. Убедитесь, что tinkoff-invest установлен в requirements.txt.") from e
from tinkoff.invest.exceptions import RequestError

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
dp.update.middleware(DbSessionMiddleware(async_sessionmaker, trading_bot))

dp.startup_timeout = 120
dp.shutdown_timeout = 120
dp.retry_times = 10
dp.retry_interval = 10

scheduler = AsyncIOScheduler()

async def notify_admin(message: str):
    """Отправка уведомления администратору."""
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, message, parse_mode="HTML")
            logger.info(f"Уведомление отправлено администратору: {message[:50]}...")
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления администратору: {e}")

async def check_database_health():
    """Проверка состояния базы данных при запуске."""
    logger.info("Проверка состояния базы данных...")
    try:
        async with get_session() as session:
            # Проверка подключения
            await session.execute(text("SELECT 1"))
            logger.info("Подключение к базе данных успешно.")

            # Проверка наличия таблицы User
            user_count = await session.execute(select(func.count()).select_from(User))
            logger.info(f"Найдено пользователей: {user_count.scalar()}")

            # Проверка наличия акций
            stock_count = await session.execute(select(func.count()).select_from(Stock))
            stock_count_value = stock_count.scalar()
            logger.info(f"Найдено акций: {stock_count_value}")

            # Если акций нет, добавляем тестовые акции
            if stock_count_value == 0:
                logger.warning("Акции отсутствуют в базе, добавляем тестовые акции...")
                test_stocks = [
                    Stock(ticker="GCHE.ME", name="Группа Черкизово", last_price=0.0, figi_status=FigiStatus.PENDING),
                    Stock(ticker="GAZA.ME", name="ГАЗ", last_price=0.0, figi_status=FigiStatus.PENDING),
                    Stock(ticker="SLEN.ME", name="Сургутнефтегаз", last_price=0.0, figi_status=FigiStatus.PENDING),
                    Stock(ticker="TASB.ME", name="Тамбовэнергосбыт", last_price=0.0, figi_status=FigiStatus.PENDING),
                    Stock(ticker="TGKB.ME", name="ТГК-2", last_price=0.0, figi_status=FigiStatus.PENDING),
                ]
                session.add_all(test_stocks)
                await session.commit()
                logger.info("Тестовые акции добавлены в базу.")

    except DBAPIError as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        await notify_admin(f"❌ Ошибка базы данных при запуске: {str(e)}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка при проверке базы данных: {e}")
        await notify_admin(f"❌ Неожиданная ошибка при запуске: {str(e)}")

async def update_stocks():
    """Обновление списка акций через Tinkoff API."""
    logger.info("Запуск обновления списка акций...")
    try:
        async with get_session() as session:
            # Получаем токен администратора для API запросов
            admin_token = os.getenv("TINKOFF_TOKEN")
            if not admin_token:
                logger.warning("Токен Tinkoff API не установлен, обновление акций пропущено.")
                return

            async with AsyncClient(admin_token) as client:
                result = await session.execute(select(Stock))
                stocks = result.scalars().all()

                for stock in stocks:
                    if stock.figi_status == FigiStatus.FAILED:
                        continue
                    try:
                        cleaned_ticker = stock.ticker.replace(".ME", "")
                        response = await client.instruments.share_by(
                            id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                            class_code="TQBR",
                            id=cleaned_ticker
                        )
                        if response.instrument and response.instrument.figi:
                            stock.figi = response.instrument.figi
                            stock.name = response.instrument.name
                            stock.figi_status = FigiStatus.SUCCESS
                            logger.info(f"Обновлён FIGI для {stock.ticker}: {stock.figi}")
                        else:
                            stock.figi_status = FigiStatus.FAILED
                            logger.warning(f"FIGI не найден для {stock.ticker}")
                        session.add(stock)
                        await session.commit()
                        await asyncio.sleep(0.5)  # Ограничение скорости запросов
                    except RequestError as e:
                        if "RESOURCE_EXHAUSTED" in str(e):
                            reset_time = int(e.metadata.ratelimit_reset) if e.metadata.ratelimit_reset else 60
                            logger.warning(f"Лимит запросов API, ожидание {reset_time} секунд...")
                            await asyncio.sleep(reset_time)
                        else:
                            logger.error(f"Ошибка API для {stock.ticker}: {e}")
                            stock.figi_status = FigiStatus.FAILED
                            session.add(stock)
                            await session.commit()
                    except Exception as e:
                        logger.error(f"Неожиданная ошибка при обновлении {stock.ticker}: {e}")
                        stock.figi_status = FigiStatus.FAILED
                        session.add(stock)
                        await session.commit()
    except Exception as e:
        logger.error(f"Ошибка при обновлении акций: {e}")
        await notify_admin(f"❌ Ошибка при обновлении акций: {str(e)}")

async def send_profit_report_wrapper(user_id: int):
    """Обёртка для отправки ежедневного отчёта о прибыли."""
    try:
        async with get_session() as session:
            await trading_bot.send_daily_profit_report(session, user_id)
    except Exception as e:
        logger.error(f"Ошибка при отправке ежедневного отчёта: {e}")
        await notify_admin(f"❌ Ошибка при отправке ежедневного отчёта: {str(e)}")

@app.get("/health")
async def health_check():
    """Эндпоинт для проверки работоспособности приложения."""
    return {"status": "ok"}

async def start_bot_polling():
    """Запуск поллинга бота в фоновом режиме."""
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        logger.info("Бот запущен в режиме поллинга.")
    except Exception as e:
        logger.error(f"Ошибка при запуске поллинга бота: {e}")
        await notify_admin(f"❌ Ошибка при запуске поллинга бота: {str(e)}")

@app.on_event("startup")
async def on_startup():
    """Инициализация при запуске приложения."""
    logger.info("Запуск приложения...")
    try:
        # Инициализация базы данных
        await init_db()
        logger.info("База данных инициализирована.")

        # Проверка состояния базы данных
        await check_database_health()

        # Запуск планировщика задач
        scheduler.add_job(update_stocks, 'interval', hours=24)
        if ADMIN_ID:
            scheduler.add_job(
                send_profit_report_wrapper,
                'interval',
                hours=24,
                args=[int(ADMIN_ID)],
                id='daily_profit_report'
            )
        scheduler.start()
        logger.info("Планировщик задач запущен.")

        # Запуск поллинга бота в фоновом режиме
        asyncio.create_task(start_bot_polling())

        # Уведомление администратора о запуске
        await notify_admin("✅ Бот успешно запущен!")
    except Exception as e:
        logger.error(f"Ошибка при запуске приложения: {e}")
        await notify_admin(f"❌ Ошибка при запуске бота: {str(e)}")
        raise

@app.on_event("shutdown")
async def on_shutdown():
    """Очистка при завершении работы приложения."""
    logger.info("Завершение работы приложения...")
    try:
        # Остановка планировщика
        scheduler.shutdown()
        logger.info("Планировщик задач остановлен.")

        # Остановка бота
        await dp.stop_polling()
        logger.info("Бот остановлен.")

        # Остановка торгового бота
        trading_bot.stop_streaming()
        logger.info("Торговый бот остановлен.")

        # Закрытие соединения с базой данных
        await dispose_engine()
        logger.info("Соединение с базой данных закрыто.")

        # Уведомление администратора о завершении
        await notify_admin("⏹️ Бот успешно остановлен.")
    except Exception as e:
        logger.error(f"Ошибка при завершении работы: {e}")
        await notify_admin(f"❌ Ошибка при остановке бота: {str(e)}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")