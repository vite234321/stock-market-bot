import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from tinkoff.invest import AsyncClient, CandleInterval, OrderDirection
from app.models import Stock, User, TradeHistory

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.handlers = [h for h in logger.handlers if not isinstance(h, logging.StreamHandler)]

async def calculate_indicators(prices: List[float]) -> Tuple[List[float], List[float], List[float], List[float], List[float]]:
    if len(prices) < 26:
        return [0] * len(prices), [0] * len(prices), [0] * len(prices), [0] * len(prices), [0] * len(prices)

    # RSI (14 дней)
    rsi_values = [0] * len(prices)
    for i in range(14, len(prices)):
        gains = [max(0, prices[j] - prices[j-1]) for j in range(i-13, i+1)]
        losses = [max(0, prices[j-1] - prices[j]) for j in range(i-13, i+1)]
        avg_gain = sum(gains) / 14 if gains else 0
        avg_loss = sum(losses) / 14 if losses else 0
        rs = avg_gain / avg_loss if avg_loss else float('inf')
        rsi = 100 - (100 / (1 + rs)) if rs != float('inf') else 100
        rsi_values[i] = rsi

    # MACD (EMA 12, 26, Signal 9)
    ema_12_values = [0] * len(prices)
    ema_26_values = [0] * len(prices)
    macd_values = [0] * len(prices)
    signal_values = [0] * len(prices)
    k_12 = 2 / (12 + 1)
    k_26 = 2 / (26 + 1)
    k_signal = 2 / (9 + 1)

    # Начальные значения EMA
    ema_12_values[11] = sum(prices[:12]) / 12
    ema_26_values[25] = sum(prices[:26]) / 26

    for i in range(12, len(prices)):
        ema_12_values[i] = prices[i] * k_12 + ema_12_values[i-1] * (1 - k_12)
    for i in range(26, len(prices)):
        ema_26_values[i] = prices[i] * k_26 + ema_26_values[i-1] * (1 - k_26)

    for i in range(26, len(prices)):
        macd_values[i] = ema_12_values[i] - ema_26_values[i]

    signal_values[34] = sum(macd_values[26:35]) / 9
    for i in range(35, len(prices)):
        signal_values[i] = macd_values[i] * k_signal + signal_values[i-1] * (1 - k_signal)

    # Bollinger Bands (20 дней)
    upper_band_values = [0] * len(prices)
    lower_band_values = [0] * len(prices)
    for i in range(19, len(prices)):
        sma = sum(prices[i-19:i+1]) / 20
        std = (sum((p - sma) ** 2 for p in prices[i-19:i+1]) / 20) ** 0.5
        upper_band_values[i] = sma + 2 * std
        lower_band_values[i] = sma - 2 * std

    return rsi_values, macd_values, signal_values, upper_band_values, lower_band_values

