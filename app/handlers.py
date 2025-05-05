from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from app.models import Stock, Subscription, Signal, User, TradeHistory
from sqlalchemy import select, func
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import os
import asyncio
import html
from typing import Optional
import aiohttp

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

async def calculate_indicators(prices: list) -> tuple:
    if len(prices) < 20:
        return None, None, None, None, None
    gains = [max(0, prices[i] - prices[i-1]) for i in range(1, len(prices[-14:]))]
    losses = [max(0, prices[i-1] - prices[i]) for i in range(1, len(prices[-14:]))]
    avg_gain = sum(gains) / 14 if gains else 0
    avg_loss = sum(losses) / 14 if losses else 0
    rs = avg_gain / avg_loss if avg_loss else float('inf')
    rsi = 100 - (100 / (1 + rs)) if rs != float('inf') else 100
    sma = sum(prices[-20:]) / 20
    std = (sum((p - sma) ** 2 for p in prices[-20:]) / 20) ** 0.5
    upper_band = sma + 2 * std
    lower_band = sma - 2 * std
    return rsi, sma, upper_band, lower_band, None

async def fetch_moex_data(ticker: str) -> list:
    try:
        client = moexalgo.MoexClient()
        candles = client.get_candles(ticker, period="1d", limit=30)
        return [c['CLOSE'] for c in candles]
    except Exception as e:
        logger.error(f"Ошибка при получении данных MOEX для {ticker}: {e}")
        return []

