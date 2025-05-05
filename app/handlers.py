from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from app.models import User, Subscription, Stock
from tinkoff.invest import AsyncClient, CandleInterval, InstrumentIdType
from tinkoff.invest.exceptions import RequestError
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import io
import logging
import asyncio
import pandas as pd
import numpy as np
from functools import wraps

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()

# Декоратор для повторных попыток
def retry(max_attempts=3, delay=5):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        logger.error(f"Не удалось выполнить {func.__name__} после {max_attempts} попыток: {str(e)}")
                        raise
                    logger.warning(f"Попытка {attempt + 1}/{max_attempts} не удалась для {func.__name__}: {str(e)}, повторяем...")
                    await asyncio.sleep(delay)
            return None
        return wrapper
    return decorator

class Form(StatesGroup):
    tinkoff_token = State()
    ticker = State()

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer(
        "Привет! Я бот для торговли на рынке ценных бумаг.\n"
        "Используй команды:\n"
        "/set_token - установить токен Tinkoff API\n"
        "/subscribe - подписаться на акции\n"
        "/signals - получить торговые сигналы\n"
        "/chart - построить график цен\n"
        "/enable_autotrading - включить автоторговлю\n"
        "/disable_autotrading - отключить автоторговлю\n"
        "/profit_report - получить отчёт о прибыли"
    )
    async with message.bot["db_session"]() as session:
        try:
            existing_user = await session.execute(
                select(User).where(User.user_id == str(message.from_user.id))
            )
            user = existing_user.scalars().first()
            if not user:
                new_user = User(
                    user_id=str(message.from_user.id),
                    username=message.from_user.username,
                )
                session.add(new_user)
                await session.commit()
                logger.info(f"Создан новый пользователь: {message.from_user.id}")
        except Exception as e:
            logger.error(f"Ошибка при создании пользователя {message.from_user.id}: {e}")
            await message.answer("Произошла ошибка при регистрации. Попробуйте позже.")
            await session.rollback()

@router.message(Command("set_token"))
async def cmd_set_token(message: types.Message, state: FSMContext):
    await message.answer("Пожалуйста, введите ваш токен Tinkoff API:")
    await state.set_state(Form.tinkoff_token)

@router.message(Form.tinkoff_token)
async def process_tinkoff_token(message: types.Message, state: FSMContext):
    async with message.bot["db_session"]() as session:
        try:
            result = await session.execute(
                select(User).where(User.user_id == str(message.from_user.id))
            )
            user = result.scalars().first()
            if not user:
                await message.answer("Пользователь не найден. Используйте /start для регистрации.")
                await state.clear()
                return
            user.tinkoff_token = message.text
            session.add(user)
            await session.commit()
            await message.answer("Токен Tinkoff API успешно сохранён!")
            logger.info(f"Токен сохранён для пользователя {message.from_user.id}")
        except Exception as e:
            logger.error(f"Ошибка при сохранении токена для {message.from_user.id}: {e}")
            await message.answer("Произошла ошибка при сохранении токена. Попробуйте позже.")
            await session.rollback()
    await state.clear()

@router.message(Command("subscribe"))
async def cmd_subscribe(message: types.Message, state: FSMContext):
    await message.answer("Введите тикер акции (например, SBER):")
    await state.set_state(Form.ticker)

