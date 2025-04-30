# app/handlers.py
from aiogram import Router, Bot
from aiogram.filters import Command  # Убираем RegexpFilter
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.models import Stock, Subscription, Signal, User
from sqlalchemy import select
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()

# Главное меню
def get_main_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Мои акции", callback_data="list_stocks")],
        [InlineKeyboardButton(text="📈 Все акции", callback_data="list_all_stocks")],
        [InlineKeyboardButton(text="🔍 Цена акции", callback_data="check_price")],
        [InlineKeyboardButton(text="🔔 Подписаться", callback_data="subscribe")],
        [InlineKeyboardButton(text="📊 Сигналы", callback_data="signals")],
        [InlineKeyboardButton(text="🔑 Установить токен", callback_data="set_token")],
        [InlineKeyboardButton(text="🤖 Автоторговля", callback_data="autotrading_menu")],
    ])
    return keyboard

# Меню автоторговли
def get_autotrading_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Мой профиль", callback_data="view_profile")],
        [InlineKeyboardButton(text="▶️ Включить автоторговлю", callback_data="enable_autotrading")],
        [InlineKeyboardButton(text="⏹️ Выключить автоторговлю", callback_data="disable_autotrading")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_menu")],
    ])
    return keyboard

@router.message(Command("start"))
async def cmd_start(message: Message):
    logger.info(f"Получена команда /start от пользователя {message.from_user.id}")
    welcome_text = (
        "🌟 <b>StockBot — Ваш помощник на MOEX!</b> 🌟\n\n"
        "Я помогу следить за акциями и торговать! 🚀\n"
        "Что я умею:\n"
        "📋 Показать ваши подписки\n"
        "📈 Показать все доступные акции\n"
        "🔍 Узнать цену акции\n"
        "🔔 Подписаться на уведомления\n"
        "📊 Показать сигналы роста\n"
        "🔑 Установить токен для автоторговли\n"
        "🤖 Настроить автоторговлю\n\n"
        "Выберите действие в меню 👇"
    )
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_menu())

@router.callback_query(lambda c: c.data == "list_stocks")
async def list_stocks(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил список своих акций")
    try:
        # Получаем тикеры, на которые подписан пользователь
        result = await session.execute(
            select(Subscription.ticker).where(Subscription.user_id == user_id)
        )
        subscribed_tickers = result.scalars().all()

        if not subscribed_tickers:
            await callback_query.message.answer("Вы не подписаны ни на одну акцию. Нажмите 'Подписаться', чтобы добавить акции.")
            return

        # Получаем акции, на которые подписан пользователь
        result = await session.execute(
            select(Stock).where(Stock.ticker.in_(subscribed_tickers))
        )
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

@router.callback_query(lambda c: c.data == "list_all_stocks")
async def list_all_stocks(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил список всех акций")
    try:
        # Получаем все акции из таблицы stocks
        result = await session.execute(select(Stock))
        stocks = result.scalars().all()

        if not stocks:
            await callback_query.message.answer("В базе нет доступных акций. Попробуйте позже.")
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
        await callback_query.message.answer("📈 <b>Все доступные акции:</b>", parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при получении всех акций: {e}")
        await callback_query.message.answer("Произошла ошибка при получении списка акций.")
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

@router.callback_query(lambda c: c.data == "set_token")
async def prompt_set_token(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет установить токен")
    await callback_query.message.answer("🔑 Введите ваш токен T-Invest API (должен начинаться с t_):")
    await callback_query.answer()

# Используем лямбда-функцию вместо RegexpFilter
@router.message(lambda message: message.text.startswith('t_'))
async def save_token(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    token = message.text.strip()
    logger.info(f"Пользователь {user_id} ввёл токен T-Invest API: {token[:10]}...")

    try:
        # Проверяем, есть ли пользователь в базе
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()

        if user:
            # Обновляем токен
            user.tinkoff_token = token
        else:
            # Создаём нового пользователя
            new_user = User(user_id=user_id, tinkoff_token=token)
            session.add(new_user)

        await session.commit()
        await message.answer("✅ Токен успешно сохранён! Теперь я могу торговать за вас.")
    except Exception as e:
        logger.error(f"Ошибка при сохранении токена для пользователя {user_id}: {e}")
        await message.answer("❌ Ошибка при сохранении токена. Попробуйте снова.")

@router.callback_query(lambda c: c.data == "autotrading_menu")
async def autotrading_menu(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} открыл меню автоторговли")
    await callback_query.message.answer("🤖 <b>Меню автоторговли:</b>", parse_mode="HTML", reply_markup=get_autotrading_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "view_profile")
async def view_profile(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил просмотр профиля")
    try:
        # Проверяем, есть ли пользователь в базе
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()

        if not user or not user.tinkoff_token:
            await callback_query.message.answer("🔑 У вас не установлен токен T-Invest API. Установите его через меню.")
            return

        # Получаем подписки пользователя
        result = await session.execute(
            select(Subscription.ticker).where(Subscription.user_id == user_id)
        )
        subscribed_tickers = result.scalars().all()

        profile_text = (
            f"📊 <b>Ваш профиль</b>\n\n"
            f"🆔 Ваш ID: {user_id}\n"
            f"🔑 Токен T-Invest API: {user.tinkoff_token[:10]}...\n"
            f"📋 Подписки: {', '.join(subscribed_tickers) if subscribed_tickers else 'Нет подписок'}\n"
        )
        await callback_query.message.answer(profile_text, parse_mode="HTML", reply_markup=get_autotrading_menu())
    except Exception as e:
        logger.error(f"Ошибка при просмотре профиля пользователя {user_id}: {e}")
        await callback_query.message.answer("❌ Ошибка при просмотре профиля.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "enable_autotrading")
async def enable_autotrading(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} включил автоторговлю")
    try:
        # Проверяем, есть ли пользователь в базе
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()

        if not user:
            await callback_query.message.answer("❌ Вы не зарегистрированы. Установите токен T-Invest API.")
            return

        user.autotrading_enabled = True
        await session.commit()
        await callback_query.message.answer("▶️ Автоторговля включена!", reply_markup=get_autotrading_menu())
    except Exception as e:
        logger.error(f"Ошибка при включении автоторговли для пользователя {user_id}: {e}")
        await callback_query.message.answer("❌ Ошибка при включении автоторговли.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "disable_autotrading")
async def disable_autotrading(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} выключил автоторговлю")
    try:
        # Проверяем, есть ли пользователь в базе
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()

        if not user:
            await callback_query.message.answer("❌ Вы не зарегистрированы. Установите токен T-Invest API.")
            return

        user.autotrading_enabled = False
        await session.commit()
        await callback_query.message.answer("⏹️ Автоторговля отключена!", reply_markup=get_autotrading_menu())
    except Exception as e:
        logger.error(f"Ошибка при отключении автоторговли для пользователя {user_id}: {e}")
        await callback_query.message.answer("❌ Ошибка при отключении автоторговли.")
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