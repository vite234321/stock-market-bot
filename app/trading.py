# app/trading.py
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Stock, Subscription, TradeHistory, User
from datetime import datetime
from tinkoff.invest import AsyncClient, OrderDirection, OrderType

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TradingBot:
    async def analyze_and_trade(self, session: AsyncSession, user_id: int):
        logger.info(f"Запуск анализа и торговли для пользователя {user_id}")
        try:
            # Получаем токен пользователя
            user_result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = user_result.scalars().first()
            if not user or not user.tinkoff_token:
                logger.error(f"Токен T-Invest API не найден для пользователя {user_id}")
                return

            async with AsyncClient(user.tinkoff_token) as client:
                # Получаем информацию о счёте
                accounts = await client.users.get_accounts()
                if not accounts.accounts:
                    logger.error(f"Счета не найдены для пользователя {user_id}")
                    return
                account_id = accounts.accounts[0].id

                # Получаем баланс
                portfolio = await client.operations.get_portfolio(account_id=account_id)
                total_balance = portfolio.total_amount_currencies.units + portfolio.total_amount_currencies.nano / 1e9
                logger.info(f"Баланс пользователя {user_id}: {total_balance} RUB")

                # Получаем позиции (текущие активы)
                positions = await client.operations.get_positions(account_id=account_id)
                holdings = {pos.figi: pos.quantity.units for pos in positions.securities}

                # Получаем все акции для анализа
                all_stocks_result = await session.execute(select(Stock))
                all_stocks = all_stocks_result.scalars().all()

                if not all_stocks:
                    logger.info("Нет доступных акций для торговли")
                    return

                for stock in all_stocks:
                    # Получаем FIGI для акции (предполагаем, что ticker соответствует FIGI, в реальном проекте нужен маппинг)
                    figi = stock.ticker  # В реальном проекте нужно сопоставить ticker с FIGI через API

                    # Простая стратегия: покупаем, если цена ниже средней
                    avg_price = stock.last_price * 0.95
                    current_price = stock.last_price
                    volume = stock.volume if stock.volume else 0

                    if current_price < avg_price and volume > 10000:
                        quantity = min(int(total_balance // current_price), 10)
                        if quantity > 0:
                            total_cost = quantity * current_price
                            if total_cost <= total_balance:
                                # Выполняем покупку через T-Invest API
                                order_response = await client.orders.post_order(
                                    account_id=account_id,
                                    figi=figi,
                                    quantity=quantity,
                                    direction=OrderDirection.ORDER_DIRECTION_BUY,
                                    order_type=OrderType.ORDER_TYPE_MARKET
                                )
                                logger.info(f"Куплено {quantity} акций {stock.ticker} по цене {current_price} для пользователя {user_id}")
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

                    # Условие для продажи: цена выросла на 10% от средней
                    if current_price > avg_price * 1.10:
                        available_to_sell = holdings.get(figi, 0)
                        if available_to_sell > 0:
                            quantity = min(available_to_sell, 10)
                            total_revenue = quantity * current_price
                            # Выполняем продажу через T-Invest API
                            order_response = await client.orders.post_order(
                                account_id=account_id,
                                figi=figi,
                                quantity=quantity,
                                direction=OrderDirection.ORDER_DIRECTION_SELL,
                                order_type=OrderType.ORDER_TYPE_MARKET
                            )
                            logger.info(f"Продано {quantity} акций {stock.ticker} по цене {current_price} для пользователя {user_id}")
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

        except Exception as e:
            logger.error(f"Ошибка автоторговли для пользователя {user_id}: {e}")
            raise