@router.message(Command("start"))
async def cmd_start(message: Message):
    logger.info(f"Получена команда /start от пользователя {message.from_user.id}")
    welcome_text = (
        "🌟 <b>StockBot — Ваш помощник на MOEX!</b> 🌟\n\n"
        "Я помогу следить за акциями и торговать на Мосбирже! 🚀\n"
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
            price = stock.last_price if stock.last_price is not None else "N/A"
            response += f"🔹 {stock.ticker} - {stock.name} | Цена: {price} RUB\n"
        response += "\n⬅️ Вернуться в меню акций."
        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении всех акций: {e}")
        await callback_query.message.answer("Произошла ошибка при получении списка акций.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "check_price")
async def prompt_check_price(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет проверить цену акции")
    await callback_query.message.answer("🔍 Введите тикер акции (например, SBER):")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "price_chart")
async def prompt_price_chart(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет увидеть график цены акции")
    await callback_query.message.answer("📉 Введите тикер акции для построения графика (например, SBER):")
    await callback_query.answer()

@router.message(lambda message: message.text)
async def generate_price_chart(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    ticker = message.text.strip()
    logger.info(f"Пользователь {user_id} запросил график цены для {ticker}")

    try:
        prices = await fetch_moex_data(ticker)
        if not prices or len(prices) < 5:
            await message.answer(f"Недостаточно данных для построения графика {ticker}.")
            return

        dates = [datetime.utcnow() - timedelta(days=i) for i in range(len(prices)-1, -1, -1)]
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

        try:
            chart_file = FSInputFile(chart_path)
            await message.answer_photo(chart_file, caption=f"📉 График цены для {ticker}", reply_markup=get_stocks_menu())
        finally:
            if os.path.exists(chart_path):
                os.remove(chart_path)
                logger.info(f"Файл графика {chart_path} удалён")
    except Exception as e:
        logger.error(f"Ошибка при построении графика для {ticker}: {e}")
        await message.answer("❌ Ошибка при построении графика.")

@router.callback_query(lambda c: c.data == "subscribe")
async def prompt_subscribe(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет подписаться на акции")
    await callback_query.message.answer("🔔 Введите тикер акции для подписки (например, SBER):")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "signals")
async def signals(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил сигналы роста")
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

        user_result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = user_result.scalars().first()
        if not user or not user.moex_token:
            await callback_query.message.answer("🔑 У вас не установлен токен MOEX. Установите его в меню настроек.")
            return

        response = "📊 <b>Сигналы роста:</b>\n\n"
        for stock in stocks:
            ticker = stock.ticker
            prices = await fetch_moex_data(ticker)
            if len(prices) < 20:
                logger.warning(f"Недостаточно данных для {ticker}")
                continue
            rsi, sma, upper_band, lower_band, _ = await calculate_indicators(prices)
            current_price = prices[-1]

            if rsi is not None:
                signal_text = ""
                if rsi < 30 and current_price < lower_band:
                    signal_text = "📈 Сигнал на покупку: RSI < 30, цена ниже нижней Bollinger Band"
                elif rsi > 70 and current_price > upper_band:
                    signal_text = "📉 Сигнал на продажу: RSI > 70, цена выше верхней Bollinger Band"

                if signal_text:
                    response += f"🔹 {ticker} ({stock.name})\n"
                    response += f"💰 Цена: {current_price:.2f} RUB\n"
                    response += f"📊 {signal_text}\n"
                    response += f"📈 RSI: {rsi:.2f}\n\n"

        if not response.strip().endswith("📊 <b>Сигналы роста:</b>\n\n"):
            response += "🚫 Нет актуальных сигналов на текущий момент.\n\n"

        response += "⬅️ Вернуться в меню акций."
        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении сигналов: {e}")
        await callback_query.message.answer("Произошла ошибка при получении сигналов.")
    await callback_query.answer()

@router.message(lambda message: message.text.startswith('m.'))
async def save_token(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    token = message.text.strip()
    logger.info(f"Пользователь {user_id} ввёл токен MOEX: {token[:10]}...")

    try:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()

        if user:
            user.moex_token = token
        else:
            new_user = User(user_id=user_id, moex_token=token)
            session.add(new_user)

        await session.commit()
        await message.answer("✅ Токен успешно сохранён! Теперь я могу торговать на MOEX.", reply_markup=get_settings_menu())
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

        if not user or not user.moex_token:
            await callback_query.message.answer("🔑 У вас не установлен токен MOEX. Установите его в меню настроек.")
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

        profile_text = (
            f"📊 <b>Ваш профиль</b>\n\n"
            f"🆔 Ваш ID: {user_id}\n"
            f"🔑 Токен MOEX: {user.moex_token[:10]}...\n"
            f"📋 Подписки: {', '.join(subscribed_tickers) if subscribed_tickers else 'Нет подписок'}\n"
            f"🤖 Статус автоторговли: {'Активна' if user.autotrading_enabled else 'Отключена'}\n"
            f"💰 Текущий баланс: N/A (интеграция баланса в разработке)\n"
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
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()
        if not user:
            await callback_query.message.answer(
                "❌ Вы не зарегистрированы. Установите токен MOEX в меню настроек.",
                reply_markup=get_autotrading_menu()
            )
            return

        if not user.moex_token:
            await callback_query.message.answer(
                "❌ Токен MOEX не установлен. Установите его в меню настроек.",
                reply_markup=get_autotrading_menu()
            )
            return

        if user.autotrading_enabled:
            await callback_query.message.answer(
                "⚠️ Автоторговля уже включена!",
                reply_markup=get_autotrading_menu()
            )
            return

        stocks_result = await session.execute(select(Stock))
        stocks = stocks_result.scalars().all()
        if not stocks:
            await callback_query.message.answer(
                "❌ Нет доступных акций для торговли. Обратитесь к администратору или добавьте тикеры.",
                reply_markup=get_autotrading_menu()
            )
            return

        user.autotrading_enabled = True
        await session.commit()

        trading_bot.stop_streaming(user_id)
        task = asyncio.create_task(trading_bot.stream_and_trade(user_id))
        trading_bot.stream_tasks[user_id] = task

        await callback_query.message.answer(
            "▶️ Автоторговля включена!",
            reply_markup=get_autotrading_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка при включении автоторговли для пользователя {user_id}: {str(e)}")
        await callback_query.message.answer(
            "❌ Ошибка при включении автоторговли.",
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
            await callback_query.message.answer("❌ Вы не зарегистрированы. Установите токен MOEX в меню настроек.")
            return

        user.autotrading_enabled = False
        await session.commit()

        trading_bot.stop_streaming(user_id)

        await callback_query.message.answer("⏹️ Автоторговля отключена!", reply_markup=get_autotrading_menu())
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
        if not user or not user.moex_token:
            await callback_query.message.answer("🔑 У вас не установлен токен MOEX. Установите его в меню настроек.")
            return

        # Простая заглушка, так как баланс требует интеграции с торговой системой MOEX
        await callback_query.message.answer(
            "💰 Ваш текущий баланс: N/A (интеграция в разработке)",
            reply_markup=get_trading_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка при получении баланса для пользователя {user_id}: {e}")
        await callback_query.message.answer("❌ Ошибка при получении баланса.")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "daily_stats")
async def daily_stats(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил дневную статистику")
    try:
        trading_bot = TradingBot(None)
        stats = await trading_bot.calculate_daily_profit(session, user_id)
        today = datetime.utcnow().date()
        response = (
            f"📅 <b>Дневная статистика ({today.strftime('%Y-%m-%d')}):</b>\n\n"
            f"🔄 Сделок: {stats['total_trades']}\n"
            f"📉 Покупки: {stats['total_buy']:.2f} RUB\n"
            f"📈 Продажи: {stats['total_sell']:.2f} RUB\n"
            f"📊 Прибыль: {stats['profit']:.2f} RUB\n"
            f"\n⬅️ Вернуться в меню торговли."
        )
        await callback_query.message.answer(response, parse_mode="HTML", reply_markup=get_trading_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")
        await callback_query.message.answer("❌ Ошибка при получении статистики.")
    await callback_query.answer()