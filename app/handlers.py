from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
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
from tinkoff.invest import AsyncClient, CandleInterval, InstrumentIdType, OrderDirection
from tinkoff.invest.exceptions import InvestError

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.handlers = [h for h in logger.handlers if not isinstance(h, logging.StreamHandler)]

router = Router()

# Определяем состояния для FSM
class CheckPriceState(StatesGroup):
    waiting_for_ticker = State()

class PriceChartState(StatesGroup):
    waiting_for_ticker = State()

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
         InlineKeyboardButton(text="📈 Все акции", callback_data="list_all_stocks_0")],
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

async def update_figi(client: AsyncClient, stock: Stock, session: AsyncSession) -> Optional[str]:
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
    await callback_query.message.edit_text("📈 <b>Меню акций:</b>", parse_mode="HTML", reply_markup=get_stocks_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "trading_menu")
async def trading_menu(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} открыл меню торговли")
    await callback_query.message.edit_text("🤖 <b>Меню торговли:</b>", parse_mode="HTML", reply_markup=get_trading_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "settings_menu")
async def settings_menu(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} открыл меню настроек")
    await callback_query.message.edit_text("⚙️ <b>Меню настроек:</b>", parse_mode="HTML", reply_markup=get_settings_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} вернулся в главное меню")
    await callback_query.message.edit_text("🌟 Выберите раздел:", parse_mode="HTML", reply_markup=get_main_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "back_to_trading")
async def back_to_trading(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} вернулся в меню торговли")
    await callback_query.message.edit_text("🤖 <b>Меню торговли:</b>", parse_mode="HTML", reply_markup=get_trading_menu())
    await callback_query.answer()

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
            await callback_query.message.edit_text(
                "Вы не подписаны ни на одну акцию. Нажмите 'Подписаться' в меню акций.",
                parse_mode="HTML",
                reply_markup=get_stocks_menu()
            )
            return

        # Получаем информацию об акциях
        result = await session.execute(
            select(Stock).where(Stock.ticker.in_(subscribed_tickers))
        )
        stocks = result.scalars().all()

        if not stocks:
            await callback_query.message.edit_text(
                "Акции не найдены. Попробуйте позже.",
                parse_mode="HTML",
                reply_markup=get_stocks_menu()
            )
            return

        # Формируем ответ
        response = "📋 <b>Ваши акции:</b>\n\n"
        for stock in stocks:
            price = stock.last_price if stock.last_price is not None else "N/A"
            response += f"🔹 {stock.ticker}: {stock.name} ({price} RUB)\n"
        response += "\n⬅️ Вернуться в меню акций."
        await callback_query.message.edit_text(response, parse_mode="HTML", reply_markup=get_stocks_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении акций: {e}")
        await callback_query.message.edit_text(
            "Произошла ошибка при получении акций.",
            parse_mode="HTML",
            reply_markup=get_stocks_menu()
        )
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith("list_all_stocks_"))
async def list_all_stocks(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил список всех акций")
    
    try:
        # Извлекаем номер страницы из callback_data
        page = int(callback_query.data.split("_")[-1])
        items_per_page = 20  # Количество акций на одной странице

        # Получаем общее количество акций
        result = await session.execute(select(func.count()).select_from(Stock))
        total_stocks = result.scalar()
        total_pages = (total_stocks + items_per_page - 1) // items_per_page  # Округление вверх

        if total_stocks == 0:
            await callback_query.message.edit_text(
                "В базе нет доступных акций. Попробуйте позже.",
                parse_mode="HTML",
                reply_markup=get_stocks_menu()
            )
            await callback_query.answer()
            return

        # Проверяем, что страница в допустимом диапазоне
        if page < 0:
            page = 0
        if page >= total_pages:
            page = total_pages - 1

        # Получаем акции для текущей страницы
        result = await session.execute(
            select(Stock).offset(page * items_per_page).limit(items_per_page)
        )
        stocks = result.scalars().all()

        # Формируем текст сообщения
        response = f"📈 <b>Все доступные акции (Страница {page + 1} из {total_pages}):</b>\n\n"
        for stock in stocks:
            status = stock.figi_status if stock.figi_status else "UNKNOWN"
            status_icon = "✅" if status == "SUCCESS" else "⚠️" if status == "PENDING" else "❌"
            price = stock.last_price if stock.last_price is not None else "N/A"
            response += f"{status_icon} {stock.ticker} - {stock.name} | Цена: {price} RUB\n"

        # Создаем клавиатуру с кнопками навигации
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"list_all_stocks_{page-1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"list_all_stocks_{page+1}"))

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            nav_buttons,
            [InlineKeyboardButton(text="⬅️ Вернуться в меню акций", callback_data="stocks_menu")]
        ])

        # Отправляем или редактируем сообщение
        await callback_query.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при получении всех акций для пользователя {user_id}: {str(e)}", exc_info=True)
        await callback_query.message.edit_text(
            f"Произошла ошибка при получении списка акций: {html.escape(str(e))}",
            parse_mode="HTML",
            reply_markup=get_stocks_menu()
        )
    finally:
        await session.close()
        logger.info(f"Сессия закрыта для пользователя {user_id}")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "check_price")
