from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.models import Stock, Subscription, Signal
from sqlalchemy import select
from moexalgo import Ticker
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    logger.info(f"Получена команда /start от пользователя {message.from_user.id}")
    await message.answer("Добро пожаловать! Используйте команды:\n/stocks - список всех бумаг\n/price [ticker] - текущая цена\n/subscribe [ticker] - подписаться на уведомления")

@router.message(Command("stocks"))
async def cmd_stocks(message: Message, session: AsyncSession):
    logger.info(f"Получена команда /stocks от пользователя {message.from_user.id}")
    try:
        result = await session.execute(select(Stock))
        stocks = result.scalars().all()
        if not stocks:
            await message.answer("Акции не найдены.")
            return

        # Создаём кнопки для каждой акции
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for stock in stocks:
            ticker = stock.ticker
            price = stock.last_price if stock.last_price is not None else "N/A"
            button_text = f"{ticker}: {stock.name} ({price} RUB)"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text=button_text, callback_data=f"stock_{ticker}")
            ])
        await message.answer("Доступные акции:", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при получении акций: {e}")
        await message.answer("Произошла ошибка при получении акций.")

@router.callback_query(lambda c: c.data.startswith("stock_"))
async def process_stock_selection(callback_query: CallbackQuery, session: AsyncSession):
    ticker = callback_query.data.split("_")[1]
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} выбрал акцию {ticker}")

    # Кнопки для действий с акцией
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Текущая цена", callback_data=f"price_{ticker}")],
        [InlineKeyboardButton(text="Подписаться", callback_data=f"subscribe_{ticker}")]
    ])
    await callback_query.message.answer(f"Вы выбрали {ticker}. Что хотите сделать?", reply_markup=keyboard)
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith("price_"))
async def process_price(callback_query: CallbackQuery):
    ticker = callback_query.data.split("_")[1]
    user_id = callback_query.from_user.id
    logger.info(f"Получена команда /price для {ticker} от пользователя {user_id}")
    try:
        stock = Ticker(ticker.replace(".ME", ""))
        data = stock.candles(period="D", limit=1)
        if data.empty:
            await callback_query.message.answer(f"Данные для {ticker} не найдены.")
            return

        current_price = data.iloc[-1]["close"]
        await callback_query.message.answer(f"Текущая цена {ticker}: {current_price} RUB")
    except Exception as e:
        logger.error(f"Ошибка при получении цены для {ticker}: {e}")
        await callback_query.message.answer(f"Ошибка при получении данных для {ticker}.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith("subscribe_"))
async def process_subscribe(callback_query: CallbackQuery, session: AsyncSession):
    ticker = callback_query.data.split("_")[1]
    user_id = callback_query.from_user.id
    logger.info(f"Получена команда /subscribe для {ticker} от пользователя {user_id}")
    try:
        stock = await session.execute(select(Stock).where(Stock.ticker == ticker))
        stock = stock.scalars().first()
        if not stock:
            await callback_query.message.answer(f"Акция {ticker} не найдена.")
            return
        subscription = await session.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.ticker == ticker
            )
        )
        if subscription.scalars().first():
            await callback_query.message.answer(f"Вы уже подписаны на {ticker}.")
            return
        new_subscription = Subscription(user_id=user_id, ticker=ticker)
        session.add(new_subscription)
        await session.commit()
        await callback_query.message.answer(f"Вы успешно подписались на {ticker}!")
    except Exception as e:
        logger.error(f"Ошибка при подписке на {ticker}: {e}")
        await callback_query.message.answer(f"Ошибка при подписке на {ticker}.")
    await callback_query.answer()

@router.message(Command("price"))
async def cmd_price(message: Message, session: AsyncSession):
    ticker = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if not ticker:
        await message.answer("Укажите тикер, например: /price GAZP")
        return
    await process_price(CallbackQuery(
        id="manual_price",
        from_user=message.from_user,
        message=message,
        chat_instance="manual",
        data=f"price_{ticker}"
    ))

@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, session: AsyncSession):
    ticker = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if not ticker:
        await message.answer("Укажите тикер, например: /subscribe GAZP")
        return
    await process_subscribe(CallbackQuery(
        id="manual_subscribe",
        from_user=message.from_user,
        message=message,
        chat_instance="manual",
        data=f"subscribe_{ticker}"
    ), session)

@router.message(Command("moex"))
async def cmd_moex(message: Message):
    logger.info(f"Получена команда /moex от пользователя {message.from_user.id}")
    ticker = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if not ticker:
        await message.answer("Укажите тикер, например: /moex SBER")
        return
    try:
        stock = Ticker(ticker.replace(".ME", ""))
        data = stock.candles(period="D", limit=1)
        if data.empty:
            await message.answer(f"Данные MOEX для {ticker} не найдены.")
            return
        last_price = data.iloc[-1]["close"]
        await message.answer(f"Последняя цена {ticker} на MOEX: {last_price} RUB")
    except Exception as e:
        logger.error(f"Ошибка при получении данных MOEX для {ticker}: {e}")
        await message.answer(f"Ошибка при получении данных MOEX для {ticker}.")

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