from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from .models import Subscription
from .plot import generate_price_plot
import httpx

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("Добро пожаловать! Используйте команды: /stocks, /price <ticker>, /subscribe <ticker>")

@router.message(Command("stocks"))
async def cmd_stocks(message: types.Message):
    async with httpx.AsyncClient() as client:
        response = await client.get("http://data-collector:8000/stocks")
        stocks = response.json()
        if not stocks:
            await message.answer("Нет доступных акций")
            return
        response_text = "\n".join([f"{s['ticker']}: {s['last_price']} RUB" for s in stocks])
        await message.answer(response_text)

@router.message(Command("price"))
async def cmd_price(message: types.Message):
    try:
        ticker = message.text.split()[1].upper()
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://data-collector:8000/stocks/{ticker}")
            response.raise_for_status()
            stock = response.json()
            plot = generate_price_plot(ticker)
            if plot:
                await message.answer_photo(plot, caption=f"{ticker}: {stock['last_price']} RUB")
            else:
                await message.answer(f"Не удалось создать график для {ticker}")
    except IndexError:
        await message.answer("Укажите тикер, например: /price SBER.ME")
    except httpx.HTTPStatusError:
        await message.answer(f"Акция {ticker} не найдена")

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
        await message.answer(f"Ошибка при подписке: {e}")