async def prompt_check_price(callback_query: CallbackQuery, state: FSMContext):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет проверить цену акции")
    await callback_query.message.edit_text("🔍 Введите тикер акции (например, SBER.ME):", reply_markup=None)
    await state.set_state(CheckPriceState.waiting_for_ticker)
    await callback_query.answer()

@router.message(CheckPriceState.waiting_for_ticker)
async def check_price_handler(message: Message, session: AsyncSession, state: FSMContext):
    user_id = message.from_user.id
    ticker = message.text.strip()
    logger.info(f"Пользователь {user_id} запросил цену акции {ticker}")

    try:
        # Проверяем наличие акции в базе
        stock_result = await session.execute(
            select(Stock).where(Stock.ticker == ticker)
        )
        stock = stock_result.scalars().first()
        if not stock:
            await message.answer(f"Акция {ticker} не найдена в базе.", reply_markup=get_stocks_menu())
            await state.clear()
            return

        # Проверяем наличие пользователя и токена
        user_result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = user_result.scalars().first()

        if not user or not user.tinkoff_token:
            response = (
                f"🔍 Акция: {stock.ticker} ({stock.name})\n"
                f"💰 Последняя известная цена: {stock.last_price if stock.last_price is not None else 'N/A'} RUB\n\n"
                "🔑 Для получения актуальной цены установите токен T-Invest API в меню настроек."
            )
            await message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
            await state.clear()
            return

        # Если токен есть, запрашиваем актуальную цену через API
        async with AsyncClient(user.tinkoff_token) as client:
            figi = stock.figi
            if not figi:
                logger.warning(f"FIGI для {ticker} отсутствует в базе, пытаемся обновить...")
                figi = await update_figi(client, stock, session)
                if not figi:
                    await message.answer(f"Не удалось получить FIGI для {ticker}. Попробуйте позже.", reply_markup=get_stocks_menu())
                    await state.clear()
                    return

            try:
                last_price = (await client.market_data.get_last_prices(figi=[figi])).last_prices[0].price
                price_value = last_price.units + last_price.nano / 1e9

                # Обновляем цену в базе
                stock.last_price = price_value
                session.add(stock)
                await session.commit()
                logger.info(f"Цена для {ticker} обновлена: {price_value} RUB")

                response = (
                    f"🔍 Акция: {stock.ticker} ({stock.name})\n"
                    f"💰 Актуальная цена: {price_value:.2f} RUB"
                )
                await message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
            except InvestError as e:
                logger.error(f"Ошибка Tinkoff API при получении цены для {ticker}: {e}")
                response = (
                    f"🔍 Акция: {stock.ticker} ({stock.name})\n"
                    f"💰 Последняя известная цена: {stock.last_price if stock.last_price is not None else 'N/A'} RUB\n\n"
                    f"❌ Ошибка API Tinkoff: {html.escape(str(e))}. Проверьте токен или попробуйте позже."
                )
                await message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
            except Exception as e:
                logger.error(f"Неизвестная ошибка при получении цены для {ticker}: {e}")
                await message.answer(
                    f"❌ Ошибка при получении цены для {ticker}: {html.escape(str(e))}. Попробуйте позже.",
                    parse_mode="HTML",
                    reply_markup=get_stocks_menu()
                )
    except Exception as e:
        logger.error(f"Ошибка при получении цены для {ticker}: {e}")
        await message.answer("❌ Ошибка при получении цены.", reply_markup=get_stocks_menu())
    finally:
        await state.clear()

