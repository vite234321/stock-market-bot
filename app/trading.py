import logging
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from app.models import Stock, Subscription, TradeHistory, User
from app.database import async_session
from datetime import datetime, timedelta
import moexalgo
import numpy as np
from aiogram import Bot
from typing import Dict, List, Optional
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.status = "Ожидание"
        self.positions: Dict[str, Dict] = {}
        self.historical_data: Dict[str, List] = {}
        self.running = False
        self.stream_tasks: Dict[int, asyncio.Task] = {}
        self._log_counter: Dict[str, int] = {}

    async def fetch_moex_data(self, ticker: str, period: str = "1d", days: int = 30):
        try:
            client = moexalgo.MoexClient()
            candles = client.get_candles(ticker, period=period, limit=days)
            prices = [c['CLOSE'] for c in candles]
            return prices
        except Exception as e:
            logger.error(f"Ошибка при получении данных MOEX для {ticker}: {e}")
            return []

    async def place_order(self, ticker: str, quantity: int, action: str, token: str):
        try:
            client = moexalgo.TradingClient(token=token)
            price = await self.get_last_price(ticker)
            if action == "buy":
                order = client.buy_market(ticker, quantity, price)
            else:
                order = client.sell_market(ticker, quantity, price)
            logger.info(f"{action.capitalize()} order placed for {quantity} shares of {ticker} at {price}")
            return order
        except Exception as e:
            logger.error(f"Ошибка при размещении ордера для {ticker}: {e}")
            return None

    async def get_last_price(self, ticker: str):
        prices = await self.fetch_moex_data(ticker, days=1)
        return prices[-1] if prices else 0

    def calculate_rsi(self, prices: List[float], period: int = 7) -> Optional[float]:
        if not prices or len(prices) < period + 1:
            logger.debug(f"Недостаточно данных для расчёта RSI: {len(prices) if prices else 0} элементов")
            return None
        gains = [max(0, prices[i] - prices[i-1]) for i in range(1, len(prices))]
        losses = [max(0, prices[i-1] - prices[i]) for i in range(1, len(prices))]
        avg_gain = sum(gains[-period:]) / period if gains else 0
        avg_loss = sum(losses[-period:]) / period if losses else 0
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def calculate_bollinger_bands(self, prices: List[float], period: int = 20, std_dev: float = 2) -> tuple:
        if not prices or len(prices) < period:
            logger.debug(f"Недостаточно данных для Bollinger Bands: {len(prices) if prices else 0}")
            return None, None, None
        sma = sum(prices[-period:]) / period
        variance = sum((p - sma) ** 2 for p in prices[-period:]) / period
        std = np.sqrt(variance)
        upper_band = sma + std_dev * std
        lower_band = sma - std_dev * std
        return sma, upper_band, lower_band

    async def calculate_daily_profit(self, session: AsyncSession, user_id: int) -> Dict:
        today = datetime.utcnow().date()
        start_of_day = datetime.combine(today, datetime.min.time())
        end_of_day = datetime.combine(today, datetime.max.time())

        try:
            result = await session.execute(
                select(TradeHistory).where(
                    TradeHistory.user_id == user_id,
                    TradeHistory.created_at >= start_of_day,
                    TradeHistory.created_at <= end_of_day
                )
            )
            trades = result.scalars().all()
        except DBAPIError as e:
            logger.error(f"Ошибка SQL при получении торговой истории для {user_id}: {e}")
            return {"total_trades": 0, "total_buy": 0, "total_sell": 0, "profit": 0}

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
            async with async_session() as session:
                user_result = await session.execute(
                    select(User).where(User.user_id == user_id)
                )
                user = user_result.scalars().first()
                if not user or not user.moex_token:
                    logger.error(f"Пользователь {user_id} не найден или токен MOEX отсутствует")
                    self.status = "Ошибка: токен не найден"
                    await self.bot.send_message(user_id, "❌ Токен MOEX не найден. Установите его в настройках.")
                    return

                all_stocks_result = await session.execute(select(Stock))
                all_stocks = all_stocks_result.scalars().all()
                if not all_stocks:
                    logger.info("Нет доступных акций для торговли")
                    self.status = "Нет акций для анализа"
                    await self.bot.send_message(user_id, "📉 Нет доступных акций для торговли.")
                    return

                while self.running:
                    for stock in all_stocks:
                        ticker = stock.ticker
                        prices = await self.fetch_moex_data(ticker, days=30)
                        if len(prices) < 20:
                            logger.warning(f"Недостаточно данных для {ticker}: {len(prices)} свечей")
                            continue

                        rsi = self.calculate_rsi(prices)
                        sma, upper_band, lower_band = self.calculate_bollinger_bands(prices)
                        current_price = prices[-1]

                        if rsi is not None and sma is not None:
                            buy_signal = rsi < 30 and current_price < lower_band
                            sell_signal = rsi > 70 and current_price > upper_band

                            if buy_signal:
                                quantity = 1  # Простая логика для примера
                                order = await self.place_order(ticker, quantity, "buy", user.moex_token)
                                if order:
                                    trade = TradeHistory(
                                        user_id=user_id,
                                        ticker=ticker,
                                        action="buy",
                                        price=current_price,
                                        quantity=quantity,
                                        total=current_price * quantity,
                                        created_at=datetime.utcnow()
                                    )
                                    session.add(trade)
                                    await session.commit()
                                    await self.bot.send_message(user_id, f"📈 Куплено {quantity} акций {ticker} по {current_price:.2f} RUB")

                            elif sell_signal and ticker in self.positions:
                                quantity = self.positions[ticker]["quantity"]
                                order = await self.place_order(ticker, quantity, "sell", user.moex_token)
                                if order:
                                    total_revenue = quantity * current_price
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
                                    await self.bot.send_message(user_id, f"📉 Продано {quantity} акций {ticker} по {current_price:.2f} RUB")
                                    del self.positions[ticker]

                    await asyncio.sleep(60)  # Проверка каждую минуту

        except Exception as e:
            logger.error(f"Ошибка стриминга для {user_id}: {e}")
            self.status = f"Ошибка: {e}"
            await self.bot.send_message(user_id, f"❌ Ошибка автоторговли: {e}")

    def stop_streaming(self, user_id: int = None):
        self.running = False
        self.status = "Остановлен"
        if user_id:
            if user_id in self.stream_tasks:
                task = self.stream_tasks[user_id]
                task.cancel()
                try:
                    asyncio.get_event_loop().run_until_complete(task)
                except asyncio.CancelledError:
                    logger.info(f"Задача стриминга для {user_id} отменена")
                del self.stream_tasks[user_id]
        else:
            for user_id, task in list(self.stream_tasks.items()):
                task.cancel()
                try:
                    asyncio.get_event_loop().run_until_complete(task)
                except asyncio.CancelledError:
                    logger.info(f"Задача стриминга для {user_id} отменена")
                del self.stream_tasks[user_id]

    def get_status(self):
        return self.status