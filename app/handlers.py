from aiogram import Router, Bot
from aiogram.filters import Command, Text
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.models import Stock, Subscription, Signal
from sqlalchemy import select
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()

# Главное меню
def get_main_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Все акции", callback_data="all_stocks_1")],
        [InlineKeyboardButton(text="📋 Мои акции", callback_data="list_stocks")],
        [InlineKeyboardButton(text="🔍 Цена акции", callback_data="check_price")],
        [InlineKeyboardButton(text="🔔 Подписаться", callback_data="subscribe")],
        [InlineKeyboardButton(text="📊 Сигналы", callback_data="signals")],
        [InlineKeyboardButton(text="🔎 Поиск", callback_data="search_stock")]
    ])
    return keyboard

@router.message(Command("start"))
async def cmd_start(message: Message):
    logger.info(f"Получена команда /start от пользователя {message.from_user.id}")
    welcome_text = (
        "🌟 <b>StockBot — Ваш помощник на MOEX!</b> 🌟\n\n"
        "Я помогу следить за всеми акциями в реальном времени! 🚀\n"
        "Что я умею:\n"
        "📈 Показать все акции с ценами\n"
        "📋 Показать ваши подписки\n"
        "🔍 Узнать цену акции\n"
        "🔔 Подписаться на уведомления\n"
        "📊 Показать сигналы роста\n"
        "🔎 Найти акцию по имени\n\n"
        "Выберите действие в меню 👇"
    )
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_menu())

@router.callback_query(lambda c: c.data == "list_stocks")
async def list_stocks(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил список своих акций")
    try:
        result = await session.execute(select(Stock))
        stocks = result.scalars().all()

        if not stocks:
            await callback_query.message.answer("Акции не найдены. Попробуйте позже.")
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for stock in stocks:
            ticker = stock.ticker
            price = stock.last_price if stock.last_price is not None else "N/A"
            button_text = f"{ticker}: {stock.name} ({price} RUB)"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text=button_text, callback_data=f"stock_{ticker}")
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_menu")])
        await callback_query.message.answer("📋 <b>Ваши акции:</b>", parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при получении акций: {e}")
        await callback_query.message.answer("Произошла ошибка при получении акций.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "check_price")
async def prompt_check_price(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет проверить цену акции")
    await callback_query.message.answer("🔍 Введите тикер акции (например, SBER.ME):")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "subscribe")
async def prompt_subscribe(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет подписаться на акции")
    await callback_query.message.answer("🔔 Введите тикер акции для подписки (например, SBER.ME):")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "signals")
async def prompt_signals(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} запросил сигналы")
    await callback_query.message.answer("📊 Введите тикер акции для проверки сигналов (например, SBER.ME):")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "search_stock")
async def prompt_search_stock(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет найти акцию")
    await callback_query.message.answer("🔎 Введите тикер или название акции (например, SBER или Сбербанк):")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} вернулся в меню")
    await callback_query.message.answer("🌟 Выберите действие:", reply_markup=get_main_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith("stock_"))
async def process_stock_selection(callback_query: CallbackQuery, session: AsyncSession):
    ticker = callback_query.data.split("_")[1]
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} выбрал акцию {ticker}")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Цена", callback_data=f"price_{ticker}")],
        [InlineKeyboardButton(text="🔔 Подписаться", callback_data=f"subscribe_{ticker}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="list_stocks")]
    ])
    await callback_query.message.answer(f"📊 Вы выбрали <b>{ticker}</b>. Что сделать?", parse_mode="HTML", reply_markup=keyboard)
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith("price_"))
async def process_price(callback_query: CallbackQuery, session: AsyncSession):
    ticker = callback_query.data.split("_")[1]
    user_id = callback_query.from_user.id
    logger.info(f"Получена команда /price для {ticker} от пользователя {user_id}")
    try:
        result = await session.execute(select(Stock).where(Stock.ticker == ticker))
        stock = result.scalars().first()
        if not stock:
            await callback_query.message.answer(f"Акция {ticker} не найдена в базе.")
            return

        await callback_query.message.answer(
            f"💰 <b>{stock.ticker}</b> ({stock.name})\n"
            f"📈 Цена: {stock.last_price} RUB\n"
            f"📊 Объём: {stock.volume} акций\n"
            f"🕒 Обновлено: {stock.updated_at}",
            parse_mode="HTML"
        )
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
        await callback_query.message.answer(f"🔔 Вы успешно подписались на <b>{ticker}</b>!", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при подписке на {ticker}: {e}")
        await callback_query.message.answer(f"Ошибка при подписке на {ticker}.")
    await callback_query.answer()

@router.message(Command("price"))
async def cmd_price(message: Message, session: AsyncSession):
    ticker = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if not ticker:
        await message.answer("🔍 Введите тикер акции (например, SBER.ME):")
        return
    await process_price(CallbackQuery(
        id="manual_price",
        from_user=message.from_user,
        message=message,
        chat_instance="manual",
        data=f"price_{ticker}"
    ), session)

@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, session: AsyncSession):
    ticker = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if not ticker:
        await message.answer("🔔 Введите тикер акции для подписки (например, SBER.ME):")
        return
    await process_subscribe(CallbackQuery(
        id="manual_subscribe",
        from_user=message.from_user,
        message=message,
        chat_instance="manual",
        data=f"subscribe_{ticker}"
    ), session)

