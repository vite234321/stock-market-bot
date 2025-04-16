from fastapi import FastAPI, Depends
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from .models import Subscription
from .database import get_db
from .handlers import router

app = FastAPI(title="Telegram Bot API")
bot = Bot(token="<YOUR_BOT_TOKEN>")
dp = Dispatcher()
dp.include_router(router)

@app.post("/webhook")
async def webhook(update: dict):
    telegram_update = Update(**update)
    await dp.process_update(telegram_update)
    return {"status": "ok"}

@app.post("/signals")
async def receive_signal(signal: dict, db: AsyncSession = Depends(get_db)):
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