@router.callback_query(lambda c: c.data == "price_chart")
async def prompt_price_chart(callback_query: CallbackQuery, state: FSMContext):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет увидеть график цены акции")
    await callback_query.message.edit_text("📉 Введите тикер акции для построения графика (например, SBER.ME):", reply_markup=None)
    await state.set_state(PriceChartState.waiting_for_ticker)
    await callback_query.answer()

@router.message(CheckPriceState.waiting_for_ticker)
async def check_price_handler(message: Message, session: AsyncSession, state: FSMContext):
    user_id = message.from_user.id
    ticker = message.text.strip()
    logger.info(f"Пользователь {user_id} запросил цену акции {ticker}")

    try:
        # Проверяем наличие акции в базе
        stock_result = await session.execute(
            select(Stock).where(Stock.ticker == ticker)
        )
        stock = stock_result.scalars().first()
        if not stock:
            await message.answer(f"Акция {ticker} не найдена в базе.", reply_markup=get_stocks_menu())
            await state.clear()
            return

        # Проверяем наличие пользователя и токена
        user_result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = user_result.scalars().first()

        if not user:
            logger.warning(f"Пользователь {user_id} не найден в базе данных")
            await message.answer(
                "❌ Вы не зарегистрированы. Установите токен T-Invest API в меню настроек.",
                reply_markup=get_stocks_menu()
            )
            await state.clear()
            return

        if not user.tinkoff_token:
            logger.warning(f"Токен T-Invest API отсутствует для пользователя {user_id}")
            response = (
                f"🔍 Акция: {stock.ticker} ({stock.name})\n"
                f"💰 Последняя известная цена: {stock.last_price if stock.last_price is not None else 'N/A'} RUB\n\n"
                "⚠️ Токен T-Invest API не установлен! Пожалуйста, установите его в меню 'Настройки' → 'Установить токен', чтобы получать актуальные цены."
            )
            await message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
            await state.clear()
            return

        # Если токен есть, запрашиваем актуальную цену через API
        async with AsyncClient(user.tinkoff_token) as client:
            figi = stock.figi
            if not figi:
                logger.warning(f"FIGI для {ticker} отсутствует в базе, пытаемся обновить...")
                figi = await update_figi(client, stock, session)
                if not figi:
                    logger.error(f"Не удалось обновить FIGI для {ticker}")
                    await message.answer(
                        f"❌ Не удалось получить FIGI для {ticker}. Попробуйте позже или проверьте тикер.",
                        reply_markup=get_stocks_menu()
                    )
                    await state.clear()
                    return

            try:
                last_price = (await client.market_data.get_last_prices(figi=[figi])).last_prices[0].price
                price_value = last_price.units + last_price.nano / 1e9

                # Обновляем цену в базе
                stock.last_price = price_value
                session.add(stock)
                await session.commit()
                logger.info(f"Цена для {ticker} обновлена: {price_value} RUB")

                response = (
                    f"🔍 Акция: {stock.ticker} ({stock.name})\n"
                    f"💰 Актуальная цена: {price_value:.2f} RUB"
                )
                await message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
            except InvestError as e:
                logger.error(f"Ошибка T-Invest API при получении цены для {ticker}: {e}")
                response = (
                    f"🔍 Акция: {stock.ticker} ({stock.name})\n"
                    f"💰 Последняя известная цена: {stock.last_price if stock.last_price is not None else 'N/A'} RUB\n\n"
                    f"❌ Ошибка API Tinkoff: {html.escape(str(e))}. Проверьте токен или попробуйте позже."
                )
                await message.answer(response, parse_mode="HTML", reply_markup=get_stocks_menu())
            except Exception as e:
                logger.error(f"Неизвестная ошибка при получении цены для {ticker}: {e}")
                await message.answer(
                    f"❌ Ошибка при получении цены для {ticker}: {html.escape(str(e))}. Попробуйте позже.",
                    parse_mode="HTML",
                    reply_markup=get_stocks_menu()
                )
    except Exception as e:
        logger.error(f"Ошибка при обработке запроса цены для {ticker}: {e}")
        await message.answer("❌ Ошибка при получении цены. Попробуйте снова.", reply_markup=get_stocks_menu())
    finally:
        await state.clear()