@router.message(Form.ticker)
async def process_ticker(message: types.Message, state: FSMContext):
    ticker = message.text.upper()
    async with message.bot["db_session"]() as session:
        try:
            user_result = await session.execute(
                select(User).where(User.user_id == str(message.from_user.id))
            )
            user = user_result.scalars().first()
            if not user:
                await message.answer("Пользователь не найден. Используйте /start для регистрации.")
                await state.clear()
                return

            existing_subscription = await session.execute(
                select(Subscription).where(
                    (Subscription.user_id == user.id) & (Subscription.ticker == ticker)
                )
            )
            if existing_subscription.scalars().first():
                await message.answer(f"Вы уже подписаны на {ticker}.")
                await state.clear()
                return

            subscription = Subscription(user_id=user.id, ticker=ticker)
            session.add(subscription)

            existing_stock = await session.execute(
                select(Stock).where(Stock.ticker == ticker)
            )
            if not existing_stock.scalars().first():
                stock = Stock(user_id=user.id, ticker=ticker)
                session.add(stock)
                logger.info(f"Добавлена новая акция {ticker} для пользователя {message.from_user.id}")
            else:
                logger.info(f"Акция {ticker} уже существует в базе данных")

            await session.commit()
            await message.answer(f"Вы успешно подписались на {ticker}!")
            logger.info(f"Пользователь {message.from_user.id} подписался на {ticker}")
        except Exception as e:
            logger.error(f"Ошибка при подписке на {ticker} для {message.from_user.id}: {e}")
            await message.answer("Произошла ошибка при подписке. Попробуйте позже.")
            await session.rollback()
    await state.clear()

@router.message(Command("enable_autotrading"))
async def cmd_enable_autotrading(message: types.Message):
    async with message.bot["db_session"]() as session:
        try:
            result = await session.execute(
                select(User).where(User.user_id == str(message.from_user.id))
            )
            user = result.scalars().first()
            if not user:
                await message.answer("Пользователь не найден. Используйте /start для регистрации.")
                return
            if not user.tinkoff_token:
                await message.answer("Сначала установите токен Tinkoff API с помощью /set_token.")
                return
            user.autotrading_enabled = True
            session.add(user)
            await session.commit()
            await message.answer("Автоторговля включена!")
            logger.info(f"Автоторговля включена для пользователя {message.from_user.id}")
            trading_bot: TradingBot = message.bot["trading_bot"]
            await trading_bot.stream_and_trade(user.user_id)
        except Exception as e:
            logger.error(f"Ошибка при включении автоторговли для {message.from_user.id}: {e}")
            await message.answer("Произошла ошибка при включении автоторговли. Попробуйте позже.")
            await session.rollback()

@router.message(Command("disable_autotrading"))
async def cmd_disable_autotrading(message: types.Message):
    async with message.bot["db_session"]() as session:
        try:
            result = await session.execute(
                select(User).where(User.user_id == str(message.from_user.id))
            )
            user = result.scalars().first()
            if not user:
                await message.answer("Пользователь не найден. Используйте /start для регистрации.")
                return
            user.autotrading_enabled = False
            session.add(user)
            await session.commit()
            await message.answer("Автоторговля отключена!")
            logger.info(f"Автоторговля отключена для пользователя {message.from_user.id}")
            trading_bot: TradingBot = message.bot["trading_bot"]
            trading_bot.stop_streaming_for_user(user.user_id)
        except Exception as e:
            logger.error(f"Ошибка при отключении автоторговли для {message.from_user.id}: {e}")
            await message.answer("Произошла ошибка при отключении автоторговли. Попробуйте позже.")
            await session.rollback()

@router.message(Command("profit_report"))
async def cmd_profit_report(message: types.Message):
    async with message.bot["db_session"]() as session:
        try:
            user_id = str(message.from_user.id)
            trading_bot: TradingBot = message.bot["trading_bot"]
            await trading_bot.send_daily_profit_report(session, user_id)
            logger.info(f"Отчёт о прибыли отправлен пользователю {user_id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке отчёта о прибыли для {user_id}: {e}")
            await message.answer("Произошла ошибка при отправке отчёта. Попробуйте позже.")

@retry(max_attempts=3, delay=10)
async def fetch_figi_with_retry(client, ticker):
    try:
        response = await client.instruments.share_by(
            id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
            class_code="TQBR",
            id=ticker
        )
        return response.instrument.figi
    except RequestError as e:
        if "NOT_FOUND" in str(e):
            logger.error(f"Инструмент {ticker} не найден в API")
            return None
        elif "RESOURCE_EXHAUSTED" in str(e):
            reset_time = int(e.metadata.get('ratelimit_reset', 60)) if e.metadata.get('ratelimit_reset') else 60
            logger.warning(f"Достигнут лимит запросов API для {ticker}, ожидание {reset_time} секунд...")
            await asyncio.sleep(reset_time)
            response = await client.instruments.share_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                class_code="TQBR",
                id=ticker
            )
            return response.instrument.figi
        else:
            logger.error(f"Не удалось получить FIGI для {ticker}: {e}")
            return None