class TradingBot:
    def __init__(self, bot):
        self.bot = bot
        self.stream_tasks: Dict[int, asyncio.Task] = {}

    async def calculate_daily_profit(self, session: AsyncSession, user_id: int) -> Dict:
        today = datetime.utcnow().date()
        start_of_day = datetime.combine(today, datetime.min.time())
        end_of_day = datetime.combine(today, datetime.max.time())

        total_trades_query = select(func.count(TradeHistory.id)).where(
            TradeHistory.user_id == user_id,
            TradeHistory.created_at.between(start_of_day, end_of_day)
        )
        total_trades = (await session.execute(total_trades_query)).scalar() or 0

        total_buy_query = select(func.sum(TradeHistory.total)).where(
            TradeHistory.user_id == user_id,
            TradeHistory.action == "buy",
            TradeHistory.created_at.between(start_of_day, end_of_day)
        )
        total_buy = (await session.execute(total_buy_query)).scalar() or 0

        total_sell_query = select(func.sum(TradeHistory.total)).where(
            TradeHistory.user_id == user_id,
            TradeHistory.action == "sell",
            TradeHistory.created_at.between(start_of_day, end_of_day)
        )
        total_sell = (await session.execute(total_sell_query)).scalar() or 0

        profit = total_sell - total_buy

        return {
            "total_trades": total_trades,
            "total_buy": total_buy,
            "total_sell": total_sell,
            "profit": profit
        }

    async def backtest_strategy(self, prices: List[float]) -> Dict:
        if len(prices) < 35:
            logger.warning("Недостаточно данных для бэктеста")
            return {"profit": 0, "trades": 0}

        total_profit = 0
        trades = 0
        position = None

        rsi, macd, signal, upper_band, lower_band = await calculate_indicators(prices)

        for i in range(35, len(prices)):
            if rsi[i] < 35 and macd[i] > signal[i] and not position:
                position = prices[i]
                trades += 1
                logger.debug(f"Бэктест: Покупка на цене {position} для i={i}")
            elif rsi[i] > 65 and position:
                total_profit += prices[i] - position
                position = None
                trades += 1
                logger.debug(f"Бэктест: Продажа на цене {prices[i]}, прибыль {total_profit}, i={i}")

        logger.info(f"Бэктест завершён: Прибыль = {total_profit:.2f} RUB, Сделок = {trades}")
        return {"profit": total_profit, "trades": trades}

    async def stream_and_trade(self, user_id: int, session: AsyncSession):
        from app.handlers import fetch_figi_with_retry
        try:
            user_result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = user_result.scalars().first()
            if not user or not user.tinkoff_token:
                logger.error(f"Токен T-Invest API не найден для пользователя {user_id}")
                return

            async with AsyncClient(user.tinkoff_token) as client:
                account_id = (await client.users.get_accounts()).accounts[0].id
                stocks_result = await session.execute(select(Stock))
                stocks = stocks_result.scalars().all()

                if not stocks:
                    logger.error(f"Нет подходящих тикеров для пользователя {user_id}")
                    return

                for stock in stocks:
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
                        from_=datetime.utcnow() - timedelta(days=90),
                        to=datetime.utcnow(),
                        interval=CandleInterval.CANDLE_INTERVAL_DAY
                    )
                    logger.info(f"Загружено {len(candles.candles)} свечей для {stock.ticker}")

                    prices = [candle.close.units + candle.close.nano / 1e9 for candle in candles.candles]
                    if len(prices) < 35:
                        logger.warning(f"Недостаточно данных для {stock.ticker}: {len(prices)} свечей")
                        continue

                    # Бэктест стратегии
                    backtest_result = await self.backtest_strategy(prices)
                    profit = backtest_result["profit"]
                    trades = backtest_result["trades"]
                    logger.info(f"Backtest для {stock.ticker}: Прибыль = {profit:.2f} RUB, Сделок = {trades}")

                    if profit <= 0:
                        logger.warning(f"Стратегия убыточна для {stock.ticker} (прибыль: {profit}), пропускаем...")
                        continue

                    # Вычисляем индикаторы
                    rsi, macd, signal, upper_band, lower_band = await calculate_indicators(prices)
                    logger.debug(f"Индикаторы для {stock.ticker}: RSI={rsi[-1]:.2f}, MACD={macd[-1]:.2f}, Signal={signal[-1]:.2f}, "
                                f"Upper Band={upper_band[-1]:.2f}, Lower Band={lower_band[-1]:.2f}, Current Price={prices[-1]:.2f}")

                    # Проверяем, достаточно ли данных для ML (временная заглушка)
                    indicators_data = []
                    for i in range(len(prices)):
                        if i < 35:
                            continue
                        indicators_data.append({
                            "rsi": rsi[i],
                            "macd": macd[i],
                            "signal": signal[i],
                            "price": prices[i],
                        })

                    if len(indicators_data) < 2:
                        logger.warning(f"Недостаточно данных для обучения ML: {len(indicators_data)} точек")
                        # Базовая стратегия
                        current_price = prices[-1]
                        if rsi[-1] < 35 and macd[-1] > signal[-1]:
                            logger.info(f"Покупка {stock.ticker} по базовой стратегии (RSI < 35, MACD > Signal)")
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
                            logger.info(f"Совершена покупка {stock.ticker} по цене {last_price_value:.2f} RUB")
                        elif rsi[-1] > 65:
                            logger.info(f"Продажа {stock.ticker} по базовой стратегии (RSI > 65)")
                            last_price = (await client.market_data.get_last_prices(figi=[stock.figi])).last_prices[0].price
                            last_price_value = last_price.units + last_price.nano / 1e9
                            order = await client.orders.post_order(
                                figi=stock.figi,
                                quantity=1,
                                price=last_price,
                                direction=OrderDirection.ORDER_DIRECTION_SELL,
                                account_id=account_id,
                                order_type="LIMIT"
                            )
                            trade = TradeHistory(
                                user_id=user_id,
                                ticker=stock.ticker,
                                action="sell",
                                quantity=1,
                                price=last_price_value,
                                total=last_price_value,
                                created_at=datetime.utcnow()
                            )
                            session.add(trade)
                            await session.commit()
                            logger.info(f"Совершена продажа {stock.ticker} по цене {last_price_value:.2f} RUB")
                        continue

                    logger.info(f"ML-модель обучена для {stock.ticker} (прибыль: {profit})")
                    # Здесь можно добавить логику для ML-торговли
        except Exception as e:
            logger.error(f"Ошибка в stream_and_trade для пользователя {user_id}: {e}")
        finally:
            await session.commit()

    def stop_streaming(self, user_id: int):
        if user_id in self.stream_tasks:
            self.stream_tasks[user_id].cancel()
            del self.stream_tasks[user_id]
            logger.info(f"Остановлен поток для пользователя {user_id}")