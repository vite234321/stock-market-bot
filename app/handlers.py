from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.models import Stock, Subscription, Signal, User, TradeHistory
from sqlalchemy import select, func
from datetime import datetime, timedelta
try:
    from tinkoff.invest import AsyncClient, CandleInterval, InstrumentIdType
except ImportError as e:
    raise ImportError("Ошибка импорта tinkoff.invest. Убедитесь, что tinkoff-invest-api установлен в requirements.txt.") from e
from tinkoff.invest.exceptions import InvestError
import matplotlib.pyplot as plt
import os
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    try:
        response = await client.instruments.share_by(
            id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
            class_code="TQBR",
            id=stock.ticker
        )
        stock.figi = response.instrument.figi
        session.add(stock)
        await session.commit()
        logger.info(f"FIGI для {stock.ticker} обновлён: {stock.figi}")
        return stock.figi
    except InvestError as e:
        if "RESOURCE_EXHAUSTED" in str(e):
            reset_time = int(e.metadata.ratelimit_reset) if e.metadata.ratelimit_reset else 60
            logger.warning(f"Достигнут лимит запросов API, ожидание {reset_time} секунд...")
            await asyncio.sleep(reset_time)
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
                figi = await update_figi(client, stock, session)
                if not figi:
                    await message.answer(f"Не удалось получить FIGI для {ticker}. Попробуйте позже.")
                    return

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

            chart_path = f"chart_{user_id}_{ticker}.png"
            plt.savefig(chart_path)
            plt.close()

            chart_file = FSInputFile(chart_path)
            await message.answer_photo(chart_file, caption=f"📉 График цены для {ticker}", reply_markup=get_stocks_menu())

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
    logger.info(f"Пользователь {user_id} пытается включить автоторговлю")
    try:
        # Проверка пользователя
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()
        if not user:
            await callback_query.message.answer(
                "❌ Вы не зарегистрированы. Установите токен T-Invest API в меню настроек.",
                reply_markup=get_autotrading_menu()
            )
            return

        # Проверка токена
        if not user.tinkoff_token:
            await callback_query.message.answer(
                "❌ Токен T-Invest API не установлен. Установите его в меню настроек.",
                reply_markup=get_autotrading_menu()
            )
            return

        # Проверка статуса автоторговли
        if user.autotrading_enabled:
            await callback_query.message.answer(
                "⚠️ Автоторговля уже включена!",
                reply_markup=get_autotrading_menu()
            )
            return

        # Проверка наличия доступных акций
        stocks_result = await session.execute(
            select(Stock).where(Stock.figi_status == 'SUCCESS')
        )
        stocks = stocks_result.scalars().all()
        if not stocks:
            await callback_query.message.answer(
                "❌ Нет доступных акций для торговли. Обратитесь к администратору или добавьте тикеры.",
                reply_markup=get_autotrading_menu()
            )
            return

        # Включение автоторговли
        user.autotrading_enabled = True
        await session.commit()

        # Остановка существующих задач стриминга
        trading_bot.stop_streaming(user_id)

        # Запуск стриминга
        task = asyncio.create_task(trading_bot.stream_and_trade(user_id))
        trading_bot.stream_tasks[user_id] = task

        await callback_query.message.answer(
            "▶️ Автоторговля включена!",
            reply_markup=get_autotrading_menu()
        )
        await callback_query.message.answer(
            "🤖 Бот начал анализ рынка и поиск возможностей для торговли."
        )
    except Exception as e:
        logger.error(f"Ошибка при включении автоторговли для пользователя {user_id}: {str(e)}")
        error_message = "❌ Ошибка при включении автоторговли: "
        if "Нет подходящих тикеров" in str(e):
            error_message += "Нет подходящих акций для торговли. Попробуйте добавить другие тикеры или проверьте настройки."
        elif "Токен T-Invest API не найден" in str(e):
            error_message += "Токен T-Invest API не установлен."
        elif "Instrument not found" in str(e):
            error_message += "Некоторые тикеры недоступны. Проверьте базу акций."
        elif "Недостаточно данных для обучения ML" in str(e):
            error_message += "Недостаточно данных для обучения модели. Попробуйте позже."
        else:
            error_message += f"Неизвестная ошибка: {str(e)}. Попробуйте снова позже."
        await callback_query.message.answer(
            error_message,
            reply_markup=get_autotrading_menu()
        )
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

        trading_bot.stop_streaming(user_id)

        await callback_query.message.answer("⏹️ Автоторговля отключена!", reply_markup=get_autotrading_menu())
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

        await callback_query.message.answer(
            f"💰 Ваш текущий баланс: {total_balance:.2f} RUB",
            reply_markup=get_trading_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка при получении баланса для пользователя {user_id}: {e}")
        await callback_query.message.answer("❌ Ошибка при получении баланса.")
    await callback_query.answer()