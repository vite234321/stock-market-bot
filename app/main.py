import os
import signal
import sys
import asyncio
import logging
from aiogram import Bot, Dispatcher
from .handlers import router
from .api import app as fastapi_app

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=os.getenv("BOT_TOKEN", "<YOUR_BOT_TOKEN>"))
dp = Dispatcher()
dp.include_router(router)

async def main():
    try:
        # Clear any existing webhook and reset update queue
        await bot.delete_webhook()
        await bot.get_updates(offset=-1)
        logger.info("Webhook deleted, update queue cleared, starting polling")
        await dp.start_polling(bot, timeout=20, skip_updates=True)
    except Exception as e:
        logger.error(f"Error in polling: {e}")
        raise

# Handle SIGTERM for graceful shutdown
def handle_shutdown(signum, frame):
    logger.info("Received SIGTERM, initiating shutdown")
    asyncio.get_event_loop().create_task(shutdown_bot())

async def shutdown_bot():
    try:
        await bot.session.close()
        logger.info("Bot session closed")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
        sys.exit(1)

signal.signal(signal.SIGTERM, handle_shutdown)

if __name__ == "__main__":
    asyncio.run(main())