@router.message(Command("signals"))
async def cmd_signals(message: Message, session: AsyncSession):
    ticker = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
    if not ticker:
        await message.answer("📊 Введите тикер акции для проверки сигналов (например, SBER.ME):")
        return
    try:
        signals = await session.execute(
            select(Signal).where(Signal.ticker == ticker)
        )
        signals = signals.scalars().all()
        if not signals:
            await message.answer(f"Сигналы для {ticker} не найдены.")
            return
        response = f"📊 <b>Сигналы для {ticker}</b>:\n" + "\n".join([f"🔹 {s.signal_type}: {s.value} ({s.created_at})" for s in signals])
        await message.answer(response, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при получении сигналов для {ticker}: {e}")
        await message.answer(f"Ошибка при получении сигналов для {ticker}.")

@router.callback_query(lambda c: c.data.startswith("all_stocks_"))
async def all_stocks(callback_query: CallbackQuery, session: AsyncSession):
    page = int(callback_query.data.split("_")[2])
    logger.info(f"Пользователь {callback_query.from_user.id} запросил все акции, страница {page}")
    try:
        # Получаем акции из базы с пагинацией
        page_size = 20
        offset = (page - 1) * page_size
        result = await session.execute(
            select(Stock).offset(offset).limit(page_size)
        )
        stocks = result.scalars().all()

        total_stocks_result = await session.execute(select(Stock))
        total_stocks = len(total_stocks_result.scalars().all())
        total_pages = (total_stocks + page_size - 1) // page_size

        if not stocks:
            await callback_query.message.answer("Акции не найдены.")
            return

        response = f"📈 <b>Все акции (Страница {page}/{total_pages})</b>:\n\n"
        for stock in stocks:
            price = stock.last_price if stock.last_price is not None else "N/A"
            response += f"🔹 {stock.ticker}: {stock.name} ({price} RUB)\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        buttons = []
        if page > 1:
            buttons.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"all_stocks_{page-1}"))
        if page < total_pages:
            buttons.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"all_stocks_{page+1}"))
        if buttons:
            keyboard.inline_keyboard.append(buttons)
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_menu")])

        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при получении списка всех акций: {e}")
        await callback_query.message.answer("Ошибка при загрузке списка акций.")
    await callback_query.answer()

@router.message(Text(startswith=["SBER", "GAZP", "LKOH", "YNDX", "ROSN", "TATN", "VTBR", "MGNT", "NVTK", "GMKN"]))
async def search_stock(message: Message, session: AsyncSession):
    query = message.text.strip().upper()
    logger.info(f"Пользователь {message.from_user.id} выполнил поиск: {query}")
    try:
        result = await session.execute(
            select(Stock).where(
                (Stock.ticker.ilike(f"%{query}%")) | (Stock.name.ilike(f"%{query}%"))
            )
        )
        stocks = result.scalars().all()

        if not stocks:
            await message.answer(f"Акции по запросу '{query}' не найдены.")
            return

        response = f"🔎 <b>Результаты поиска для '{query}'</b>:\n\n"
        for stock in stocks[:10]:  # Ограничим до 10 результатов
            price = stock.last_price if stock.last_price is not None else "N/A"
            response += f"🔹 {stock.ticker}: {stock.name} ({price} RUB)\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_menu")]
        ])
        await message.answer(response, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при поиске акций: {e}")
        await message.answer("Ошибка при поиске акций.")