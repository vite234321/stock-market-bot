# app/handlers.py
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.models import Stock, Subscription, Signal, User, TradeHistory
from sqlalchemy import select, func
from datetime import datetime, timedelta
from tinkoff.invest import AsyncClient, CandleInterval, InstrumentIdType
from tinkoff.invest.exceptions import InvestError  # Заменяем TinkoffInvestError на InvestError
import matplotlib.pyplot as plt
import os
import asyncio

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()

# Главное меню
def get_main_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Акции", callback_data="stocks_menu")],
        [InlineKeyboardButton(text="🤖 Торговля", callback_data="trading_menu")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu")],
    ])
    return keyboard

# Меню акций
def get_stocks_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Мои акции", callback_data="list_stocks"),
         InlineKeyboardButton(text="📈 Все акции", callback_data="list_all_stocks")],
        [InlineKeyboardButton(text="🔍 Проверить цену", callback_data="check_price"),
         InlineKeyboardButton(text="📉 График цены", callback_data="price_chart")],
        [InlineKeyboardButton(text="🔔 Подписаться", callback_data="subscribe"),
         InlineKeyboardButton(text="📊 Сигналы роста", callback_data="signals")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
    ])
    return keyboard

# Меню торговли
def get_trading_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Автоторговля", callback_data="autotrading_menu"),
         InlineKeyboardButton(text="📜 История", callback_data="trade_history")],
        [InlineKeyboardButton(text="💰 Баланс", callback_data="balance"),
         InlineKeyboardButton(text="📅 Статистика", callback_data="daily_stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
    ])
    return keyboard

# Меню настроек
def get_settings_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Установить токен", callback_data="set_token")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
    ])
    return keyboard

# Меню автоторговли
def get_autotrading_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Профиль", callback_data="view_profile")],
        [InlineKeyboardButton(text="▶️ Включить", callback_data="enable_autotrading"),
         InlineKeyboardButton(text="⏹️ Выключить", callback_data="disable_autotrading")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_trading")],
    ])
    return keyboard

@router.message(Command("start"))
async def cmd_start(message: Message):
    logger.info(f"Получена команда /start от пользователя {message.from_user.id}")
    welcome_text = (
        "🌟 <b>StockBot — Ваш помощник на MOEX!</b> 🌟\n\n"
        "Я помогу следить за акциями и торговать! 🚀\n"
        "Выберите раздел в меню ниже 👇"
    )
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_menu())

@router.callback_query(lambda c: c.data == "stocks_menu")
async def stocks_menu(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} открыл меню акций")
    await callback_query.message.answer("📈 <b>Меню акций:</b>", parse_mode="HTML", reply_markup=get_stocks_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "trading_menu")
async def trading_menu(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} открыл меню торговли")
    await callback_query.message.answer("🤖 <b>Меню торговли:</b>", parse_mode="HTML", reply_markup=get_trading_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "settings_menu")
async def settings_menu(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} открыл меню настроек")
    await callback_query.message.answer("⚙️ <b>Меню настроек:</b>", parse_mode="HTML", reply_markup=get_settings_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} вернулся в главное меню")
    await callback_query.message.answer("🌟 Выберите раздел:", reply_markup=get_main_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "back_to_trading")
async def back_to_trading(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} вернулся в меню торговли")
    await callback_query.message.answer("🤖 <b>Меню торговли:</b>", parse_mode="HTML", reply_markup=get_trading_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "list_stocks")