@retry(max_attempts=3, delay=10)
async def get_candles_with_retry(client, figi, start_date, end_date, interval):
    try:
        candles = await client.market_data.get_candles(
            figi=figi,
            from_=start_date,
            to=end_date,
            interval=interval
        )
        return candles.candles
    except RequestError as e:
        if "RESOURCE_EXHAUSTED" in str(e):
            reset_time = int(e.metadata.get('ratelimit_reset', 60)) if e.metadata.get('ratelimit_reset') else 60
            logger.warning(f"Достигнут лимит запросов API для FIGI {figi}, ожидание {reset_time} секунд...")
            await asyncio.sleep(reset_time)
            return await client.market_data.get_candles(
                figi=figi,
                from_=start_date,
                to=end_date,
                interval=interval
            ).candles
        else:
            logger.error(f"Не удалось получить свечи для FIGI {figi}: {e}")
            return []

@router.message(Command("chart"))
async def generate_price_chart(message: types.Message):
    user_id = str(message.from_user.id)
    async with message.bot["db_session"]() as session:
        try:
            user_result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = user_result.scalars().first()
            if not user or not user.tinkoff_token:
                await message.answer("Сначала установите токен Tinkoff API с помощью /set_token.")
                return

            result = await session.execute(
                select(Subscription.ticker).where(Subscription.user_id == user.id)
            )
            tickers = result.scalars().all()
            if not tickers:
                await message.answer("У вас нет подписок на акции. Используйте /subscribe для добавления.")
                return

            ticker = tickers[0]
            stock_result = await session.execute(
                select(Stock).where(Stock.ticker == ticker)
            )
            stock = stock_result.scalars().first()
            if not stock:
                await message.answer(f"Акция {ticker} не найдена в базе данных.")
                return

            figi = stock.figi
            if not figi:
                async with AsyncClient(user.tinkoff_token) as client:
                    figi = await fetch_figi_with_retry(client, ticker)
                    if not figi:
                        await message.answer(f"Не удалось получить FIGI для {ticker}. Попробуйте позже.")
                        return
                    stock.figi = figi
                    session.add(stock)
                    await session.commit()
                    logger.info(f"FIGI для {ticker} обновлён: {figi}")

            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=30)
            async with AsyncClient(user.tinkoff_token) as client:
                candles = await get_candles_with_retry(
                    client, figi, start_date, end_date, CandleInterval.CANDLE_INTERVAL_DAY
                )
                if not candles:
                    await message.answer(f"Не удалось получить данные для {ticker}. Попробуйте позже.")
                    return

                times = [candle.time for candle in candles]
                prices = [candle.close.units + candle.close.nano / 1e9 for candle in candles]

                plt.figure(figsize=(10, 5))
                plt.plot(times, prices, label=f"{ticker} Price")
                plt.title(f"График цен для {ticker} (30 дней)")
                plt.xlabel("Дата")
                plt.ylabel("Цена (RUB)")
                plt.legend()
                plt.grid(True)

                buf = io.BytesIO()
                plt.savefig(buf, format="png")
                buf.seek(0)
                plt.close()

                await message.answer_photo(
                    photo=types.BufferedInputFile(buf.getvalue(), filename=f"{ticker}_chart.png"),
                    caption=f"График цен для {ticker}"
                )
                logger.info(f"График цен для {ticker} отправлен пользователю {user_id}")
        except Exception as e:
            logger.error(f"Ошибка при построении графика для {user_id}: {e}")
            await message.answer("Произошла ошибка при построении графика. Попробуйте позже.")

