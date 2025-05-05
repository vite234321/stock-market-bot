from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.models import Stock, Subscription, Signal, User, TradeHistory, FigiStatus
from sqlalchemy import select, func
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import os
import asyncio
import html
from typing import Optional
import aiohttp

# Проверка установки tinkoff-invest
try:
    import tinkoff
    from tinkoff.invest import AsyncClient, CandleInterval, InstrumentIdType, OrderDirection
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info(f"Модуль tinkoff-invest успешно импортирован в handlers.py, версия: {tinkoff.invest.__version__}")
except ImportError as e:
    logging.basicConfig(level=logging.ERROR)
    logger = logging.getLogger(__name__)
    logger.error("Ошибка импорта tinkoff.invest в handlers.py. Убедитесь, что tinkoff-invest установлен в requirements.txt.")
    raise ImportError("Ошибка импорта tinkoff.invest. Убедитесь, что tinkoff-invest установлен в requirements.txt.") from e
from tinkoff.invest.exceptions import InvestError

router = Router()

def get_main_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Акции", callback_data="stocks_menu")],
        [InlineKeyboardButton(text="🤖 Торговля", callback_data="trading_menu")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu")],
    ])
    return keyboard

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

def get_trading_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Автоторговля", callback_data="autotrading_menu"),
         InlineKeyboardButton(text="📜 История", callback_data="trade_history")],
        [InlineKeyboardButton(text="💰 Баланс", callback_data="balance"),
         InlineKeyboardButton(text="📅 Статистика", callback_data="daily_stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
    ])
    return keyboard

def get_settings_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Установить токен", callback_data="set_token")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
    ])
    return keyboard

def get_autotrading_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Профиль", callback_data="view_profile")],
        [InlineKeyboardButton(text="▶️ Включить", callback_data="enable_autotrading"),
         InlineKeyboardButton(text="⏹️ Выключить", callback_data="disable_autotrading")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_trading")],
    ])
    return keyboard

async def calculate_indicators(prices: list) -> tuple:
    if len(prices) < 20:
        return None, None, None, None, None

    # RSI (14 дней)
    gains = [max(0, prices[i] - prices[i-1]) for i in range(1, len(prices[-14:]))]
    losses = [max(0, prices[i-1] - prices[i]) for i in range(1, len(prices[-14:]))]
    avg_gain = sum(gains) / 14 if gains else 0
    avg_loss = sum(losses) / 14 if losses else 0
    rs = avg_gain / avg_loss if avg_loss else float('inf')
    rsi = 100 - (100 / (1 + rs)) if rs != float('inf') else 100

    # MACD (EMA 12, 26, Signal 9)
    ema_12 = sum(prices[-12:]) / 12
    ema_26 = sum(prices[-26:]) / 26 if len(prices) >= 26 else ema_12
    macd = ema_12 - ema_26
    signal = sum(prices[-9:]) / 9 if len(prices) >= 9 else macd
    histogram = macd - signal

    # Bollinger Bands (20 дней)
    sma = sum(prices[-20:]) / 20
    std = (sum((p - sma) ** 2 for p in prices[-20:]) / 20) ** 0.5
    upper_band = sma + 2 * std
    lower_band = sma - 2 * std

    return rsi, macd, signal, upper_band, lower_band

