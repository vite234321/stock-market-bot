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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.status = "Ожидание"

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
        """Обновляет FIGI для акции через Tinkoff API, если его нет в базе."""
        try:
            # Исправляем тикер, убирая .ME
            original_ticker = stock.ticker
            stock.ticker = stock.ticker.replace('.ME', '')
            if original_ticker != stock.ticker:
                logger.info(f"Исправлен тикер: {original_ticker} -> {stock.ticker}")
                session.add(stock)
                await session.commit()

            response = await client.instruments.share_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,  # Исправлено
                class_code="TQBR",
                id=stock.ticker
            )
            stock.figi = response.instrument.figi
            session.add(stock)
            await session.commit()
            logger.info(f"FIGI для {stock.ticker} обновлён: {stock.figi}")
            return stock.figi
        except InvestError as e:
            if "NOT_FOUND" in str(e):
                logger.error(f"Инструмент {stock.ticker} не найден в API")
                return None
            elif "RESOURCE_EXHAUSTED" in str(e):
                reset_time = int(e.metadata.ratelimit_reset) if e.metadata.ratelimit_reset else 60
                logger.warning(f"Достигнут лимит запросов API, ожидание {reset_time} секунд...")
                await asyncio.sleep(reset_time)
                response = await client.instruments.share_by(
                    id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,  # Исправлено
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
                # Добавляем отладочный вызов для проверки доступных акций
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
                await self.bot.send_message(user_id, "🔍 Бот ищет возможности для торговли...")

                for stock in all_stocks:
                    figi = stock.figi
                    if not figi:
                        logger.warning(f"FIGI для {stock.ticker} отсутствует в базе, пытаемся обновить...")
                        figi = await self.update_figi(client, stock, session)
                        if not figi:
                            logger.warning(f"Не удалось получить FIGI для {stock.ticker}, пропускаем...")
                            continue

                    end_date = datetime.utcnow()
                    start_date = end_date - timedelta(days=30)
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
                    if len(prices) < 5:
                        logger.warning(f"Недостаточно данных для анализа {stock.ticker}")
                        continue

                    sma = sum(prices[-5:]) / 5
                    current_price = stock.last_price
                    volume = stock.volume if stock.volume else 0

                    trend_up = prices[-1] > prices[-2] > prices[-3]
                    if current_price < sma and trend_up and volume > 10000:
                        quantity = min(int(total_balance // current_price), 10)
                        if quantity > 0:
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

                    trend_down = prices[-1] < prices[-2] < prices[-3]
                    if current_price > sma * 1.10 and trend_down:
                        available_to_sell = holdings.get(figi, 0)
                        if available_to_sell > 0:
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

                    await asyncio.sleep(0.5)

                self.status = "Ожидание следующего цикла"
                logger.info(f"Статус бота для пользователя {user_id}: {self.status}")
                await self.bot.send_message(user_id, "⏳ Ожидание следующего цикла торговли...")

        except Exception as e:
            logger.error(f"Ошибка автоторговли для пользователя {user_id}: {e}")
            self.status = f"Ошибка: {str(e)}"
            await self.bot.send_message(user_id, f"❌ Ошибка автоторговли: {str(e)}")
            raise

    def get_status(self):
        return self.status