@router.callback_query(lambda c: c.data.startswith("list_all_stocks_"))
async def list_all_stocks(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил список всех акций")
    
    try:
        # Извлекаем номер страницы из callback_data
        page = int(callback_query.data.split("_")[-1])
        items_per_page = 20

        # Получаем общее количество акций
        try:
            result = await session.execute(select(func.count()).select_from(Stock))
            total_stocks = result.scalar()
            logger.info(f"Общее количество акций в базе: {total_stocks}")
        except Exception as e:
            logger.error(f"Ошибка при получении количества акций: {str(e)}")
            await callback_query.message.edit_text(
                "❌ Ошибка при получении списка акций: Не удалось подключиться к базе данных.",
                parse_mode="HTML",
                reply_markup=get_stocks_menu()
            )
            await callback_query.answer()
            return

        total_pages = (total_stocks + items_per_page - 1) // items_per_page

        if total_stocks == 0:
            await callback_query.message.edit_text(
                "В базе нет доступных акций. Попробуйте позже.",
                parse_mode="HTML",
                reply_markup=get_stocks_menu()
            )
            await callback_query.answer()
            return

        if page < 0:
            page = 0
        if page >= total_pages:
            page = total_pages - 1

        # Получаем акции для текущей страницы
        try:
            result = await session.execute(
                select(Stock).offset(page * items_per_page).limit(items_per_page)
            )
            stocks = result.scalars().all()
            logger.info(f"Получено {len(stocks)} акций для страницы {page + 1}")
        except Exception as e:
            logger.error(f"Ошибка при получении акций для страницы {page + 1}: {str(e)}")
            await callback_query.message.edit_text(
                "❌ Ошибка при получении списка акций: Не удалось получить данные из базы.",
                parse_mode="HTML",
                reply_markup=get_stocks_menu()
            )
            await callback_query.answer()
            return

        response = f"📈 <b>Все доступные акции (Страница {page + 1} из {total_pages}):</b>\n\n"
        for stock in stocks:
            status = stock.figi_status if stock.figi_status else "UNKNOWN"
            status_icon = "✅" if status == "SUCCESS" else "⚠️" if status == "PENDING" else "❌"
            price = stock.last_price if stock.last_price is not None else "N/A"
            response += f"{status_icon} {stock.ticker} - {stock.name} | Цена: {price} RUB\n"

        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"list_all_stocks_{page-1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"list_all_stocks_{page+1}"))

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            nav_buttons,
            [InlineKeyboardButton(text="⬅️ Вернуться в меню акций", callback_data="stocks_menu")]
        ])

        await callback_query.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при обработке запроса списка акций для пользователя {user_id}: {str(e)}")
        await callback_query.message.edit_text(
            f"❌ Ошибка при получении списка акций: {html.escape(str(e))}",
            parse_mode="HTML",
            reply_markup=get_stocks_menu()
        )
    finally:
        await session.close()
        logger.info(f"Сессия закрыта для пользователя {user_id}")
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "subscribe")
async def prompt_subscribe(callback_query: CallbackQuery):
    logger.info(f"Пользователь {callback_query.from_user.id} хочет подписаться на акции")
    await callback_query.message.edit_text("🔔 Введите тикер акции для подписки (например, SBER.ME):", reply_markup=None)
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
            await callback_query.message.edit_text(
                "Вы не подписаны ни на одну акцию. Нажмите 'Подписаться' в меню акций.",
                parse_mode="HTML",
                reply_markup=get_stocks_menu()
            )
            return

        result = await session.execute(
            select(Stock).where(Stock.ticker.in_(subscribed_tickers))
        )
        stocks = result.scalars().all()

        if not stocks:
            await callback_query.message.edit_text(
                "Акции не найдены. Попробуйте позже.",
                parse_mode="HTML",
                reply_markup=get_stocks_menu()
            )
            return

        user_result = await session.execute(
            select(User).where(User.user_id == user_id)
        )
        user = user_result.scalars().first()
        if not user or not user.tinkoff_token:
            await callback_query.message.edit_text(
                "🔑 У вас не установлен токен T-Invest API. Установите его в меню настроек.",
                parse_mode="HTML",
                reply_markup=get_stocks_menu()
            )
            return

        async with AsyncClient(user.tinkoff_token) as client:
            response = "📊 <b>Сигналы роста:</b>\n\n"
            for stock in stocks:
                if not stock.figi:
                    figi = await update_figi(client, stock, session)
                    if not figi:
                        logger.warning(f"Пропущена акция {stock.ticker} из-за отсутствия FIGI")
                        continue

                end_date = datetime.utcnow()
                start_date = end_date - timedelta(days=30)
                try:
                    candles = await client.market_data.get_candles(
                        figi=stock.figi,
                        from_=start_date,
                        to=end_date,
                        interval=CandleInterval.CANDLE_INTERVAL_DAY
                    )
                except InvestError as e:
                    logger.warning(f"Ошибка получения свечей для {stock.ticker}: {e}")
                    continue

                if not candles.candles or len(candles.candles) < 20:
                    logger.warning(f"Недостаточно данных для {stock.ticker}: {len(candles.candles)} свечей")
                    continue

                prices = [candle.close.units + candle.close.nano / 1e9 for candle in candles.candles]
                rsi, macd, signal, upper_band, lower_band = await calculate_indicators(prices)

                if rsi is None:
                    logger.warning(f"Не удалось рассчитать индикаторы для {stock.ticker}")
                    continue

                current_price = prices[-1]
                logger.info(f"Индикаторы для {stock.ticker}: RSI={rsi:.2f}, MACD={macd:.2f}, Signal={signal:.2f}, "
                           f"Upper Band={upper_band:.2f}, Lower Band={lower_band:.2f}, Current Price={current_price:.2f}")

                signal_text = ""
                if rsi < 30 and macd > signal and current_price < lower_band:
                    signal_text = "📈 Сигнал на покупку: RSI < 30, MACD > Signal, цена ниже нижней Bollinger Band"
                elif rsi > 70 and current_price > upper_band:
                    signal_text = "📉 Сигнал на продажу: RSI > 70, цена выше верхней Bollinger Band"
                else:
                    logger.info(f"Сигналы для {stock.ticker} не сгенерированы: RSI={rsi:.2f}, "
                               f"MACD-Signal={macd-signal:.2f}, Price vs Bands={current_price:.2f} ({lower_band:.2f}, {upper_band:.2f})")

                if signal_text:
                    response += f"🔹 {stock.ticker} ({stock.name})\n"
                    response += f"💰 Цена: {current_price:.2f} RUB\n"
                    response += f"📊 {signal_text}\n"
                    response += f"📈 RSI: {rsi:.2f}\n\n"

            if not response.strip().endswith("📊 <b>Сигналы роста:</b>\n\n"):
                response += "🚫 Нет актуальных сигналов на текущий момент.\n\n"

            response += "⬅️ Вернуться в меню акций."
            await callback_query.message.edit_text(response, parse_mode="HTML", reply_markup=get_stocks_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении сигналов: {e}")
        await callback_query.message.edit_text(
            "Произошла ошибка при получении сигналов. Проверьте подключение к Tinkoff API.",
            parse_mode="HTML",
            reply_markup=get_stocks_menu()
        )
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
    await callback_query.message.edit_text("🤖 <b>Меню автоторговли:</b>", parse_mode="HTML", reply_markup=get_autotrading_menu())
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "view_profile")
async def view_profile(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} запросил просмотр профиля")
    try:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()

        if not user or not user.tinkoff_token:
            await callback_query.message.edit_text(
                "🔑 У вас не установлен токен T-Invest API. Установите его в меню настроек.",
                parse_mode="HTML",
                reply_markup=get_autotrading_menu()
            )
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
                await callback_query.message.edit_text(
                    "❌ Счета не найдены. Проверьте токен T-Invest API.",
                    parse_mode="HTML",
                    reply_markup=get_autotrading_menu()
                )
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
        await callback_query.message.edit_text(profile_text, parse_mode="HTML", reply_markup=get_autotrading_menu())
    except Exception as e:
        logger.error(f"Ошибка при просмотре профиля пользователя {user_id}: {e}")
        await callback_query.message.edit_text(
            "❌ Ошибка при просмотре профиля.",
            parse_mode="HTML",
            reply_markup=get_autotrading_menu()
        )
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "enable_autotrading")
async def enable_autotrading(callback_query: CallbackQuery, session: AsyncSession, trading_bot):
    user_id = callback_query.from_user.id
    logger.info(f"Пользователь {user_id} пытается включить автоторговлю")
    try:
        result = await session.execute(select(User).where(User.user_id == user_id))
        user = result.scalars().first()
        if not user:
            await callback_query.message.edit_text(
                "❌ Вы не зарегистрированы. Установите токен T-Invest API в меню настроек.",
                parse_mode="HTML",
                reply_markup=get_autotrading_menu()
            )
            return

        if not user.tinkoff_token:
            await callback_query.message.edit_text(
                "❌ Токен T-Invest API не установлен. Установите его в меню настроек.",
                parse_mode="HTML",
                reply_markup=get_autotrading_menu()
            )
            return

        if user.autotrading_enabled:
            await callback_query.message.edit_text(
                "⚠️ Автоторговля уже включена!",
                parse_mode="HTML",
                reply_markup=get_autotrading_menu()
            )
            return

        stocks_result = await session.execute(select(Stock))
        stocks = stocks_result.scalars().all()
        if not stocks:
            await callback_query.message.edit_text(
                "❌ Нет доступных акций для торговли. Обратитесь к администратору или добавьте тикеры.",
                parse_mode="HTML",
                reply_markup=get_autotrading_menu()
            )
            return

        user.autotrading_enabled = True
        await session.commit()

        trading_bot.stop_streaming(user_id)
        task = asyncio.create_task(trading_bot.stream_and_trade(user_id))
        trading_bot.stream_tasks[user_id] = task

        async with AsyncClient(user.tinkoff_token) as client:
            account_id = (await client.users.get_accounts()).accounts[0].id
            for stock in stocks:
                if stock.ticker != "SBER.ME":
                    continue
                if not stock.figi:
                    figi = await fetch_figi_with_retry(client, stock.ticker)
                    if not figi:
                        logger.warning(f"Не удалось получить FIGI для {stock.ticker}, пропускаем...")
                        continue
                    stock.figi = figi
                    session.add(stock)
                    await session.commit()

                candles = await client.market_data.get_candles(
                    figi=stock.figi,
                    from_=datetime.utcnow() - timedelta(days=30),
                    to=datetime.utcnow(),
                    interval=CandleInterval.CANDLE_INTERVAL_DAY
                )
                prices = [candle.close.units + candle.close.nano / 1e9 for candle in candles.candles]
                rsi, _, _, _, _ = await calculate_indicators(prices)
                if rsi and rsi < 30:
                    logger.info(f"Покупка акции {stock.ticker} по стратегии RSI < 30")
                    last_price = (await client.market_data.get_last_prices(figi=[stock.figi])).last_prices[0].price
                    last_price_value = last_price.units + last_price.nano / 1e9
                    order = await client.orders.post_order(
                        figi=stock.figi,
                        quantity=1,
                        price=last_price,
                        direction=OrderDirection.ORDER_DIRECTION_BUY,
                        account_id=account_id,
                        order_type="LIMIT"
                    )
                    trade = TradeHistory(
                        user_id=user_id,
                        ticker=stock.ticker,
                        action="buy",
                        quantity=1,
                        price=last_price_value,
                        total=last_price_value,
                        created_at=datetime.utcnow()
                    )
                    session.add(trade)
                    await session.commit()
                    await callback_query.message.answer(f"✅ Куплена акция {stock.ticker} по цене {last_price_value:.2f} RUB")
                    break

        await callback_query.message.edit_text(
            "▶️ Автоторговля включена!",
            parse_mode="HTML",
            reply_markup=get_autotrading_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка при включении автоторговли для пользователя {user_id}: {str(e)}")
        error_message = "❌ Ошибка при включении автоторговли: "
        if "Нет подходящих тикеров" in str(e):
            error_message += "Нет подходящих акций для торговли. Попробуйте добавить другие тикеры."
        elif "Токен T-Invest API не найден" in str(e):
            error_message += "Токен T-Invest API не установлен."
        elif "Instrument not found" in str(e):
            error_message += "Некоторые тикеры недоступны. Проверьте базу акций."
        elif "Недостаточно данных для обучения ML" in str(e):
            error_message += "Недостаточно данных для обучения модели."
        else:
            error_message += f"Неизвестная ошибка: {html.escape(str(e))}."
        await callback_query.message.edit_text(
            error_message,
            parse_mode="HTML",
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
            await callback_query.message.edit_text(
                "❌ Вы не зарегистрированы. Установите токен T-Invest API в меню настроек.",
                parse_mode="HTML",
                reply_markup=get_autotrading_menu()
            )
            return

        user.autotrading_enabled = False
        await session.commit()

        trading_bot.stop_streaming(user_id)

        await callback_query.message.edit_text(
            "⏹️ Автоторговля отключена!",
            parse_mode="HTML",
            reply_markup=get_autotrading_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка при отключении автоторговли для пользователя {user_id}: {e}")
        await callback_query.message.edit_text(
            "❌ Ошибка при отключении автоторговли.",
            parse_mode="HTML",
            reply_markup=get_autotrading_menu()
        )
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
            await callback_query.message.edit_text(
                "📜 У вас пока нет истории торгов.",
                parse_mode="HTML",
                reply_markup=get_trading_menu()
            )
            return

        response = "📜 <b>История торгов (последние 10):</b>\n\n"
        for trade in trades:
            action = "Покупка" if trade.action == "buy" else "Продажа"
            response += f"🕒 {trade.created_at.strftime('%Y-%m-%d %H:%M:%S')} | {action} | {trade.ticker} | {trade.quantity} акций | {trade.price} RUB | Итог: {trade.total} RUB\n"
        response += "\n⬅️ Вернуться в меню торговли."
        await callback_query.message.edit_text(response, parse_mode="HTML", reply_markup=get_trading_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении истории торгов: {e}")
        await callback_query.message.edit_text(
            "❌ Ошибка при получении истории торгов.",
            parse_mode="HTML",
            reply_markup=get_trading_menu()
        )
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
            await callback_query.message.edit_text(
                "🔑 У вас не установлен токен T-Invest API. Установите его в меню настроек.",
                parse_mode="HTML",
                reply_markup=get_trading_menu()
            )
            return

        async with AsyncClient(user.tinkoff_token) as client:
            accounts = await client.users.get_accounts()
            if not accounts.accounts:
                await callback_query.message.edit_text(
                    "❌ Счета не найдены. Проверьте токен T-Invest API.",
                    parse_mode="HTML",
                    reply_markup=get_trading_menu()
                )
                return
            account_id = accounts.accounts[0].id

            portfolio = await client.operations.get_portfolio(account_id=account_id)
            total_balance = portfolio.total_amount_currencies.units + portfolio.total_amount_currencies.nano / 1e9

        await callback_query.message.edit_text(
            f"💰 Ваш текущий баланс: {total_balance:.2f} RUB",
            parse_mode="HTML",
            reply_markup=get_trading_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка при получении баланса для пользователя {user_id}: {e}")
        await callback_query.message.edit_text(
            "❌ Ошибка при получении баланса.",
            parse_mode="HTML",
            reply_markup=get_trading_menu()
        )
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
        await callback_query.message.edit_text(response, parse_mode="HTML", reply_markup=get_trading_menu())
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")
        await callback_query.message.edit_text(
            "❌ Ошибка при получении статистики.",
            parse_mode="HTML",
            reply_markup=get_trading_menu()
        )
    await callback_query.answer()