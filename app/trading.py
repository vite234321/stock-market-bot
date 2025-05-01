# app/trading.py
import logging
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Stock, Subscription, TradeHistory, User
from datetime import datetime, timedelta
from tinkoff.invest import AsyncClient, OrderDirection, OrderType, CandleInterval, InstrumentIdType
from tinkoff.invest.exceptions import InvestError
from aiogram import Bot
import html

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.status = "Ожидание"
        self.positions = {}  # Храним открытые позиции: {figi: {"entry_price": float, "quantity": int}}

    async def debug_available_shares(self, client: AsyncClient):
        """Отладочная функция для вывода доступных акций."""
        try:
            response = await client.instruments.shares()
            for instrument in response.instruments:
                if instrument.class_code == "TQBR":
                    logger.info(f"Доступный тикер: {instrument.ticker}, FIGI: {instrument.figi}, Название: {instrument.name}")
        except Exception as e:
            logger.error(f"Ошибка при получении списка акций: {e}")

    async def update_figi(self, client: AsyncClient, stock: Stock, session: AsyncSession):
        """Проверяет наличие FIGI в базе. Если его нет, пытается обновить."""
        if stock.figi:
            return stock.figi
        logger.warning(f"FIGI для {stock.ticker} отсутствует в базе, пытаемся обновить...")
        try:
            cleaned_ticker = stock.ticker.replace(".ME", "")
            response = await client.instruments.share_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                class_code="TQBR",
                id=cleaned_ticker
            )
            stock.figi = response.instrument.figi
            session.add(stock)
            await session.commit()
            logger.info(f"FIGI для {stock.ticker} обновлён: {stock.figi}")
            return stock.figi
        except InvestError as e:
            logger.error(f"Не удалось обновить FIGI для {stock.ticker}: {e}")
            return None

    def calculate_rsi(self, prices, period=14):
        """Рассчитывает RSI (Relative Strength Index)."""
        if len(prices) < period + 1:
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

    def calculate_macd(self, prices, fast_period=12, slow_period=26, signal_period=9):
        """Рассчитывает MACD и сигнальную линию."""
        if len(prices) < slow_period + signal_period:
            return None, None, None
        # Рассчитываем экспоненциальные скользящие средние (EMA)
        def ema(data, period):
            ema_values = []
            k = 2 / (period + 1)
            ema_values.append(sum(data[:period]) / period)  # Первое значение — простая средняя
            for i in range(period, len(data)):
                ema_value = data[i] * k + ema_values[-1] * (1 - k)
                ema_values.append(ema_value)
            return ema_values

        # EMA для быстрого и медленного периодов
        ema_fast = ema(prices, fast_period)
        ema_slow = ema(prices, slow_period)
        # MACD = EMA(fast) - EMA(slow)
        macd = [ema_fast[i] - ema_slow[i] for i in range(len(ema_fast))]
        # Сигнальная линия — EMA MACD
        signal = ema(macd, signal_period)
        # Гистограмма = MACD - Signal
        histogram = [macd[i + signal_period - 1] - signal[i] for i in range(len(signal))]
        return macd[-1], signal[-1], histogram[-1]

    def calculate_atr(self, candles, period=14):
        """Рассчитывает ATR (Average True Range) для оценки волатильности."""
        if len(candles) < period + 1:
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

    async def analyze_and_trade(self, session: AsyncSession, user_id: int):
        logger.info(f"Запуск анализа и торговли для пользователя {user_id}")
        self.status = "Анализирует рынок"
        logger.info(f"Статус бота для пользователя {user_id}: {self.status}")

        try:
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

                portfolio = await client.operations.get_portfolio(account_id=account_id)
                total_balance = portfolio.total_amount_currencies.units + portfolio.total_amount_currencies.nano / 1e9
                logger.info(f"Баланс пользователя {user_id}: {total_balance} RUB")

                positions = await client.operations.get_positions(account_id=account_id)
                holdings = {pos.figi: pos.quantity.units for pos in positions.securities}

                all_stocks_result = await session.execute(select(Stock))
                all_stocks = all_stocks_result.scalars().all()

                if not all_stocks:
                    logger.info("Нет доступных акций для торговли")
                    self.status = "Нет акций для анализа"
                    await self.bot.send_message(user_id, "📉 Нет доступных акций для торговли.")
                    return

                self.status = "Ищет возможности для торговли"
                logger.info(f"Статус бота для пользователя {user_id}: {self.status}")

                for stock in all_stocks:
                    figi = stock.figi
                    if not figi:
                        figi = await self.update_figi(client, stock, session)
                        if not figi:
                            logger.warning(f"FIGI для {stock.ticker} не удалось обновить, пропускаем...")
                            continue

                    end_date = datetime.utcnow()
                    start_date = end_date - timedelta(days=60)  # Больше данных для расчёта индикаторов
                    logger.info(f"Запрашиваем свечи для {stock.ticker} (FIGI: {figi})")
                    try:
                        candles = await client.market_data.get_candles(
                            figi=figi,
                            from_=start_date,
                            to=end_date,
                            interval=CandleInterval.CANDLE_INTERVAL_DAY
                        )
                        logger.info(f"Получено {len(candles.candles)} свечей для {stock.ticker}")
                    except Exception as e:
                        logger.error(f"Ошибка при получении свечей для {stock.ticker}: {e}")
                        continue

                    if not candles.candles:
                        logger.warning(f"Нет данных о свечах для {stock.ticker}")
                        continue

                    prices = [candle.close.units + candle.close.nano / 1e9 for candle in candles.candles]
                    if len(prices) < 35:  # Нужно достаточно данных для MACD (26 + 9)
                        logger.warning(f"Недостаточно данных для анализа {stock.ticker}")
                        continue

                    # Рассчитываем индикаторы
                    rsi = self.calculate_rsi(prices)
                    macd, signal, histogram = self.calculate_macd(prices)
                    atr = self.calculate_atr(candles.candles)
                    if rsi is None or macd is None or atr is None:
                        logger.warning(f"Невозможно рассчитать индикаторы для {stock.ticker}")
                        continue

                    current_price = stock.last_price
                    if not current_price:
                        logger.warning(f"Текущая цена для {stock.ticker} отсутствует")
                        continue

                    # Правила покупки
                    buy_signal = False
                    if rsi < 30 and histogram > 0:  # Перепроданность и бычье пересечение MACD
                        buy_signal = True
                        logger.info(f"Сигнал на покупку {stock.ticker}: RSI={rsi:.2f}, MACD Histogram={histogram:.2f}")

                    if buy_signal:
                        max_position_cost = total_balance * 0.1  # Не более 10% баланса на одну сделку
                        quantity = min(int(max_position_cost // current_price), 10)
                        if quantity <= 0:
                            logger.info(f"Недостаточно средств для покупки {stock.ticker}")
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
                            logger.info(f"Куплено {quantity} акций {stock.ticker} по цене {current_price} для пользователя {user_id}")
                            self.status = f"Совершил покупку: {quantity} акций {stock.ticker}"
                            await self.bot.send_message(user_id, f"📈 Куплено {quantity} акций {stock.ticker} по цене {current_price} RUB")
                            trade = TradeHistory(
                                user_id=user_id,
                                ticker=stock.ticker,
                                action="buy",
                                price=current_price,
                                quantity=quantity,
                                total=total_cost,
                                created_at=datetime.utcnow()
                            )
                            session.add(trade)
                            await session.commit()
                            self.positions[figi] = {"entry_price": current_price, "quantity": quantity}

                    # Правила продажи
                    available_to_sell = holdings.get(figi, 0)
                    if available_to_sell > 0 and figi in self.positions:
                        position = self.positions[figi]
                        entry_price = position["entry_price"]
                        profit_percent = (current_price - entry_price) / entry_price * 100
                        loss_percent = (entry_price - current_price) / entry_price * 100
                        atr_multiplier = 2  # Используем ATR для динамического стоп-лосса
                        dynamic_stop_loss = entry_price - atr * atr_multiplier
                        dynamic_take_profit = entry_price + atr * atr_multiplier * 2

                        sell_signal = False
                        if rsi > 70 and histogram < 0:  # Перекупленность и медвежье пересечение MACD
                            sell_signal = True
                            logger.info(f"Сигнал на продажу {stock.ticker}: RSI={rsi:.2f}, MACD Histogram={histogram:.2f}")
                        elif current_price >= dynamic_take_profit:  # Тейк-профит
                            sell_signal = True
                            logger.info(f"Сигнал на продажу {stock.ticker}: Достигнут тейк-профит {current_price} >= {dynamic_take_profit}")
                        elif current_price <= dynamic_stop_loss:  # Стоп-лосс
                            sell_signal = True
                            logger.info(f"Сигнал на продажу {stock.ticker}: Достигнут стоп-лосс {current_price} <= {dynamic_stop_loss}")

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
                            logger.info(f"Продано {quantity} акций {stock.ticker} по цене {current_price} для пользователя {user_id}")
                            self.status = f"Совершил продажу: {quantity} акций {stock.ticker}"
                            await self.bot.send_message(user_id, f"📉 Продано {quantity} акций {stock.ticker} по цене {current_price} RUB")
                            trade = TradeHistory(
                                user_id=user_id,
                                ticker=stock.ticker,
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

                    await asyncio.sleep(0.5)

                self.status = "Ожидание следующего цикла"
                logger.info(f"Статус бота для пользователя {user_id}: {self.status}")

        except Exception as e:
            logger.error(f"Ошибка автоторговли для пользователя {user_id}: {e}")
            self.status = f"Ошибка: {str(e)}"
            error_message = html.escape(str(e))
            await self.bot.send_message(user_id, f"❌ Ошибка автоторговли: {error_message}")
            raise

    def get_status(self):
        return self.status