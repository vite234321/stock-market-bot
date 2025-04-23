from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.models import Stock, Subscription, Signal
from sqlalchemy import select
import yfinance as yf
from moexalgo import Ticker
import matplotlib.pyplot as plt
import io
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    logger.info(f"Получена команда /start от пользователя {message.from_user.id}")
    await message.answer("Добро пожаловать! Используйте команды: /stocks, /price [ticker], /moex [ticker], /subscribe [ticker], /signals [ticker]")

@router.message(Command("stocks"))
async def cmd_stocks(message: Message, session: AsyncSession):
    logger.info(f"Получена команда /stocks от пользователя {message.from_user.id}")
    try:
        result = await session.execute(select(Stock))
        stocks = result.scalars().all()
        if not stocks:
            await message.answer("Акции не найдены.")
            return
        response = "Доступные акции:\n" + "\n".join([f"{stock.ticker}: {stock.name}" for stock in stocks])
        await message.answer(response)
    except Exception as e:
        logger.error(f"Ошибка при получении акций: {e}")
        await message.answer("Произошла ошибка при получении акций.")

@router.message(Command("price"))
async def cmd_price(message: Message, session: AsyncSession):
    logger.info(f"Получена команда /price от пользователя {message.from_user.id}")
    ticker = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if not ticker:
        await message.answer("Укажите тикер, например: /price SBER.ME")
        return
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1mo")
        if hist.empty:
            await message.answer(f"Данные для {ticker} не найдены.")
            return
        plt.figure(figsize=(10, 5))
        plt.plot(hist.index, hist['Close'], label=f"{ticker} Close Price")
        plt.title(f"{ticker} Price Over Last Month")
        plt.xlabel("Date")
        plt.ylabel("Price")
        plt.legend()
        plt.grid()
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        await message.answer_photo(photo=buf, caption=f"График цен для {ticker} за последний месяц")
        plt.close()
    except Exception as e:
        logger.error(f"Ошибка при получении цены для {ticker}: {e}")
        await message.answer(f"Ошибка при получении данных для {ticker}.")

@router.message(Command("moex"))
async def cmd_moex(message: Message):
    logger.info(f"Получена команда /moex от пользователя {message.from_user.id}")
    ticker = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if not ticker:
        await message.answer("Укажите тикер, например: /moex SBER")
        return
    try:
        stock = Ticker(ticker)
        data = stock.candles(date=datetime.now().strftime("%Y-%m-%d"), period="D")
        if not data:
            await message.answer(f"Данные MOEX для {ticker} не найдены.")
            return
        last_price = data[-1]["close"]
        await message.answer(f"Последняя цена {ticker} на MOEX: {last_price} RUB")
    except Exception as e:
        logger.error(f"Ошибка при получении данных MOEX для {ticker}: {e}")
        await message.answer(f"Ошибка при получении данных MOEX для {ticker}.")

@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, session: AsyncSession):
    logger.info(f"Получена команда /subscribe от пользователя {message.from_user.id}")
    ticker = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if not ticker:
        await message.answer("Укажите тикер, например: /subscribe SBER.ME")
        return
    try:
        stock = await session.execute(select(Stock).where(Stock.ticker == ticker))
        stock = stock.scalars().first()
        if not stock:
            await message.answer(f"Акция {ticker} не найдена.")
            return
        subscription = await session.execute(
            select(Subscription).where(
                Subscription.user_id == message.from_user.id,
                Subscription.ticker == ticker
            )
        )
        if subscription.scalars().first():
            await message.answer(f"Вы уже подписаны на {ticker}.")
            return
        new_subscription = Subscription(user_id=message.from_user.id, ticker=ticker)
        session.add(new_subscription)
        await session.commit()
        await message.answer(f"Вы успешно подписались на {ticker}!")
    except Exception as e:
        logger.error(f"Ошибка при подписке на {ticker}: {e}")
        await message.answer(f"Ошибка при подписке на {ticker}.")

@router.message(Command("signals"))
async def cmd_signals(message: Message, session: AsyncSession):
    logger.info(f"Получена команда /signals от пользователя {message.from_user.id}")
    ticker = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if not ticker:
        await message.answer("Укажите тикер, например: /signals SBER.ME")
        return
    try:
        signals = await session.execute(
            select(Signal).where(Signal.ticker == ticker)
        )
        signals = signals.scalars().all()
        if not signals:
            await message.answer(f"Сигналы для {ticker} не найдены.")
            return
        response = f"Сигналы для {ticker}:\n" + "\n".join([f"{s.signal_type}: {s.value} ({s.created_at})" for s in signals])
        await message.answer(response)
    except Exception as e:
        logger.error(f"Ошибка при получении сигналов для {ticker}: {e}")
        await message.answer(f"Ошибка при получении сигналов для {ticker}.")