def calculate_indicators(prices):
    if not prices or len(prices) < 20:
        logger.warning("Недостаточно данных для расчёта индикаторов")
        return None, None, None, None, None

    df = pd.DataFrame(prices, columns=['close'])
    
    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # MACD
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=9, adjust=False).mean()
    
    # Bollinger Bands
    sma = df['close'].rolling(window=20).mean()
    std = df['close'].rolling(window=20).std()
    upper_band = sma + (std * 2)
    lower_band = sma - (std * 2)

    return rsi.iloc[-1], macd.iloc[-1], signal_line.iloc[-1], upper_band.iloc[-1], lower_band.iloc[-1]

@router.message(Command("signals"))
async def signals(message: types.Message):
    user_id = str(message.from_user.id)
    async with message.bot["db_session"]() as session:
        try:
            user_result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = user_result.scalars().first()
            if not user or not user.tinkoff_token:
                await message.answer("Сначала установите токен Tinkoff API с помощью /set_token.")
                return

            # Кэширование тикеров
            cached_tickers = getattr(message.bot, "cached_tickers", [])
            try:
                result = await session.execute(
                    select(Subscription.ticker).where(Subscription.user_id == user.id)
                )
                subscribed_tickers = result.scalars().all()
                message.bot.cached_tickers = subscribed_tickers  # Обновляем кэш
            except Exception as e:
                logger.warning(f"Ошибка запроса к базе данных для {user_id}, используем кэш: {e}")
                subscribed_tickers = cached_tickers
                if not subscribed_tickers:
                    await message.answer("Нет доступных тикеров для анализа. Используйте /subscribe для добавления.")
                    return

            if not subscribed_tickers:
                await message.answer("У вас нет подписок на акции. Используйте /subscribe для добавления.")
                return

            async with AsyncClient(user.tinkoff_token) as client:
                signals_text = []
                end_date = datetime.utcnow()
                start_date = end_date - timedelta(days=60)

                for ticker in subscribed_tickers:
                    stock_result = await session.execute(
                        select(Stock).where(Stock.ticker == ticker)
                    )
                    stock = stock_result.scalars().first()
                    if not stock:
                        logger.warning(f"Акция {ticker} не найдена в базе данных для {user_id}")
                        continue

                    figi = stock.figi
                    if not figi:
                        figi = await fetch_figi_with_retry(client, ticker)
                        if not figi:
                            signals_text.append(f"{ticker}: Не удалось получить FIGI.")
                            continue
                        stock.figi = figi
                        session.add(stock)
                        await session.commit()
                        logger.info(f"FIGI для {ticker} обновлён: {figi}")

                    candles = await get_candles_with_retry(
                        client, figi, start_date, end_date, CandleInterval.CANDLE_INTERVAL_DAY
                    )
                    if not candles:
                        signals_text.append(f"{ticker}: Не удалось получить данные.")
                        continue

                    prices = [candle.close.units + candle.close.nano / 1e9 for candle in candles]
                    rsi, macd, signal_line, upper_band, lower_band = calculate_indicators(prices)

                    if None in (rsi, macd, signal_line, upper_band, lower_band):
                        signals_text.append(f"{ticker}: Недостаточно данных для анализа.")
                        continue

                    last_price = prices[-1]
                    signal = []
                    if rsi > 70:
                        signal.append("Перекуплен (RSI)")
                    elif rsi < 30:
                        signal.append("Перепродан (RSI)")
                    if macd > signal_line:
                        signal.append("Покупка (MACD)")
                    elif macd < signal_line:
                        signal.append("Продажа (MACD)")
                    if last_price > upper_band:
                        signal.append("Выше верхней Bollinger Band")
                    elif last_price < lower_band:
                        signal.append("Ниже нижней Bollinger Band")

                    if signal:
                        signals_text.append(f"{ticker}: {', '.join(signal)}")
                    else:
                        signals_text.append(f"{ticker}: Нет сигналов.")

                if not signals_text:
                    await message.answer("Не удалось сгенерировать сигналы для ваших акций.")
                else:
                    await message.answer("\n".join(signals_text))
                    logger.info(f"Сигналы отправлены пользователю {user_id}: {signals_text}")
        except Exception as e:
            logger.error(f"Ошибка при генерации сигналов для {user_id}: {e}")
            await message.answer("Произошла ошибка при генерации сигналов. Попробуйте позже.")