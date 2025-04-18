import logging
import asyncio
import httpx
from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from .models import Subscription
from .plot import generate_price_plot
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
router = Router()
@router.message(CommandStart())
async def cmd_start(message: types.Message):
    logger.info(f"Received /start command from user {message.from_user.id}")
    await message.answer("Добро пожаловать! Используйте команды: /stocks, /price <ticker>, /subscribe <ticker>")

async def fetch_stocks(max_attempts=3, delay=2):
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("https://stock-market-collector.herokuapp.com/stocks")
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError as e:
                    logger.error(f"Failed to parse JSON from stocks API: {e}, response: {response.text}")
                    raise
        except httpx.HTTPStatusError as e:
            logger.warning(f"Attempt {attempt} failed with HTTP error: {e}")
            if attempt == max_attempts:
                raise
        except Exception as e:
            logger.warning(f"Attempt {attempt} failed: {e}")
            if attempt == max_attempts:
                raise
        await asyncio.sleep(delay)
    return None

@router.message(Command("stocks"))
async def cmd_stocks(message: types.Message):
    try:
        stocks = await fetch_stocks()
        if not stocks:
            await message.answer("Нет доступных акций")
            return
        response_text = "\n".join([f"{s['ticker']}: {s['last_price']} RUB" for s in stocks])
        await message.answer(response_text)
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching stocks: {e}")
        await message.answer("Ошибка при получении данных об акциях")
    except Exception as e:
        logger.error(f"Unexpected error in cmd_stocks: {e}")
        await message.answer(f"Произошла ошибка: {e}")

async def fetch_stock_price(ticker, max_attempts=3, delay=2):
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"https://stock-market-collector.herokuapp.com/stocks/{ticker}")
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError as e:
                    logger.error(f"Failed to parse JSON for {ticker}: {e}, response: {response.text}")
                    raise
        except httpx.HTTPStatusError as e:
            logger.warning(f"Attempt {attempt} failed with HTTP error: {e}")
            if attempt == max_attempts:
                raise
        except Exception as e:
            logger.warning(f"Attempt {attempt} failed: {e}")
            if attempt == max_attempts:
                raise
        await asyncio.sleep(delay)
    return None

@router.message(Command("price"))
async def cmd_price(message: types.Message):
    try:
        ticker = message.text.split()[1].upper()
        stock = await fetch_stock_price(ticker)
        if not stock:
            await message.answer(f"Не удалось получить данные для {ticker}")
            return
        plot = await generate_price_plot(ticker)
        if plot:
            await message.answer_photo(plot, caption=f"{ticker}: {stock['last_price']} RUB")
        else:
            await message.answer(f"Не удалось создать график для {ticker}")
    except IndexError:
        await message.answer("Укажите тикер, например: /price SBER.ME")
    except httpx.HTTPStatusError:
        await message.answer(f"Акция {ticker} не найдена")
    except Exception as e:
        logger.error(f"Error in cmd_price for {ticker}: {e}")
        await message.answer(f"Ошибка при получении цены для {ticker}: {e}")
@router.message(Command("subscribe"))
async def cmd_subscribe(message: types.Message, db: AsyncSession):
    try:
        ticker = message.text.split()[1].upper()
        subscription = Subscription(user_id=message.from_user.id, ticker=ticker)
        db.add(subscription)
        await db.commit()
        await message.answer(f"Вы подписаны на уведомления по {ticker}")
    except IndexError:
        await message.answer("Укажите тикер, например: /subscribe SBER.ME")
    except Exception as e:
        logger.error(f"Error in cmd_subscribe: {e}")
        await message.answer(f"Ошибка при подписке: {e}")
