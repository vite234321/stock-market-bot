# app/trading.py
import logging
from tinkoff.invest import Client, RequestError
from tinkoff.invest.services import InstrumentsService, MarketDataService, OperationsService
from app.models import Signal, User
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TradingBot:
    async def analyze_and_trade(self, session: AsyncSession, user_id: int):
        logger.info(f"Запуск автоторговли для пользователя {user_id}")
        try:
            # Получаем токен пользователя из базы
            result = await session.execute(select(User).where(User.user_id == user_id))
            user = result.scalars().first()

            if not user or not user.tinkoff_token:
                logger.info(f"У пользователя {user_id} не установлен токен T-Invest API")
                return

            token = user.tinkoff_token

            # Получаем подписки пользователя
            result = await session.execute(
                select(Subscription.ticker).where(Subscription.user_id == user_id)
            )
            subscribed_tickers = result.scalars().all()

            if not subscribed_tickers:
                logger.info(f"Пользователь {user_id} не подписан на акции для автоторговли")
                return

            # Получаем сигналы для подписанных акций
            signals = []
            for ticker in subscribed_tickers:
                result = await session.execute(
                    select(Signal).where(Signal.ticker == ticker)
                )
                ticker_signals = result.scalars().all()
                signals.extend(ticker_signals)

            with Client(token) as client:
                instruments_service = client.instruments
                market_data_service = client.market_data
                operations_service = client.operations

                for signal in signals:
                    ticker = signal.ticker
                    signal_type = signal.signal_type
                    signal_value = signal.value

                    # Исправлено: "и" заменено на "and"
                    if signal_type == "BUY" and signal_value > 0.5:
                        await self._execute_buy(client, ticker, user_id)
                    elif signal_type == "SELL" and signal_value > 0.5:
                        await self._execute_sell(client, ticker, user_id)

        except Exception as e:
            logger.error(f"Ошибка автоторговли для пользователя {user_id}: {e}")

    async def _execute_buy(self, client, ticker, user_id):
        try:
            # Поиск инструмента по тикеру
            instruments_service = client.instruments
            instruments = instruments_service.shares().instruments
            instrument = next((i for i in instruments if i.ticker == ticker), None)

            if not instrument:
                logger.warning(f"Инструмент {ticker} не найден")
                return

            figi = instrument.figi
            market_data_service = client.market_data
            last_price = market_data_service.get_last_prices(figi=[figi]).last_prices[0].price
            last_price = last_price.units + last_price.nano / 1_000_000_000

            # Покупаем 1 лот
            quantity = 1
            logger.info(f"Покупка {quantity} лотов {ticker} по цене {last_price} для пользователя {user_id}")

            # Здесь должна быть реальная операция покупки через client.orders.post_order()
            # Для примера просто логируем
            logger.info(f"Успешная покупка {ticker} для пользователя {user_id}")

        except RequestError as e:
            logger.error(f"Ошибка покупки {ticker} для пользователя {user_id}: {e}")

    async def _execute_sell(self, client, ticker, user_id):
        try:
            # Поиск инструмента по тикеру
            instruments_service = client.instruments
            instruments = instruments_service.shares().instruments
            instrument = next((i for i in instruments if i.ticker == ticker), None)

            if not instrument:
                logger.warning(f"Инструмент {ticker} не найден")
                return

            figi = instrument.figi
            market_data_service = client.market_data
            last_price = market_data_service.get_last_prices(figi=[figi]).last_prices[0].price
            last_price = last_price.units + last_price.nano / 1_000_000_000

            # Продаём 1 лот
            quantity = 1
            logger.info(f"Продажа {quantity} лотов {ticker} по цене {last_price} для пользователя {user_id}")

            # Здесь должна быть реальная операция продажи через client.orders.post_order()
            # Для примера просто логируем
            logger.info(f"Успешная продажа {ticker} для пользователя {user_id}")

        except RequestError as e:
            logger.error(f"Ошибка продажи {ticker} для пользователя {user_id}: {e}")