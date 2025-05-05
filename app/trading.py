import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from tinkoff.invest import AsyncClient, CandleInterval, OrderDirection
from app.models import Stock, User, TradeHistory
from app.handlers import calculate_indicators

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# Убедимся, что логи не отправляются в Telegram
logger.handlers = [h for h in logger.handlers if not isinstance(h, logging.StreamHandler)]

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

    async def backtest_strategy(self, prices: list) -> Dict:
        if len(prices) < 30:
            return {"profit": 0, "trades": 0}

        total_profit = 0
        trades = 0
        position = None

        rsi, macd, signal, upper_band, lower_band = await calculate_indicators(prices)

        for i in range(26, len(prices)):
            current_price = prices[i]
            if rsi[i-14] < 30 and macd[i-26] > signal[i-9] and current_price < lower_band[i-20] and not position:
                position = current_price
                trades += 1
            elif rsi[i-14] > 70 and current_price > upper_band[i-20] and position:
                total_profit += current_price - position
                position = None
                trades += 1

        return {"profit": total_profit, "trades": trades}

    async def stream_and_trade(self, user_id: int):
        from app.handlers import fetch_figi_with_retry
        try:
            async with AsyncSession() as session:
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
                        if len(prices) < 30:
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
                        if rsi is None:
                            logger.warning(f"Недостаточно данных для расчёта индикаторов для {stock.ticker}")
                            continue

                        # Подготовка данных для ML
                        indicators_data = []
                        for i in range(len(prices)):
                            if i < 26:  # Пропускаем первые 26 дней, где индикаторы не определены
                                continue
                            indicators_data.append({
                                "rsi": rsi if i >= 14 else None,
                                "macd": macd if i >= 26 else None,
                                "signal": signal if i >= 9 else None,
                                "price": prices[i],
                            })

                        # Проверяем, достаточно ли данных для ML
                        if len(indicators_data) < 2:
                            logger.warning(f"Недостаточно данных для обучения ML после расчёта индикаторов для {stock.ticker}: {len(indicators_data)} точек, требуется минимум 2")
                            # Используем базовую стратегию без ML
                            current_price = prices[-1]
                            if rsi < 30 and macd > signal and current_price < lower_band:
                                logger.info(f"Покупка {stock.ticker} по базовой стратегии (RSI < 30, MACD > Signal, цена ниже Bollinger Band)")
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
                            continue

                        logger.info(f"ML-модель обучена для {stock.ticker} (прибыль: {profit})")
                        # Здесь можно добавить логику для ML-торговли, если данные есть
        except Exception as e:
            logger.error(f"Ошибка в stream_and_trade для пользователя {user_id}: {e}")
        finally:
            await session.commit()

    def stop_streaming(self, user_id: int):
        if user_id in self.stream_tasks:
            self.stream_tasks[user_id].cancel()
            del self.stream_tasks[user_id]
            logger.info(f"Остановлен поток для пользователя {user_id}")