async def fetch_figi_with_retry(client: AsyncClient, ticker: str, max_retries: int = 3) -> Optional[str]:
    for attempt in range(max_retries):
        try:
            cleaned_ticker = ticker.replace(".ME", "")
            instrument = await client.instruments.share_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                class_code="TQBR",
                id=cleaned_ticker
            )
            return instrument.instrument.figi
        except InvestError as e:
            if "RESOURCE_EXHAUSTED" in str(e):
                reset_time = int(e.metadata.ratelimit_reset) if e.metadata.ratelimit_reset else 60
                logger.warning(f"Попытка {attempt + 1}/{max_retries}: Лимит запросов превышен, ожидание {reset_time} секунд...")
                await asyncio.sleep(reset_time)
            else:
                logger.error(f"Попытка {attempt + 1}/{max_retries}: Не удалось получить FIGI для {ticker}: {e}")
                break
        except Exception as e:
            logger.error(f"Попытка {attempt + 1}/{max_retries}: Неожиданная ошибка для {ticker}: {e}")
            break
    return None

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
            await callback_query.answer()
            return

        response = "📈 <b>Все доступные акции:</b>\n\n"
        for stock in stocks:
            # Проверяем figi_status, если None, то отображаем как "UNKNOWN"
            status = stock.figi_status if stock.figi_status else "UNKNOWN"
            status_icon = "✅" if status == "SUCCESS" else "⚠️" if status == "PENDING" else "❌"
            price = stock.last_price if stock.last_price is not None else "N/A"
            response += f"{status_icon} {stock.ticker} - {stock.name} | Цена: {price} RUB\n"

        response += "\n⬅️ Вернуться в меню акций."
        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении всех акций: {e}")
        await callback_query.message.answer("Произошла ошибка при получении списка акций.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "check_price")
async def prompt_check_price(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет проверить цену")
    await callback_query.message.answer("🔍 Введите тикер акции (например, SBER.ME):")
    await callback_query.answer()

@router.message(lambda message: message.text and message.text.endswith(".ME"))
async def check_price(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    ticker = message.text.strip()
    logger.info(f"Пользователь {user_id} запросил цену для {ticker}")

    try:
        user_result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = user_result.scalars().first()
        if not user or not user.tinkoff_token:
            await message.answer("🔑 У вас не установлен токен T-Invest API. Установите его в меню настроек.")
            return

        stock_result = await session.execute(
            select(Stock).where(Stock.ticker == ticker)
        )
        stock = stock_result.scalars().first()
        if not stock:
            await message.answer(f"Акция {ticker} не найдена в базе.")
            return

        async with AsyncClient(user.tinkoff_token) as client:
            figi = stock.figi
            if not figi:
                logger.warning(f"FIGI для {ticker} отсутствует в базе, пытаемся обновить...")
                figi = await fetch_figi_with_retry(client, ticker)
                if not figi:
                    await message.answer(f"Не удалось получить FIGI для {ticker}. Попробуйте позже.")
                    return
                stock.figi = figi
                stock.set_figi_status(FigiStatus.SUCCESS)
                session.add(stock)
                await session.commit()

            try:
                orderbook = await client.market_data.get_order_book(
                    figi=figi,
                    depth=1
                )
                if orderbook.bids and orderbook.bids[0].price:
                    price = orderbook.bids[0].price.units + orderbook.bids[0].price.nano / 1e9
                    stock.last_price = price
                    session.add(stock)
                    await session.commit()
                    await message.answer(f"📈 Текущая цена {ticker}: {price} RUB", reply_markup=get_stocks_menu())
                else:
                    await message.answer(f"Цена для {ticker} не доступна.")
            except InvestError as e:
                logger.error(f"Ошибка Tinkoff API при проверке цены для {ticker}: {e}")
                await message.answer(f"Ошибка API Tinkoff: {html.escape(str(e))}. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Ошибка при проверке цены для {ticker}: {e}")
        await message.answer(f"❌ Ошибка: {html.escape(str(e))}.")

@router.callback_query(lambda c: c.data == "price_chart")
async def prompt_price_chart(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет график цены")
    await callback_query.message.answer("📉 Введите тикер акции (например, SBER.ME) для построения графика:")
    await callback_query.answer()

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

        stock_result = await session.execute(
            select(Stock).where(Stock.ticker == ticker)
        )
        stock = stock_result.scalars().first()
        if not stock:
            await message.answer(f"Акция {ticker} не найдена в базе.")
            return

        async with AsyncClient(user.tinkoff_token) as client:
            figi = stock.figi
            if not figi:
                logger.warning(f"FIGI для {ticker} отсутствует в базе, пытаемся обновить...")
                figi = await fetch_figi_with_retry(client, ticker)
                if not figi:
                    await message.answer(f"Не удалось получить FIGI для {ticker}. Попробуйте позже.")
                    return
                stock.figi = figi
                stock.set_figi_status(FigiStatus.SUCCESS)
                session.add(stock)
                await session.commit()

            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=30)
            try:
                candles = await client.market_data.get_candles(
                    figi=figi,
                    from_=start_date,
                    to=end_date,
                    interval=CandleInterval.CANDLE_INTERVAL_DAY
                )
            except InvestError as e:
                logger.error(f"Ошибка Tinkoff API при получении свечей для {ticker}: {e}")
                await message.answer(f"Ошибка API Tinkoff: {html.escape(str(e))}. Попробуйте позже.")
                return

            if not candles.candles:
                await message.answer(f"Данные для {ticker} не найдены.")
                return

            # Проверяем, достаточно ли данных для построения графика
            if len(candles.candles) < 5:
                await message.answer(f"Недостаточно данных для построения графика {ticker} (найдено {len(candles.candles)} свечей, требуется минимум 5).")
                return

            dates = [candle.time for candle in candles.candles]
            prices = [candle.close.units + candle.close.nano / 1e9 for candle in candles.candles]

            plt.figure(figsize=(10, 5))
            plt.plot(dates, prices, marker='o', linestyle='-', color='b')
            plt.title(f"График цены {ticker} (30 дней)")
            plt.xlabel("Дата")
            plt.ylabel("Цена (RUB)")
            plt.grid(True)
            plt.xticks(rotation=45)
            plt.tight_layout()

            chart_path = f"chart_{user_id}_{ticker.replace('.ME', '')}.png"
            plt.savefig(chart_path)
            plt.close()

            try:
                chart_file = FSInputFile(chart_path)
                await message.answer_photo(chart_file, caption=f"📉 График цены для {ticker}", reply_markup=get_stocks_menu())
            finally:
                try:
                    os.remove(chart_path)
                    logger.info(f"Файл графика {chart_path} удалён")
                except Exception as e:
                    logger.warning(f"Не удалось удалить файл графика {chart_path}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при построении графика для {ticker}: {e}")
        await message.answer(f"❌ Ошибка при построении графика: {html.escape(str(e))}.")

@router.callback_query(lambda c: c.data == "subscribe")
async def prompt_subscribe(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет подписаться на акцию")
    await callback_query.message.answer("🔔 Введите тикер акции (например, SBER.ME) для подписки:")
    await callback_query.answer()

@router.message(lambda message: message.text and message.text.endswith(".ME") and not message.reply_to_message)
async def subscribe_to_stock(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    ticker = message.text.strip()
    logger.info(f"Пользователь {user_id} запросил подписку на {ticker}")

    try:
        user_result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = user_result.scalars().first()
        if not user:
            await message.answer("Пользователь не найден. Обратитесь к администратору.")
            return

        stock_result = await session.execute(
            select(Stock).where(Stock.ticker == ticker)
        )
        stock = stock_result.scalars().first()
        if not stock:
            await message.answer(f"Акция {ticker} не найдена в базе.")
            return

        result = await session.execute(
            select(Subscription).where(Subscription.user_id == user_id, Subscription.ticker == ticker)
        )
        if result.scalars().first():
            await message.answer(f"Вы уже подписаны на {ticker}.")
            return

        subscription = Subscription(user_id=user_id, ticker=ticker)
        session.add(subscription)
        await session.commit()
        await message.answer(f"✅ Вы подписаны на {ticker}!", reply_markup=get_stocks_menu())
    except Exception as e:
        logger.error(f"Ошибка при подписке на {ticker}: {e}")
        await message.answer(f"❌ Ошибка при подписке: {html.escape(str(e))}.")

@router.callback_query(lambda c: c.data == "signals")
async def list_signals(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил сигналы роста")

    try:
        result = await session.execute(
            select(Signal).where(Signal.user_id == user_id).order_by(Signal.created_at.desc()).limit(10)
        )
        signals = result.scalars().all()

        if not signals:
            await callback_query.message.answer("📊 Нет сигналов роста за последние 10 дней.")
            return

        response = "📊 <b>Сигналы роста:</b>\n\n"
        for signal in signals:
            response += f"🔹 {signal.ticker}: {signal.price} RUB ({signal.created_at.strftime('%Y-%m-%d %H:%M')})\n"
        response += "\n⬅️ Вернуться в меню акций."
        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении сигналов: {e}")
        await callback_query.message.answer("Произошла ошибка при получении сигналов.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "autotrading_menu")
async def autotrading_menu(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} открыл меню автоторговли")
    await callback_query.message.answer("🤖 <b>Меню автоторговли:</b>", parse_mode="HTML", reply_markup=get_autotrading_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "view_profile")
async def view_profile(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил профиль")
    try:
        user_result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = user_result.scalars().first()
        if not user:
            await callback_query.message.answer("Пользователь не найден.")
            return
        token_set = "Да" if user.tinkoff_token else "Нет"
        response = (
            f"📊 <b>Ваш профиль:</b>\n\n"
            f"ID: {user_id}\n"
            f"Токен установлен: {token_set}\n"
        )
        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_autotrading_menu())
    except Exception as e:
        logger.error(f"Ошибка при просмотре профиля: {e}")
        await callback_query.message.answer("Произошла ошибка при получении профиля.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "enable_autotrading")
async def enable_autotrading(callback_query: CallbackQuery, session: AsyncSession, bot: Bot):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил включение автоторговли")
    try:
        user_result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = user_result.scalars().first()
        if not user or not user.tinkoff_token:
            await callback_query.message.answer("🔑 Установите токен T-Invest API в меню настроек.")
            return
        from app.trading import TradingBot
        trading_bot = TradingBot(bot)
        if user_id in trading_bot.stream_tasks:
            await callback_query.message.answer("🤖 Автоторговля уже запущена.")
            return
        task = asyncio.create_task(trading_bot.stream_and_trade(user_id))
        trading_bot.stream_tasks[user_id] = task
        await callback_query.message.answer("🤖 Автоторговля запущена!", reply_markup=get_autotrading_menu())
    except Exception as e:
        logger.error(f"Ошибка при включении автоторговли: {e}")
        await callback_query.message.answer("❌ Ошибка при запуске автоторговли.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "disable_autotrading")
async def disable_autotrading(callback_query: CallbackQuery, bot: Bot):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил выключение автоторговли")
    from app.trading import TradingBot
    trading_bot = TradingBot(bot)
    trading_bot.stop_streaming(user_id)
    await callback_query.message.answer("⏹️ Автоторговля остановлена.", reply_markup=get_autotrading_menu())
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
            await callback_query.message.answer("📜 История торгов пуста.")
            return

        response = "📜 <b>История торгов:</b>\n\n"
        for trade in trades:
            action = "Покупка" if trade.action == "buy" else "Продажа"
            response += f"🔹 {trade.ticker}: {action} ({trade.price} RUB x {trade.quantity}) - {trade.total} RUB ({trade.created_at.strftime('%Y-%m-%d %H:%M')})\n"
        response += "\n⬅️ Вернуться в меню торговли."
        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_trading_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении истории торгов: {e}")
        await callback_query.message.answer("Произошла ошибка при получении истории.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "balance")
async def check_balance(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил баланс")
    try:
        user_result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = user_result.scalars().first()
        if not user or not user.tinkoff_token:
            await callback_query.message.answer("🔑 Установите токен T-Invest API в меню настроек.")
            return

        async with AsyncClient(user.tinkoff_token) as client:
            accounts = await client.users.get_accounts()
            if not accounts.accounts:
                await callback_query.message.answer("⚠️ Счёт не найден.")
                return
            account_id = accounts.accounts[0].id
            portfolio = await client.operations.get_portfolio(account_id=account_id)
            balance = portfolio.total_amount_currencies.units + portfolio.total_amount_currencies.nano / 1e9
            await callback_query.message.answer(f"💰 Ваш баланс: {balance:.2f} RUB", reply_markup=get_trading_menu())
    except Exception as e:
        logger.error(f"Ошибка при проверке баланса: {e}")
        await callback_query.message.answer("❌ Ошибка при получении баланса.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "daily_stats")
async def daily_stats(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил дневную статистику")
    try:
        from app.trading import TradingBot
        trading_bot = TradingBot(None)
        stats = await trading_bot.calculate_daily_profit(session, user_id)
        today = datetime.utcnow().date()
        response = (
            f"📅 <b>Дневная статистика ({today.strftime('%Y-%m-%d')}):</b>\n\n"
            f"🔄 Сделок: {stats['total_trades']}\n"
            f"📉 Покупок: {stats['total_buy']:.2f} RUB\n"
            f"📈 Продаж: {stats['total_sell']:.2f} RUB\n"
            f"📊 Прибыль: {stats['profit']:.2f} RUB\n"
            f"\n⬅️ Вернуться в меню торговли."
        )
        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_trading_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")
        await callback_query.message.answer("❌ Ошибка при получении статистики.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "set_token")
async def prompt_set_token(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет установить токен")
    await callback_query.message.answer("🔑 Введите ваш токен T-Invest API:")
    await callback_query.answer()

@router.message(lambda message: message.reply_to_message and message.reply_to_message.text == "🔑 Введите ваш токен T-Invest API:")
async def set_token(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    token = message.text.strip()
    logger.info(f"Пользователь {user_id} установил токен")
    try:
        user_result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = user_result.scalars().first()
        if not user:
            user = User(user_id=user_id, tinkoff_token=token)
            session.add(user)
        else:
            user.tinkoff_token = token
            session.add(user)
        await session.commit()
        await message.answer("✅ Токен успешно установлен!", reply_markup=get_settings_menu())
    except Exception as e:
        logger.error(f"Ошибка при установке токена: {e}")
        await message.answer("❌ Ошибка при установке токена.")