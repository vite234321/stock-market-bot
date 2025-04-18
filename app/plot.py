import logging
import asyncio
import yfinance as yf
import matplotlib.pyplot as plt
from io import BytesIO
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
async def generate_price_plot(ticker: str):
    for attempt in range(1, 4):
        try:
            stock = yf.Ticker(ticker)
            data = stock.history(period="1mo")
            if data.empty:
                logger.warning(f"No data for {ticker} on attempt {attempt}")
                if attempt == 3:
                    return None
                await asyncio.sleep(2)
                continue
            plt.figure(figsize=(10, 5))
            plt.plot(data.index, data["Close"], label="Цена закрытия")
            plt.title(f"Цена {ticker} за последний месяц")
            plt.xlabel("Дата")
            plt.ylabel("Цена (RUB)")
            plt.legend()
            buffer = BytesIO()
            plt.savefig(buffer, format="png")
            buffer.seek(0)
            plt.close()
            logger.info(f"Plot generated for {ticker}")
            return buffer
        except Exception as e:
            logger.error(f"Error generating plot for {ticker} on attempt {attempt}: {e}")
            if attempt == 3:
                return None
            await asyncio.sleep(2)
