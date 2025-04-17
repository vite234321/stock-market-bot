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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Telegram Bot API")
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()
dp.include_router(router)
dp.update.middleware(DbSessionMiddleware())

# Global variable to control polling
polling_task = None

# Test endpoint for database connectivity
@app.get("/test-db")
async def test_db(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(Subscription))
        subscriptions = result.scalars().all()
        return {"status": "ok", "subscriptions": len(subscriptions)}
    except Exception as e:
        logger.error(f"Database test failed: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/signals")
async def receive_signal(signal: dict, db: AsyncSession = Depends(get_db)):
    logger.info(f"Received signal: {signal}")
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
        logger.error(f"Error sending notification: {e}")
        return {"status": "error"}

@app.on_event("startup")
async def on_startup():
    global polling_task
    try:
        # Clear any existing webhook and reset update queue
        await bot.delete_webhook()
        await bot.get_updates(offset=-1)
        logger.info("Webhook deleted, update queue cleared, starting polling")
        polling_task = asyncio.create_task(dp.start_polling(bot, timeout=20, skip_updates=True))
        
        # Keep-alive ping every 15 minutes
        async def keep_alive():
            async with httpx.AsyncClient() as client:
                while True:
                    try:
                        await client.get(f"https://{os.getenv('HEROKU_APP_NAME')}.herokuapp.com/test-db")
                        logger.info("Keep-alive ping completed")
                    except Exception as e:
                        logger.error(f"Keep-alive ping failed: {e}")
                    await asyncio.sleep(15 * 60)  # 15 minutes
        asyncio.create_task(keep_alive())
    except Exception as e:
        logger.error(f"Error starting polling: {e}")
        raise

@app.on_event("shutdown")
async def on_shutdown():
    global polling_task
    try:
        if polling_task:
            polling_task.cancel()
            logger.info("Polling stopped")
        await bot.session.close()
        logger.info("Bot session closed")
    except Exception as e:
        logger.error(f"Error stopping bot: {e}")

# Handle SIGTERM for graceful shutdown
def handle_shutdown(signum, frame):
    logger.info("Received SIGTERM, initiating shutdown")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_shutdown)
