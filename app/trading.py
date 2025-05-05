from aiogram import Bot
from sqlalchemy import select
from app.models import User, Stock, Subscription
from tinkoff.invest import AsyncClient, CandleInterval, MarketDataStreamManager, MarketDataStreamResult, SubscribeCandlesRequest
from tinkoff.invest.exceptions import RequestError
import asyncio
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from functools import wraps
import platform

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
                    logger.warning(f"Попытка {attempt + 1}/{max_attempts} не удалась для {func.__name__}: {e}, повторяем...")
                    await asyncio.sleep(delay)
            return None
        return wrapper
    return decorator

class TradingBot:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.stream_manager = MarketDataStreamManager()
        self.stream_tasks: dict[str, asyncio.Task] = {}
        self.stop_events: dict[str, asyncio.Event] = {}

    async def train_ml_model(self, ticker: str, client: AsyncClient, figi: str):
        """Обучает ML-модель на основе исторических данных."""
        try:
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=90)
            candles = await self.get_candles_with_retry(client, figi, start_date, end_date, CandleInterval.CANDLE_INTERVAL_DAY)
            if not candles:
                logger.warning(f"Нет данных для обучения ML для {ticker}")
                return

            prices = [candle.close.units + candle.close.nano / 1e9 for candle in candles]
            df = pd.DataFrame(prices, columns=['close'])
            # Простая модель (пример): предсказание следующей цены на основе скользящего среднего
            df['prediction'] = df['close'].rolling(window=5).mean().shift(-1)
            logger.info(f"ML-модель обучена для {ticker} с {len(df)} точками данных")
        except Exception as e:
            logger.error(f"Ошибка при обучении ML для {ticker}: {e}")

    @retry(max_attempts=3, delay=10)
    async def get_candles_with_retry(self, client, figi, start_date, end_date, interval):
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

    def is_negative_news(self, title: str, description: str) -> bool:
        negative_keywords = ["падение", "кризис", "убытки", "санкции", "проблемы"]
        negative_count = sum(1 for keyword in negative_keywords if keyword in title.lower() or keyword in description.lower())
        if negative_count >= 2:
            logger.warning(f"Обнаружены негативные новости: {title}")
            return True
        return False

    async def backtest_strategy(self, client: AsyncClient, figi: str) -> dict:
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=30)
        candles = await self.get_candles_with_retry(client, figi, start_date, end_date, CandleInterval.CANDLE_INTERVAL_DAY)
        if not candles:
            return {"profit": 0}

        prices = [candle.close.units + candle.close.nano / 1e9 for candle in candles]
        initial_price = prices[0]
        final_price = prices[-1]
        profit = (final_price - initial_price) * 100 / initial_price  # Простой расчёт прибыли в процентах
        return {"profit": profit}

    async def stream_and_trade(self, user_id: str):
        if user_id in self.stream_tasks and not self.stream_tasks[user_id].done():
            logger.info(f"Стриминг уже запущен для пользователя {user_id}")
            return

        self.stop_events[user_id] = asyncio.Event()
        async with self.bot["db_session"]() as session:
            try:
                user_result = await session.execute(
                    select(User).where(User.user_id == user_id)
                )
                user = user_result.scalars().first()
                if not user or not user.tinkoff_token or not user.autotrading_enabled:
                    logger.warning(f"Недостаточно данных для стриминга для {user_id}")
                    return

                async with AsyncClient(user.tinkoff_token) as client:
                    subscription_result = await session.execute(
                        select(Subscription.ticker, Stock.figi).join(Stock).where(Subscription.user_id == user.id)
                    )
                    subscriptions = subscription_result.all()
                    figis_to_subscribe = [row.figi for row in subscriptions if row.figi]
                    if not figis_to_subscribe:
                        logger.warning(f"Нет акций для стриминга для пользователя {user_id}")
                        return

                    logger.info(f"Подписываемся на {len(figis_to_subscribe)} FIGI для пользователя {user_id}: {figis_to_subscribe}")
                    request = SubscribeCandlesRequest(
                        subscription_action="SUBSCRIPTION_ACTION_SUBSCRIBE",
                        instruments=[{"figi": figi, "interval": CandleInterval.CANDLE_INTERVAL_1_MIN} for figi in figis_to_subscribe]
                    )

                    async with self.stream_manager as stream:
                        await stream.candles.subscribe(request)

                        async for data in stream:
                            if isinstance(data, MarketDataStreamResult):
                                candle = data.candle
                                figi = candle.figi
                                stock_result = await session.execute(
                                    select(Stock).where(Stock.figi == figi)
                                )
                                stock = stock_result.scalars().first()
                                if not stock:
                                    continue

                                price = candle.close.units + candle.close.nano / 1e9
                                stock.last_price = price
                                session.add(stock)

                                backtest_result = await self.backtest_strategy(client, figi)
                                if backtest_result["profit"] < -100:  # Более мягкий порог
                                    logger.warning(f"Стратегия убыточна для {stock.ticker} (прибыль: {backtest_result['profit']}), пропускаем...")
                                    continue

                                await self.train_ml_model(stock.ticker, client, figi)

                                if self.is_negative_news("Тест новости", "Падение и кризис"):
                                    logger.warning(f"Пропущена торговля для {stock.ticker} из-за негативных новостей")
                                    continue

                                # Логика торговли (пример)
                                if price > stock.last_price * 1.01:  # Рост на 1%
                                    logger.info(f"Сигнал на покупку для {stock.ticker} по цене {price}")
                                    # Здесь должна быть логика покупки
                                elif price < stock.last_price * 0.99:  # Падение на 1%
                                    logger.info(f"Сигнал на продажу для {stock.ticker} по цене {price}")
                                    # Здесь должна быть логика продажи

                                await session.commit()
                            if self.stop_events[user_id].is_set():
                                break

            except Exception as e:
                logger.error(f"Ошибка в стриминге для {user_id}: {e}")
                await session.rollback()
            finally:
                if user_id in self.stream_tasks:
                    self.stream_tasks[user_id].cancel()
                    del self.stream_tasks[user_id]
                if user_id in self.stop_events:
                    del self.stop_events[user_id]

    async def send_daily_profit_report(self, session: AsyncSession, user_id: str):
        try:
            user_result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            user = user_result.scalars().first()
            if not user or not user.tinkoff_token:
                logger.warning(f"Не удалось отправить отчёт для {user_id}: нет токена или пользователя")
                return

            subscription_result = await session.execute(
                select(Subscription.ticker, Stock.last_price).join(Stock).where(Subscription.user_id == user.id)
            )
            subscriptions = subscription_result.all()
            if not subscriptions:
                await self.bot.send_message(user_id, "У вас нет подписок на акции для отчёта.")
                return

            total_profit = 0
            report = ["<b>Ежедневный отчёт о прибыли</b>\n"]
            for ticker, last_price in subscriptions:
                if last_price:
                    report.append(f"{ticker}: {last_price:.2f} RUB")
                    # Простой расчёт прибыли (пример)
                    total_profit += last_price * 0.01  # Предположим 1% прибыли
            report.append(f"<b>Общая прибыль: {total_profit:.2f} RUB</b>")
            await self.bot.send_message(user_id, "\n".join(report), parse_mode="HTML")
            logger.info(f"Отчёт отправлен пользователю {user_id}: {total_profit:.2f} RUB")
        except Exception as e:
            logger.error(f"Ошибка при отправке отчёта для {user_id}: {e}")

    def stop_streaming_for_user(self, user_id: str):
        if user_id in self.stop_events:
            self.stop_events[user_id].set()
            logger.info(f"Остановка стриминга для пользователя {user_id}")
        if user_id in self.stream_tasks and not self.stream_tasks[user_id].done():
            self.stream_tasks[user_id].cancel()
            logger.info(f"Задача стриминга для {user_id} отменена")

    def stop_streaming(self):
        for user_id in list(self.stop_events.keys()):
            self.stop_streaming_for_user(user_id)
        logger.info("Все стриминговые задачи остановлены")

async def main():
    setup()  # Инициализация бота и других компонентов
    while True:
        update_loop()  # Обновление состояния игры/бота
        await asyncio.sleep(1.0 / 60)  # Управление частотой обновления

if platform.system() == "Emscripten":
    asyncio.ensure_future(main())
else:
    if __name__ == "__main__":
        asyncio.run(main())