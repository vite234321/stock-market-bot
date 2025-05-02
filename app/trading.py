import logging
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Stock, Subscription, TradeHistory, User, FigiStatus
from app.database import async_session
from datetime import datetime, timedelta
from tinkoff.invest import (
    AsyncClient, OrderDirection, OrderType, CandleInterval, InstrumentIdType,
    SubscribeCandlesRequest, SubscriptionAction, SubscriptionInterval
)
from tinkoff.invest.exceptions import InvestError
from aiogram import Bot
import html
import httpx
from sklearn.linear_model import LinearRegression
import numpy as np
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.status = "Ожидание"
        self.positions: Dict[str, Dict] = {}
        self.ml_models: Dict[str, LinearRegression] = {}
        self.news_cache: Dict[str, List[Dict]] = {}
        self.historical_data: Dict[str, List] = {}
        self.running = False
        self.stream_tasks: Dict[int, asyncio.Task] = {}
        self.streaming_client = None  # Для хранения клиента стриминга

    async def debug_available_shares(self, client: AsyncClient):
        try:
            response = await client.instruments.shares()
            for instrument in response.instruments:
                if instrument.class_code == "TQBR":
                    logger.info(f"Доступный тикер: {instrument.ticker}, FIGI: {instrument.figi}, Название: {instrument.name}")
        except Exception as e:
            logger.error(f"Ошибка при получении списка акций: {e}")

    async def update_figi(self, client: AsyncClient, stock: Stock, session: AsyncSession) -> Optional[str]:
        if stock.figi:
            return stock.figi

        logger.info(f"Обновление FIGI для {stock.ticker}...")
        try:
            cleaned_ticker = stock.ticker.replace(".ME", "")
            response = await client.instruments.share_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                class_code="TQBR",
                id=cleaned_ticker
            )
            if not hasattr(response, 'instrument') or not response.instrument.figi:
                logger.error(f"API Tinkoff не вернул FIGI для {stock.ticker}")
                return None

            stock.figi = response.instrument.figi
            stock.set_figi_status(FigiStatus.SUCCESS)
            session.add(stock)
            await session.commit()
            logger.info(f"FIGI для {stock.ticker} обновлён: {stock.figi}")
            return stock.figi
        except InvestError as e:
            logger.error(f"Не удалось обновить FIGI для {stock.ticker}: {e}")
            stock.set_figi_status(FigiStatus.FAILED)
            session.add(stock)
            await session.commit()
            return None
        except Exception as e:
            logger.error(f"Неожиданная ошибка при обновлении FIGI для {stock.ticker}: {e}")
            stock.set_figi_status(FigiStatus.FAILED)
            session.add(stock)
            await session.commit()
            return None

    async def fetch_news(self, ticker: str) -> List[Dict]:
        api_key = "YOUR_NEWSAPI_KEY"
        if not api_key:
            logger.warning("NewsAPI ключ не установлен, новости не будут проверяться")
            return []

        cleaned_ticker = ticker.replace(".ME", "")
        if cleaned_ticker in self.news_cache:
            return self.news_cache[cleaned_ticker]

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://newsapi.org/v2/everything",
                    params={
                        "q": cleaned_ticker,
                        "apiKey": api_key,
                        "language": "ru",
                        "sortBy": "publishedAt",
                        "pageSize": 10
                    }
                )
                response.raise_for_status()
                articles = response.json().get("articles", [])
                self.news_cache[cleaned_ticker] = articles
                logger.info(f"Получено {len(articles)} новостей для {cleaned_ticker}")
                if len(self.news_cache) > 50:
                    oldest_ticker = next(iter(self.news_cache))
                    del self.news_cache[oldest_ticker]
                    logger.info(f"Удалён кэш новостей для {oldest_ticker} для оптимизации памяти")
                return articles
        except Exception as e:
            logger.error(f"Ошибка при получении новостей для {ticker}: {e}")
            return []

    def is_negative_news(self, articles: List[Dict]) -> bool:
        if not articles:
            return False
        negative_keywords = {"падение", "кризис", "убытки", "снижение", "скандал", "санкции"}
        for article in articles:
            title = article.get("title", "").lower()
            description = article.get("description", "").lower()
            if any(keyword in title or keyword in description for keyword in negative_keywords):
                logger.warning(f"Обнаружены негативные новости: {title}")
                return True
        return False

    def calculate_rsi(self, prices: List[float], period: int = 14) -> Optional[float]:
        if not prices or len(prices) < period + 1:
            logger.warning(f"Недостаточно данных для расчёта RSI: {len(prices) if prices else 0} элементов, требуется {period + 1}")
            return None
        gains = []
        losses = []
        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def calculate_macd(self, prices: List[float], fast_period: int = 12, slow_period: int = 26, signal_period: int = 9) -> tuple:
        required_length = max(fast_period, slow_period, signal_period)  # Минимальная длина для расчёта всех EMA
        if not prices or len(prices) < required_length:
            logger.warning(f"Недостаточно данных для расчёта MACD: {len(prices) if prices else 0} элементов, требуется {required_length}")
            return None, None, None

        def ema(data, period):
            if not data or len(data) < period:
                logger.warning(f"Недостаточно данных для EMA: {len(data) if data else 0} элементов, требуется {period}")
                return None
            ema_values = []
            k = 2 / (period + 1)
            # Начальное значение EMA - простое среднее за period
            initial_ema = sum(data[:period]) / period
            ema_values.append(initial_ema)
            # Вычисляем EMA для оставшихся значений
            for i in range(period, len(data)):
                ema_value = data[i] * k + ema_values[-1] * (1 - k)
                ema_values.append(ema_value)
            return ema_values

        # Вычисляем EMA для быстрого и медленного периода
        ema_fast = ema(prices, fast_period)
        if ema_fast is None:
            logger.warning("Не удалось рассчитать EMA fast из-за недостатка данных")
            return None, None, None

        ema_slow = ema(prices, slow_period)
        if ema_slow is None:
            logger.warning("Не удалось рассчитать EMA slow из-за недостатка данных")
            return None, None, None

        # Убедимся, что ema_fast и ema_slow имеют одинаковую длину
        min_length = min(len(ema_fast), len(ema_slow))
        if min_length == 0:
            logger.warning("EMA fast или slow пусты")
            return None, None, None

        macd = [ema_fast[i] - ema_slow[i] for i in range(min_length)]
        if not macd:
            logger.warning("MACD не удалось рассчитать: пустой список")
            return None, None, None

        # Вычисляем сигнальную линию
        signal = ema(macd, signal_period)
        if signal is None:
            logger.warning("Сигнальная линия MACD не может быть рассчитана")
            return None, None, None

        # Последние значения для MACD, сигнальной линии и гистограммы
        signal_idx = len(signal) - 1
        macd_idx = len(macd) - 1
        if signal_idx < 0 or macd_idx < 0:
            logger.warning("Недостаточно данных для расчёта MACD: signal_idx или macd_idx меньше 0")
            return None, None, None

        histogram = macd[macd_idx] - signal[signal_idx]
        return macd[macd_idx], signal[signal_idx], histogram

    def calculate_atr(self, candles: List, period: int = 14) -> Optional[float]:
        if not candles or len(candles) < period + 1:
            logger.warning(f"Недостаточно данных для расчёта ATR: {len(candles) if candles else 0} элементов, требуется {period + 1}")
            return None
        tr_values = []
        for i in range(1, len(candles)):
            high = candles[i].high.units + candles[i].high.nano / 1e9
            low = candles[i].low.units + candles[i].low.nano / 1e9
            prev_close = candles[i-1].close.units + candles[i-1].close.nano / 1e9
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_values.append(tr)
        atr = sum(tr_values[-period:]) / period
        return atr

    def calculate_bollinger_bands(self, prices: List[float], period: int = 20, std_dev: float = 2) -> tuple:
        if not prices or len(prices) < period:
            logger.warning(f"Недостаточно данных для расчёта Bollinger Bands: {len(prices) if prices else 0} элементов, требуется {period}")
            return None, None, None
        sma = sum(prices[-period:]) / period
        variance = sum((p - sma) ** 2 for p in prices[-period:]) / period
        std = np.sqrt(variance)
        upper_band = sma + std_dev * std
        lower_band = sma - std_dev * std
        return sma, upper_band, lower_band

    async def train_ml_model(self, ticker: str, client: AsyncClient, figi: str):
        required_candles = 60
        prices = []

        if ticker in self.historical_data and len(self.historical_data[ticker]) >= required_candles:
            prices = [c["close"] for c in self.historical_data[ticker][-required_candles:]]
        else:
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=365)
            try:
                candles = await client.market_data.get_candles(
                    figi=figi,
                    from_=start_date,
                    to=end_date,
                    interval=CandleInterval.CANDLE_INTERVAL_DAY
                )
                if not candles.candles:
                    logger.warning(f"Не удалось получить свечи для {ticker}")
                    return
                prices = [candle.close.units + candle.close.nano / 1e9 for candle in candles.candles]
                self.historical_data[ticker] = [{"close": p, "time": c.time} for p, c in zip(prices, candles.candles)]
            except Exception as e:
                logger.error(f"Ошибка при получении свечей для {ticker}: {e}")
                return

        if not prices or len(prices) < required_candles:
            logger.warning(f"Недостаточно данных для обучения ML модели для {ticker}: {len(prices) if prices else 0} свечей, требуется {required_candles}")
            return

        X = []
        y = []
        for i in range(30, len(prices) - 1):
            window = prices[i-30:i]
            rsi = self.calculate_rsi(window)
            macd, signal, _ = self.calculate_macd(window)
            if rsi is None or macd is None or signal is None:
                logger.warning(f"Не удалось рассчитать индикаторы для {ticker} на итерации {i}")
                continue
            features = [window[-1], rsi, macd - signal]
            X.append(features)
            y.append(prices[i+1])

        if not X or len(X) < 10:
            logger.warning(f"Недостаточно данных для обучения ML после расчёта индикаторов для {ticker}: {len(X) if X else 0} точек")
            return

        model = LinearRegression()
        model.fit(X, y)
        self.ml_models[ticker] = model
        logger.info(f"ML модель обучена для {ticker}")

    def predict_price(self, ticker: str, prices: List[float]) -> Optional[float]:
        if not prices or ticker not in self.ml_models or len(prices) < 30:
            logger.warning(f"Недостаточно данных для предсказания цены для {ticker}: {len(prices) if prices else 0} элементов")
            return None
        window = prices[-30:]
        rsi = self.calculate_rsi(window)
        macd, signal, _ = self.calculate_macd(window)
        if rsi is None or macd is None or signal is None:
            logger.warning(f"Не удалось рассчитать индикаторы для предсказания цены для {ticker}")
            return None
        features = np.array([[window[-1], rsi, macd - signal]])
        predicted_price = self.ml_models[ticker].predict(features)[0]
        return predicted_price

    async def backtest_strategy(self, ticker: str, figi: str, client: AsyncClient) -> Dict:
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=365)
        try:
            candles = await client.market_data.get_candles(
                figi=figi,
                from_=start_date,
                to=end_date,
                interval=CandleInterval.CANDLE_INTERVAL_DAY
            )
            prices = [candle.close.units + candle.close.nano / 1e9 for candle in candles.candles] if candles.candles else []
        except Exception as e:
            logger.error(f"Ошибка при получении свечей для бэктестинга {ticker}: {e}")
            return {"profit": 0, "trades": 0}

        required_length = 60
        if not prices or len(prices) < required_length:
            logger.warning(f"Недостаточно данных для бэктестинга {ticker}: {len(prices) if prices else 0} свечей, требуется {required_length}")
            return {"profit": 0, "trades": 0}

        balance = 100000
        position = 0
        total_trades = 0
        entry_price = 0

        for i in range(35, len(prices)):
            window = prices[i-35:i]
            rsi = self.calculate_rsi(window)
            macd, signal, histogram = self.calculate_macd(window)
            sma, upper_band, lower_band = self.calculate_bollinger_bands(window)
            
            if rsi is None or macd is None or signal is None or sma is None:
                logger.warning(f"Не удалось рассчитать индикаторы для {ticker} на итерации {i}")
                continue

            current_price = prices[i]
            if rsi < 30 and histogram > 0 and current_price < lower_band:
                quantity = min(int(balance // current_price), 10)
                if quantity > 0:
                    cost = quantity * current_price
                    balance -= cost
                    position += quantity
                    entry_price = current_price
                    total_trades += 1

            elif position > 0 and (rsi > 70 or current_price > upper_band or (current_price < entry_price * 0.95)):
                revenue = position * current_price
                balance += revenue
                position = 0
                total_trades += 1

        profit = balance - 100000
        logger.info(f"Backtest для {ticker}: Прибыль = {profit:.2f} RUB, Сделок = {total_trades}")
        return {"profit": profit, "trades": total_trades}

    async def calculate_daily_profit(self, session: AsyncSession, user_id: int) -> Dict:
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

        total_buy = sum(trade.total for trade in trades if trade.action == "buy")
        total_sell = sum(trade.total for trade in trades if trade.action == "sell")
        profit = total_sell - total_buy
        return {
            "total_trades": len(trades),
            "total_buy": total_buy,
            "total_sell": total_sell,
            "profit": profit
        }

    async def send_daily_profit_report(self, session: AsyncSession, user_id: int):
        stats = await self.calculate_daily_profit(session, user_id)
        today = datetime.utcnow().date()
        message = (
            f"📅 <b>Дневной отчёт ({today.strftime('%Y-%m-%d')}):</b>\n\n"
            f"🔄 Всего сделок: {stats['total_trades']}\n"
            f"📉 Покупки: {stats['total_buy']:.2f} RUB\n"
            f"📈 Продажи: {stats['total_sell']:.2f} RUB\n"
            f"📊 Прибыль: {stats['profit']:.2f} RUB"
        )
        await self.bot.send_message(user_id, message, parse_mode="HTML")

    async def stream_and_trade(self, user_id: int):
        logger.info(f"Запуск стриминга и торговли для пользователя {user_id}")
        self.status = "Запуск стриминга"
        self.running = True

        try:
            # Проверяем пользователя и токен
            async with async_session() as session:
                user_result = await session.execute(
                    select(User).where(User.user_id == user_id)
                )
                user = user_result.scalars().first()
                if not user or not user.tinkoff_token:
                    logger.error(f"Токен T-Invest API не найден для пользователя {user_id}")
                    self.status = "Ошибка: токен не найден"
                    await self.bot.send_message(user_id, "❌ Токен T-Invest API не найден. Установите его в меню настроек.")
                    return

            async with AsyncClient(user.tinkoff_token) as client:
                await self.debug_available_shares(client)

                accounts = await client.users.get_accounts()
                if not accounts.accounts:
                    logger.error(f"Счета не найдены для пользователя {user_id}")
                    self.status = "Ошибка: счёт не найден"
                    await self.bot.send_message(user_id, "❌ Счёт не найден. Проверьте токен T-Invest API.")
                    return
                account_id = accounts.accounts[0].id

                # Получаем список акций
                async with async_session() as session:
                    all_stocks_result = await session.execute(
                        select(Stock).where(Stock.figi_status != 'FAILED')
                    )
                    all_stocks = all_stocks_result.scalars().all()

                if not all_stocks:
                    logger.info("Нет доступных акций для торговли")
                    self.status = "Нет акций для анализа"
                    await self.bot.send_message(user_id, "📉 Нет доступных акций для торговли.")
                    return

                figis_to_subscribe = []
                async with async_session() as session:
                    for stock in all_stocks:
                        figi = stock.figi
                        if not figi:
                            figi = await self.update_figi(client, stock, session)
                            if not figi:
                                logger.warning(f"FIGI для {stock.ticker} не удалось обновить, пропускаем...")
                                continue

                        figis_to_subscribe.append(figi)

                        backtest_result = await self.backtest_strategy(stock.ticker, figi, client)
                        if backtest_result["profit"] < 0:
                            logger.warning(f"Стратегия убыточна для {stock.ticker} (прибыль: {backtest_result['profit']}), пропускаем...")
                            continue

                        await self.train_ml_model(stock.ticker, client, figi)

                if not figis_to_subscribe:
                    logger.info("Нет тикеров для подписки после backtesting")
                    self.status = "Нет подходящих тикеров"
                    await self.bot.send_message(user_id, "📉 Нет подходящих тикеров для торговли после тестирования стратегии.")
                    return

                self.status = "Подписка на свечи"
                subscribe_request = SubscribeCandlesRequest(
                    subscription_action=SubscriptionAction.SUBSCRIPTION_ACTION_SUBSCRIBE,
                    instruments=[
                        {
                            "figi": figi,
                            "interval": SubscriptionInterval.SUBSCRIPTION_INTERVAL_ONE_MINUTE
                        }
                        for figi in figis_to_subscribe
                    ]
                )

                # Инициализируем клиента стриминга
                self.streaming_client = client
                async with async_session() as session:
                    async for candle in client.market_data_stream.market_data_stream(subscribe_request):
                        if not self.running:
                            logger.info("Остановка стриминга")
                            break

                        if not candle.candle:
                            logger.warning("Получена пустая свеча, пропускаем...")
                            continue

                        figi = candle.candle.figi
                        stock_result = await session.execute(select(Stock).where(Stock.figi == figi))
                        stock = stock_result.scalars().first()
                        if not stock:
                            logger.warning(f"Акция с FIGI {figi} не найдена в базе")
                            continue

                        ticker = stock.ticker
                        current_price = candle.candle.close.units + candle.candle.close.nano / 1e9 if candle.candle.close else 0
                        candle_data = {
                            "close": current_price,
                            "high": candle.candle.high.units + candle.candle.high.nano / 1e9 if candle.candle.high else current_price,
                            "low": candle.candle.low.units + candle.candle.low.nano / 1e9 if candle.candle.low else current_price,
                            "time": candle.candle.time
                        }

                        if ticker not in self.historical_data:
                            self.historical_data[ticker] = []
                        self.historical_data[ticker].append(candle_data)
                        if len(self.historical_data[ticker]) > 100:
                            self.historical_data[ticker] = self.historical_data[ticker][-100:]
                            logger.info(f"Ограничен размер исторических данных для {ticker} до 100 свечей")

                        news = await self.fetch_news(ticker)
                        if self.is_negative_news(news):
                            logger.warning(f"Негативные новости для {ticker}, торговля приостановлена")
                            continue

                        portfolio = await client.operations.get_portfolio(account_id=account_id)
                        total_balance = portfolio.total_amount_currencies.units + portfolio.total_amount_currencies.nano / 1e9
                        positions = await client.operations.get_positions(account_id=account_id)
                        holdings = {pos.figi: pos.quantity.units for pos in positions.securities}

                        # Проверяем, достаточно ли данных для расчёта индикаторов
                        if ticker not in self.historical_data or not self.historical_data[ticker] or len(self.historical_data[ticker]) < 35:
                            logger.info(f"Недостаточно данных для {ticker}: {len(self.historical_data[ticker]) if self.historical_data.get(ticker) else 0} свечей, требуется 35. Ожидаем накопления данных...")
                            continue

                        prices = [c["close"] for c in self.historical_data[ticker][-35:]] if self.historical_data[ticker] else []
                        candles = [
                            type('Candle', (), {
                                "close": type('Price', (), {"units": int(c["close"]), "nano": int((c["close"] % 1) * 1e9)}),
                                "high": type('Price', (), {"units": int(c["high"]), "nano": int((c["high"] % 1) * 1e9)}),
                                "low": type('Price', (), {"units": int(c["low"]), "nano": int((c["low"] % 1) * 1e9)})
                            }) for c in self.historical_data[ticker][-35:]
                        ]

                        # Дополнительная проверка на длину списка candles
                        if not candles or len(candles) < 35:
                            logger.warning(f"Недостаточно свечей для расчёта индикаторов для {ticker}: {len(candles) if candles else 0} свечей, требуется 35")
                            continue

                        rsi = self.calculate_rsi(prices)
                        macd, signal, histogram = self.calculate_macd(prices)
                        atr = self.calculate_atr(candles)
                        sma, upper_band, lower_band = self.calculate_bollinger_bands(prices)
                        predicted_price = self.predict_price(ticker, prices)

                        if any(x is None for x in [rsi, macd, signal, atr, sma, predicted_price]):
                            logger.warning(f"Невозможно рассчитать индикаторы или предсказание для {ticker}")
                            continue

                        buy_signal = False
                        if (rsi is not None and macd is not None and signal is not None and sma is not None and predicted_price is not None and
                            rsi < 30 and histogram > 0 and current_price < lower_band and predicted_price > current_price * 1.02):
                            buy_signal = True
                            logger.info(f"Сигнал на покупку {ticker}: RSI={rsi:.2f}, MACD Histogram={histogram:.2f}, Bollinger Lower={lower_band:.2f}, Predicted Price={predicted_price:.2f}")

                        if buy_signal:
                            max_position_cost = total_balance * 0.1
                            quantity = min(int(max_position_cost // current_price), 10) if current_price > 0 else 0
                            if quantity <= 0:
                                logger.info(f"Недостаточно средств для покупки {ticker}")
                                continue
                            total_cost = quantity * current_price
                            if total_cost <= total_balance:
                                order_response = await client.orders.post_order(
                                    account_id=account_id,
                                    figi=figi,
                                    quantity=quantity,
                                    direction=OrderDirection.ORDER_DIRECTION_BUY,
                                    order_type=OrderType.ORDER_TYPE_MARKET
                                )
                                logger.info(f"Куплено {quantity} акций {ticker} по цене {current_price} для пользователя {user_id}")
                                self.status = f"Совершил покупку: {quantity} акций {ticker}"
                                await self.bot.send_message(user_id, f"📈 Куплено {quantity} акций {ticker} по цене {current_price} RUB")
                                trade = TradeHistory(
                                    user_id=user_id,
                                    ticker=ticker,
                                    action="buy",
                                    price=current_price,
                                    quantity=quantity,
                                    total=total_cost,
                                    created_at=datetime.utcnow()
                                )
                                session.add(trade)
                                await session.commit()

                                atr_multiplier = 2
                                stop_loss = current_price - (atr * atr_multiplier if atr else 0)
                                take_profit = current_price + (atr * atr_multiplier * 2 if atr else 0)
                                self.positions[figi] = {
                                    "entry_price": current_price,
                                    "quantity": quantity,
                                    "stop_loss": stop_loss,
                                    "take_profit": take_profit,
                                    "highest_price": current_price
                                }

                        available_to_sell = holdings.get(figi, 0)
                        if available_to_sell > 0 and figi in self.positions:
                            position = self.positions[figi]
                            entry_price = position["entry_price"]
                            highest_price = max(position["highest_price"], current_price)
                            position["highest_price"] = highest_price

                            trailing_stop = highest_price - (atr * 2 if atr else 0)
                            position["stop_loss"] = max(position["stop_loss"], trailing_stop)

                            sell_signal = False
                            if (rsi is not None and macd is not None and signal is not None and sma is not None and predicted_price is not None and
                                (rsi > 70 and histogram < 0 and current_price > upper_band) or
                                current_price >= position["take_profit"] or
                                current_price <= position["stop_loss"] or
                                predicted_price < current_price * 0.98):
                                sell_signal = True
                                logger.info(f"Сигнал на продажу {ticker}: RSI={rsi:.2f}, MACD Histogram={histogram:.2f}, Bollinger Upper={upper_band:.2f}, Predicted Price={predicted_price:.2f}")

                            if sell_signal:
                                quantity = min(available_to_sell, 10)
                                total_revenue = quantity * current_price
                                order_response = await client.orders.post_order(
                                    account_id=account_id,
                                    figi=figi,
                                    quantity=quantity,
                                    direction=OrderDirection.ORDER_DIRECTION_SELL,
                                    order_type=OrderType.ORDER_TYPE_MARKET
                                )
                                logger.info(f"Продано {quantity} акций {ticker} по цене {current_price} для пользователя {user_id}")
                                self.status = f"Совершил продажу: {quantity} акций {ticker}"
                                await self.bot.send_message(user_id, f"📉 Продано {quantity} акций {ticker} по цене {current_price} RUB")
                                trade = TradeHistory(
                                    user_id=user_id,
                                    ticker=ticker,
                                    action="sell",
                                    price=current_price,
                                    quantity=quantity,
                                    total=total_revenue,
                                    created_at=datetime.utcnow()
                                )
                                session.add(trade)
                                await session.commit()
                                if quantity == position["quantity"]:
                                    del self.positions[figi]
                                else:
                                    self.positions[figi]["quantity"] -= quantity

                        await asyncio.sleep(0.1)

        except Exception as e:
            logger.error(f"Ошибка стриминга и торговли для пользователя {user_id}: {str(e)}")
            self.status = f"Ошибка: {str(e)}"
            error_message = html.escape(str(e))
            await self.bot.send_message(user_id, f"❌ Ошибка автоторговли: {error_message}")
            raise

    def stop_streaming(self, user_id: int = None):
        self.running = False
        self.status = "Остановлен"
        
        # Закрываем стриминг, если он активен
        if self.streaming_client:
            try:
                self.streaming_client.__aexit__(None, None, None)  # Закрываем контекстный менеджер
                logger.info("Клиент стриминга закрыт")
            except Exception as e:
                logger.error(f"Ошибка при закрытии клиента стриминга: {e}")
            finally:
                self.streaming_client = None

        if user_id:
            if user_id in self.stream_tasks:
                task = self.stream_tasks[user_id]
                task.cancel()
                try:
                    asyncio.get_event_loop().run_until_complete(task)
                except asyncio.CancelledError:
                    logger.info(f"Задача стриминга для пользователя {user_id} отменена")
                del self.stream_tasks[user_id]
                logger.info(f"Стриминг остановлен для пользователя {user_id}")
        else:
            for user_id, task in list(self.stream_tasks.items()):
                task.cancel()
                try:
                    asyncio.get_event_loop().run_until_complete(task)
                except asyncio.CancelledError:
                    logger.info(f"Задача стриминга для пользователя {user_id} отменена")
                del self.stream_tasks[user_id]
            logger.info("Стриминг остановлен для всех пользователей")

    def get_status(self):
        return self.status