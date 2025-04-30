# app/trading.py
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models import Stock, Subscription, TradeHistory, UserBalance
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TradingBot:
    async def analyze_and_trade(self, session: AsyncSession, user_id: int):
        logger.info(f"Запуск анализа и торговли для пользователя {user_id}")
        try:
            # Получаем баланс пользователя
            balance_result = await session.execute(
                select(UserBalance).where(UserBalance.user_id == user_id)
            )
            user_balance = balance_result.scalars().first()
            if not user_balance:
                user_balance = UserBalance(user_id=user_id, balance=100000.0)
                session.add(user_balance)
                await session.commit()

            # Получаем все акции (не только по подпискам)
            all_stocks_result = await session.execute(select(Stock))
            all_stocks = all_stocks_result.scalars().all()

            if not all_stocks:
                logger.info("Нет доступных акций для торговли")
                return

            for stock in all_stocks:
                # Простая стратегия: покупаем, если цена ниже скользящего среднего
                # Для упрощения предположим, что у нас есть "средняя цена" (например, last_price за последние дни)
                avg_price = stock.last_price * 0.95  # Пример: покупаем, если цена на 5% ниже средней
                current_price = stock.last_price
                volume = stock.volume if stock.volume else 0

                # Условие для покупки: цена ниже средней и высокий объём торгов
                if current_price < avg_price and volume > 10000:
                    quantity = min(int(user_balance.balance // current_price), 10)  # Покупаем не более 10 акций
                    if quantity > 0:
                        total_cost = quantity * current_price
                        if total_cost <= user_balance.balance:
                            user_balance.balance -= total_cost
                            user_balance.updated_at = datetime.utcnow()
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
                            logger.info(f"Куплено {quantity} акций {stock.ticker} по цене {current_price} для пользователя {user_id}")
                            await session.commit()

                # Условие для продажи: цена выросла на 10% от средней
                if current_price > avg_price * 1.10:
                    # Проверяем, есть ли у пользователя акции для продажи
                    bought_trades = await session.execute(
                        select(TradeHistory).where(
                            TradeHistory.user_id == user_id,
                            TradeHistory.ticker == stock.ticker,
                            TradeHistory.action == "buy"
                        )
                    )
                    bought_trades = bought_trades.scalars().all()
                    total_bought = sum(trade.quantity for trade in bought_trades)

                    sold_trades = await session.execute(
                        select(TradeHistory).where(
                            TradeHistory.user_id == user_id,
                            TradeHistory.ticker == stock.ticker,
                            TradeHistory.action == "sell"
                        )
                    )
                    sold_trades = sold_trades.scalars().all()
                    total_sold = sum(trade.quantity for trade in sold_trades)

                    available_to_sell = total_bought - total_sold
                    if available_to_sell > 0:
                        quantity = min(available_to_sell, 10)  # Продаём не более 10 акций
                        total_revenue = quantity * current_price
                        user_balance.balance += total_revenue
                        user_balance.updated_at = datetime.utcnow()
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
                        logger.info(f"Продано {quantity} акций {stock.ticker} по цене {current_price} для пользователя {user_id}")
                        await session.commit()

        except Exception as e:
            logger.error(f"Ошибка автоторговли для пользователя {user_id}: {e}")
            raise