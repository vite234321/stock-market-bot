from fastapi import FastAPI, Depends
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from .models import Subscription
from .database import get_db
from .handlers import router
from .middlewares import DbSessionMiddleware
import os
import asyncio
import httpx

app = FastAPI(title="Telegram Bot API")
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()
dp.include_router(router)
dp.update.middleware(DbSessionMiddleware())

# Глобальная переменная для контроля polling
polling_task = None

# Тестовый эндпоинт для проверки подключения к БД
@app.get("/test-db")
async def test_db(db: AsyncSession = Depends(get_db)):
    try:
        # Простой запрос для проверки
        result = await db.execute(select(Subscription))
        subscriptions = result.scalars().all()
        return {"status": "ok", "subscriptions": len(subscriptions)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/signals")
async def receive_signal(signal: dict, db: AsyncSession = Depends(get_db)):
    print(f"Получен сигнал: {signal}")
    ticker = signal["ticker"]
    try:
        result = await db.execute(select(Subscription).where(Subscription.ticker == ticker))
        for sub in result.scalars().all():
            await bot.send_message(
                sub.user_id,
                f"Сигнал для {ticker}: {signal['signal_type']} ({signal['value']} RUB)"
            )
        return {"status": "ok"}
    except Exception as e:
        print(f"Ошибка при отправке уведомления: {e}")
        return {"status": "error"}

@app.on_event("startup")
async def on_startup():
    global polling_task
    try:
        await bot.delete_webhook()
        print("Webhook удалён, начинаем polling")
        polling_task = asyncio.create_task(dp.start_polling(bot))
        # Добавляем задачу для пинга каждые 15 минут
        async def keep_alive():
            async with httpx.AsyncClient() as client:
                while True:
                    try:
                        await client.get("https://stock-market-bot.herokuapp.com/test-db")
                        print("Keep-alive пинг выполнен")
                    except Exception as e:
                        print(f"Ошибка keep-alive пинга: {e}")
                    await asyncio.sleep(15 * 60)  # 15 минут
        asyncio.create_task(keep_alive())
    except Exception as e:
        print(f"Ошибка при запуске polling: {e}")

@app.on_event("shutdown")
async def on_shutdown():
    global polling_task
    try:
        if polling_task:
            polling_task.cancel()
            print("Polling остановлен")
        await bot.session.close()
        print("Бот остановлен")
    except Exception as e:
        print(f"Ошибка при остановке бота: {e}")