async def list_stocks(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил список своих акций")
    try:
        result = await session.execute(
            select(Subscription.ticker).where(Subscription.user_id == user_id)
        )
        subscribed_tickers = result.scalars().all()

        if not subscribed_tickers:
            await callback_query.message.answer("Вы не подписаны ни на одну акцию. Нажмите 'Подписаться' в меню акций.")
            return

        result = await session.execute(
            select(Stock).where(Stock.ticker.in_(subscribed_tickers))
        )
        stocks = result.scalars().all()

        if not stocks:
            await callback_query.message.answer("Акции не найдены. Попробуйте позже.")
            return

        response = "📋 <b>Ваши акции:</b>\n\n"
        for stock in stocks:
            price = stock.last_price if stock.last_price is not None else "N/A"
            response += f"🔹 {stock.ticker}: {stock.name} ({price} RUB)\n"
        response += "\n⬅️ Вернуться в меню акций."
        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении акций: {e}")
        await callback_query.message.answer("Произошла ошибка при получении акций.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "list_all_stocks")
async def list_all_stocks(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил список всех акций")
    try:
        result = await session.execute(select(Stock))
        stocks = result.scalars().all()

        if not stocks:
            await callback_query.message.answer("В базе нет доступных акций. Попробуйте позже.")
            return

        response = "📈 <b>Все доступные акции:</b>\n\n"
        for stock in stocks:
            price = stock.last_price if stock.last_price is not None else "N/A"
            response += f"🔹 {stock.ticker}: {stock.name} ({price} RUB)\n"
        response += "\n⬅️ Вернуться в меню акций."
        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении всех акций: {e}")
        await callback_query.message.answer("Произошла ошибка при получении списка акций.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "check_price")
async def prompt_check_price(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет проверить цену акции")
    await callback_query.message.answer("🔍 Введите тикер акции (например, SBER.ME):")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "price_chart")
async def prompt_price_chart(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет увидеть график цены акции")
    await callback_query.message.answer("📉 Введите тикер акции для построения графика (например, SBER.ME):")
    await callback_query.answer()

async def update_figi(client: AsyncClient, stock: Stock, session: AsyncSession):
    """Обновляет FIGI для акции через Tinkoff API, если его нет в базе."""
    try:
        response = await client.instruments.share_by(
            id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
            class_code="TQBR",  # Код рынка для акций MOEX
            id=stock.ticker
        )
        stock.figi = response.instrument.figi
        session.add(stock)
        await session.commit()
        logger.info(f"FIGI для {stock.ticker} обновлён: {stock.figi}")
        return stock.figi
    except InvestError as e:  # Заменяем TinkoffInvestError на InvestError
        if "RESOURCE_EXHAUSTED" in str(e):
            reset_time = int(e.metadata.ratelimit_reset) if e.metadata.ratelimit_reset else 60
            logger.warning(f"Достигнут лимит запросов API, ожидание {reset_time} секунд...")
            await asyncio.sleep(reset_time)
            # Повторяем запрос после ожидания
            response = await client.instruments.share_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                class_code="TQBR",
                id=stock.ticker
            )
            stock.figi = response.instrument.figi
            session.add(stock)
            await session.commit()
            logger.info(f"FIGI для {stock.ticker} обновлён после ожидания: {stock.figi}")
            return stock.figi
        else:
            logger.error(f"Не удалось обновить FIGI для {stock.ticker}: {e}")
            return None
    except Exception as e:
        logger.error(f"Не удалось обновить FIGI для {stock.ticker}: {e}")
        return None

@router.message(lambda message: message.text.endswith(".ME"))
async def generate_price_chart(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    ticker = message.text.strip()
    logger.info(f"Пользователь {user_id} запросил график цены для {ticker}")

    try:
        user_result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = user_result.scalars().first()
        if not user or not user.tinkoff_token:
            await message.answer("🔑 У вас не установлен токен T-Invest API. Установите его в меню настроек.")
            return

        # Находим акцию в базе
        stock_result = await session.execute(
            select(Stock).where(Stock.ticker == ticker)
        )
        stock = stock_result.scalars().first()
        if not stock:
            await message.answer(f"Акция {ticker} не найдена в базе.")
            return

        async with AsyncClient(user.tinkoff_token) as client:
            # Проверяем наличие FIGI
            figi = stock.figi
            if not figi:
                logger.warning(f"FIGI для {ticker} отсутствует в базе, пытаемся обновить...")
                figi = await update_figi(client, stock, session)
                if not figi:
                    await message.answer(f"Не удалось получить FIGI для {ticker}. Попробуйте позже.")
                    return

            # Получаем свечи за последние 30 дней
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=30)
            candles = await client.market_data.get_candles(
                figi=figi,
                from_=start_date,
                to=end_date,
                interval=CandleInterval.CANDLE_INTERVAL_DAY
            )

            if not candles.candles:
                await message.answer(f"Данные для {ticker} не найдены.")
                return

            # Извлекаем данные для графика
            dates = [candle.time for candle in candles.candles]
            prices = [candle.close.units + candle.close.nano / 1e9 for candle in candles.candles]

            # Генерируем график
            plt.figure(figsize=(10, 5))
            plt.plot(dates, prices, marker='o', linestyle='-', color='b')
            plt.title(f"График цены {ticker} (30 дней)")
            plt.xlabel("Дата")
            plt.ylabel("Цена (RUB)")
            plt.grid(True)
            plt.xticks(rotation=45)
            plt.tight_layout()

            # Сохраняем график во временный файл
            chart_path = f"chart_{user_id}_{ticker}.png"
            plt.savefig(chart_path)
            plt.close()

            # Отправляем график в Telegram
            chart_file = FSInputFile(chart_path)
            await message.answer_photo(chart_file, caption=f"📉 График цены для {ticker}", reply_markup=get_stocks_menu())

            # Удаляем временный файл
            os.remove(chart_path)
    except Exception as e:
        logger.error(f"Ошибка при построении графика для {ticker}: {e}")
        await message.answer("❌ Ошибка при построении графика.")

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
    await callback_query.message.answer("🔑 Введите ваш токен T-Invest API (должен начинаться с t.):")
    await callback_query.answer()

@router.message(lambda message: message.text.startswith('t.'))
async def save_token(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    token = message.text.strip()
    logger.info(f"Пользователь {user_id} ввёл токен T-Invest API: {token[:10]}...")

    try:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()

        if user:
            user.tinkoff_token = token
        else:
            new_user = User(user_id=user_id, tinkoff_token=token)
            session.add(new_user)

        await session.commit()
        await message.answer("✅ Токен успешно сохранён! Теперь я могу торговать за вас.", reply_markup=get_settings_menu())
    except Exception as e:
        logger.error(f"Ошибка при сохранении токена для пользователя {user_id}: {e}")
        await message.answer("❌ Ошибка при сохранении токена. Попробуйте снова.", reply_markup=get_settings_menu())

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
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()

        if not user or not user.tinkoff_token:
            await callback_query.message.answer("🔑 У вас не установлен токен T-Invest API. Установите его в меню настроек.")
            return

        result = await session.execute(
            select(Subscription.ticker).where(Subscription.user_id == user_id)
        )
        subscribed_tickers = result.scalars().all()

        # Получаем статистику
        total_trades_result = await session.execute(
            select(func.count(TradeHistory.id)).where(TradeHistory.user_id == user_id)
        )
        total_trades = total_trades_result.scalar()

        total_buy_result = await session.execute(
            select(func.sum(TradeHistory.total)).where(TradeHistory.user_id == user_id, TradeHistory.action == "buy")
        )
        total_buy = total_buy_result.scalar() or 0

        total_sell_result = await session.execute(
            select(func.sum(TradeHistory.total)).where(TradeHistory.user_id == user_id, TradeHistory.action == "sell")
        )
        total_sell = total_sell_result.scalar() or 0

        profit = total_sell - total_buy

        # Получаем баланс через T-Invest API
        async with AsyncClient(user.tinkoff_token) as client:
            accounts = await client.users.get_accounts()
            if not accounts.accounts:
                await callback_query.message.answer("❌ Счета не найдены. Проверьте токен T-Invest API.")
                return
            account_id = accounts.accounts[0].id

            portfolio = await client.operations.get_portfolio(account_id=account_id)
            total_balance = portfolio.total_amount_currencies.units + portfolio.total_amount_currencies.nano / 1e9

        profile_text = (
            f"📊 <b>Ваш профиль</b>\n\n"
            f"🆔 Ваш ID: {user_id}\n"
            f"🔑 Токен T-Invest API: {user.tinkoff_token[:10]}...\n"
            f"📋 Подписки: {', '.join(subscribed_tickers) if subscribed_tickers else 'Нет подписок'}\n"
            f"🤖 Статус автоторговли: {'Активна' if user.autotrading_enabled else 'Отключена'}\n"
            f"💰 Текущий баланс: {total_balance:.2f} RUB\n"
            f"🔄 Всего сделок: {total_trades}\n"
            f"📉 Покупки: {total_buy:.2f} RUB\n"
            f"📈 Продажи: {total_sell:.2f} RUB\n"
            f"📊 Прибыль: {profit:.2f} RUB\n"
        )
        await callback_query.message.answer(profile_text, parse_mode="HTML", reply_markup=get_autotrading_menu())
    except Exception as e:
        logger.error(f"Ошибка при просмотре профиля пользователя {user_id}: {e}")
        await callback_query.message.answer("❌ Ошибка при просмотре профиля.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "enable_autotrading")
async def enable_autotrading(callback_query: CallbackQuery, session: AsyncSession, trading_bot):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} включил автоторговлю")
    try:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()

        if not user:
            await callback_query.message.answer("❌ Вы не зарегистрированы. Установите токен T-Invest API в меню настроек.")
            return

        if user.autotrading_enabled:
            await callback_query.message.answer("⚠️ Автоторговля уже включена!", reply_markup=get_autotrading_menu())
            return

        user.autotrading_enabled = True
        await session.commit()

        # Останавливаем существующий стрим, если он есть
        trading_bot.stop_streaming(user_id)

        # Запускаем новый стрим
        task = asyncio.create_task(trading_bot.stream_and_trade(user_id))
        trading_bot.stream_tasks[user_id] = task

        await callback_query.message.answer("▶️ Автоторговля включена!", reply_markup=get_autotrading_menu())
        # Отправляем уведомление
        await callback_query.message.answer("🤖 Бот начал анализ рынка и поиск возможностей для торговли.")
    except Exception as e:
        logger.error(f"Ошибка при включении автоторговли для пользователя {user_id}: {e}")
        await callback_query.message.answer("❌ Ошибка при включении автоторговли.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "disable_autotrading")
async def disable_autotrading(callback_query: CallbackQuery, session: AsyncSession, trading_bot):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} выключил автоторговлю")
    try:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()

        if not user:
            await callback_query.message.answer("❌ Вы не зарегистрированы. Установите токен T-Invest API в меню настроек.")
            return

        user.autotrading_enabled = False
        await session.commit()

        # Останавливаем стрим для пользователя
        trading_bot.stop_streaming(user_id)

        await callback_query.message.answer("⏹️ Автоторговля отключена!", reply_markup=get_autotrading_menu())
        # Отправляем уведомление
        await callback_query.message.answer("🤖 Бот прекратил торговлю.")
    except Exception as e:
        logger.error(f"Ошибка при отключении автоторговли для пользователя {user_id}: {e}")
        await callback_query.message.answer("❌ Ошибка при отключении автоторговли.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "trade_history")
async def trade_history(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил историю торгов")
    try:
        result = await session.execute(
            select(TradeHistory).where(TradeHistory.user_id == user_id).order_by(TradeHistory.created_at.desc()).limit(10)
        )
        trades = result.scalars().all()

        if not trades:
            await callback_query.message.answer("📜 У вас пока нет истории торгов.")
            return

        response = "📜 <b>История торгов (последние 10):</b>\n\n"
        for trade in trades:
            action = "Покупка" if trade.action == "buy" else "Продажа"
            response += f"🕒 {trade.created_at.strftime('%Y-%m-%d %H:%M:%S')} | {action} | {trade.ticker} | {trade.quantity} акций | {trade.price} RUB | Итог: {trade.total} RUB\n"
        response += "\n⬅️ Вернуться в меню торговли."
        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_trading_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении истории торгов: {e}")
        await callback_query.message.answer("❌ Ошибка при получении истории торгов.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "balance")
async def balance(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил баланс")
    try:
        user_result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = user_result.scalars().first()
        if not user or not user.tinkoff_token:
            await callback_query.message.answer("🔑 У вас не установлен токен T-Invest API. Установите его в меню настроек.")
            return

        async with AsyncClient(user.tinkoff_token) as client:
            accounts = await client.users.get_accounts()
            if not accounts.accounts:
                await callback_query.message.answer("❌ Счета не найдены. Проверьте токен T-Invest API.")
                return
            account_id = accounts.accounts[0].id

            portfolio = await client.operations.get_portfolio(account_id=account_id)
            total_balance = portfolio.total_amount_currencies.units + portfolio.total_amount_currencies.nano / 1e9

            response = f"💰 <b>Ваш баланс:</b> {total_balance:.2f} RUB\n"
            response += f"🕒 Обновлено: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n"
            response += "\n⬅️ Вернуться в меню торговли."
            await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_trading_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении баланса: {e}")
        await callback_query.message.answer("❌ Ошибка при получении баланса.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "daily_stats")
async def daily_stats(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил дневную статистику")
    try:
        today = datetime.utcnow().date()
        start_of_day = datetime.combine(today, datetime.min.time())
        end_of_day = datetime.combine(today, datetime.max.time())

        result = await session.execute(
            select(TradeHistory).where(
                TradeHistory.user_id == user_id,
                TradeHistory.created_at >= start_of_day,
                TradeHistory.created_at <= end_of_day
            )
        )
        trades = result.scalars().all()

        if not trades:
            await callback_query.message.answer("📅 Сегодня не было торгов.")
            return

        total_trades = len(trades)
        total_buy = sum(trade.total for trade in trades if trade.action == "buy")
        total_sell = sum(trade.total for trade in trades if trade.action == "sell")
        profit = total_sell - total_buy

        response = (
            f"📅 <b>Дневная статистика ({today.strftime('%Y-%m-%d')}):</b>\n\n"
            f"🔄 Всего сделок: {total_trades}\n"
            f"📉 Покупки: {total_buy:.2f} RUB\n"
            f"📈 Продажи: {total_sell:.2f} RUB\n"
            f"📊 Прибыль: {profit:.2f} RUB\n"
            f"\n⬅️ Вернуться в меню торговли."
        )
        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_trading_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении дневной статистики: {e}")
        await callback_query.message.answer("❌ Ошибка при получении дневной статистики.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith("stock_"))
async def process_stock_selection(callback_query: CallbackQuery, session: AsyncSession):
    ticker = callback_query.data.split("_")[1]
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} выбрал акцию {ticker}")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Цена", callback_data=f"price_{ticker}")],
        [InlineKeyboardButton(text="🔔 Подписаться", callback_data=f"subscribe_{ticker}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="stocks_menu")]
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
            parse_mode="HTML",
            reply_markup=get_stocks_menu()
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
        await callback_query.message.answer(f"🔔 Вы успешно подписались на <b>{ticker}</b>!", parse_mode="HTML", reply_markup=get_stocks_menu())
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
        await message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении сигналов для {ticker}: {e}")
        await message.answer(f"Ошибка при получении{ticker}: {e}")