from aiogram import Router, Bot
from aiogram.filters import Command, Text
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.models import Stock, Subscription, Signal
from sqlalchemy import select
from moexalgo import Market, Ticker
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()

# Главное меню
def get_main_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Список акций", callback_data="list_stocks")],
        [InlineKeyboardButton(text="🔍 Цена акции", callback_data="check_price")],
        [InlineKeyboardButton(text="🔔 Подписаться на акции", callback_data="subscribe")],
        [InlineKeyboardButton(text="📊 Мои сигналы", callback_data="signals")],
        [InlineKeyboardButton(text="🔎 Поиск акции", callback_data="search_stock")]
    ])
    return keyboard

@router.message(Command("start"))
async def cmd_start(message: Message):
    logger.info(f"Получена команда /start от пользователя {message.from_user.id}")
    welcome_text = (
        "🌟 <b>Добро пожаловать в StockBot!</b> 🌟\n\n"
        "Я помогу вам следить за акциями на MOEX! 🚀\n"
        "Вы можете:\n"
        "📈 Посмотреть список доступных акций\n"
        "🔍 Узнать текущую цену акции\n"
        "🔔 Подписаться на уведомления о росте\n"
        "📊 Проверить сигналы по акциям\n"
        "🔎 Найти акцию по тикеру или имени\n\n"
        "Выберите действие в меню ниже 👇"
    )
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_menu())

@router.callback_query(lambda c: c.data == "list_stocks")
async def list_stocks(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил список акций")
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
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")])
        await callback_query.message.answer("📈 <b>Доступные акции:</b>", parse_mode="HTML", reply_markup=keyboard)
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
        [InlineKeyboardButton(text="🔍 Текущая цена", callback_data=f"price_{ticker}")],
        [InlineKeyboardButton(text="🔔 Подписаться", callback_data=f"subscribe_{ticker}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="list_stocks")]
    ])
    await callback_query.message.answer(f"📊 Вы выбрали <b>{ticker}</b>. Что хотите сделать?", parse_mode="HTML", reply_markup=keyboard)
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
        volume = data.iloc[-1]["volume"]
        await callback_query.message.answer(
            f"💰 <b>{ticker}</b>\n"
            f"📈 Цена: {current_price} RUB\n"
            f"📊 Объём: {volume} акций",
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
async def cmd_price(message: Message):
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
    ))

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

@router.message(Command("all_stocks"))
async def cmd_all_stocks(message: Message, page: int = 1):
    logger.info(f"Получена команда /all_stocks от пользователя {message.from_user.id}, страница {page}")
    try:
        market = Market("stocks")
        stocks = market.tickers()
        if not stocks:
            await message.answer("Не удалось загрузить список акций с MOEX.")
            return

        # Пагинация: по 20 акций на страницу
        page_size = 20
        total_stocks = len(stocks)
        total_pages = (total_stocks + page_size - 1) // page_size
        start = (page - 1) * page_size
        end = min(start + page_size, total_stocks)
        stocks_page = stocks[start:end]

        response = f"📜 <b>Список всех акций на MOEX (Страница {page}/{total_pages})</b>:\n\n"
        for stock in stocks_page:
            ticker = stock['ticker']
            name = stock.get('shortname', ticker)
            response += f"🔹 {ticker}: {name}\n"

        # Кнопки пагинации
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        buttons = []
        if page > 1:
            buttons.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"all_stocks_{page-1}"))
        if page < total_pages:
            buttons.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"all_stocks_{page+1}"))
        if buttons:
            keyboard.inline_keyboard.append(buttons)
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_menu")])

        response += "\nДля проверки цены используйте /price [ticker] или выберите акцию в меню 📈"
        await message.answer(response, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при получении списка всех акций: {e}")
        await message.answer("Ошибка при загрузке списка акций.")

@router.callback_query(lambda c: c.data.startswith("all_stocks_"))
async def paginate_all_stocks(callback_query: CallbackQuery):
    page = int(callback_query.data.split("_")[2])
    await cmd_all_stocks(callback_query.message, page=page)
    await callback_query.answer()

@router.message(Text(startswith=["SBER", "GAZP", "LKOH", "YNDX", "ROSN", "TATN", "VTBR", "MGNT", "NVTK", "GMKN"]))
async def search_stock(message: Message):
    query = message.text.strip().upper()
    logger.info(f"Пользователь {message.from_user.id} выполнил поиск: {query}")
    try:
        market = Market("stocks")
        stocks = market.tickers()
        if not stocks:
            await message.answer("Не удалось загрузить список акций с MOEX.")
            return

        # Поиск по тикеру или имени
        results = [
            stock for stock in stocks
            if query in stock['ticker'].upper() or query in stock.get('shortname', '').upper()
        ]

        if not results:
            await message.answer(f"Акции по запросу '{query}' не найдены.")
            return

        response = f"🔎 <b>Результаты поиска для '{query}'</b>:\n\n"
        for stock in results[:10]:  # Ограничим до 10 результатов
            ticker = stock['ticker']
            name = stock.get('shortname', ticker)
            response += f"🔹 {ticker}: {name}\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_menu")]
        ])
        await message.answer(response, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при поиске акций: {e}")
        await message.answer("Ошибка при поиске акций.")