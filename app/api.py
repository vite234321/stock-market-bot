import os
import signal
import sys
import asyncio
import logging
import httpx
from fastapi import FastAPI, Depends
from aiogram import Bot, Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from .models import Subscription
from .database import get_db
from .handlers import router
from .middlewares import DbSessionMiddleware

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Telegram Bot API")
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()
dp.include_router(router)
dp.update.middleware(DbSessionMiddleware())

# Глобальная переменная для управления polling
polling_task = None

# Тестовый эндпоинт для проверки подключения к базе данных
@app.get("/test-db")
async def test_db(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(Subscription))
        subscriptions = result.scalars().all()
        return {"status": "ok", "subscriptions": len(subscriptions)}
    except Exception as e:
        logger.error(f"Ошибка тестирования базы данных: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/signals")
async def receive_signal(signal: dict, db: AsyncSession = Depends(get_db)):
    logger.info(f"Получен сигнал: {signal}")
    ticker = signal.get("ticker")
    try:
        result = await db.execute(select(Subscription).where(Subscription.ticker == ticker))
        for sub in result.scalars().all():
            await bot.send_message(
                sub.user_id,
                f"Сигнал для {ticker}: {signal.get('signal_type')} ({signal.get('value')} RUB)"
            )
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}")
        return {"status": "error"}

@app.on_event("startup")
async def on_startup():
    global polling_task
    try:
        # Очистка вебхуков и очереди обновлений с таймаутом
        async with bot.session:
            await bot.delete_webhook(drop_pending_updates=True)
            logger.info("Вебхук удален, очередь обновлений очищена")
        # Запуск polling
        logger.info("Запуск polling")
        polling_task = asyncio.create_task(dp.start_polling(bot, timeout=20, skip_updates=True))
        
        # Keep-alive пинг каждые 15 минут
        async def keep_alive():
            async with httpx.AsyncClient() as client:
                while True:
                    try:
                        await client.get(f"https://{os.getenv('HEROKU_APP_NAME')}.herokuapp.com/test-db")
                        logger.info("Keep-alive пинг выполнен")
                    except Exception as e:
                        logger.error(f"Ошибка keep-alive пинга: {e}")
                    await asyncio.sleep(15 * 60)  # 15 минут
        asyncio.create_task(keep_alive())
    except Exception as e:
        logger.error(f"Ошибка запуска polling: {e}")
        raise

@app.on_event("shutdown")
async def on_shutdown():
    global polling_task
    try:
        if polling_task:
            polling_task.cancel()
            try:
                await polling_task  # Ожидание отмены
            except asyncio.CancelledError:
                pass
            logger.info("Polling остановлен")
        await bot.session.close()
        logger.info("Сессия бота закрыта")
    except Exception as e:
        logger.error(f"Ошибка остановки бота: {e}")

# Обработка SIGTERM для корректного завершения
def handle_shutdown(signum, frame):
    logger.info("Получен SIGTERM, инициируется завершение")
    asyncio.get_event_loop().create_task(shutdown_bot())

async def shutdown_bot():
    global polling_task
    try:
        if polling_task:
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
            logger.info("Polling остановлен во время SIGTERM")
        await bot.session.close()
        logger.info("Сессия бота закрыта во время SIGTERM")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Ошибка во время завершения SIGTERM: {e}")
        sys.exit(1)

signal.signal(signal.SIGTERM, handle_shutdown)
