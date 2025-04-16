import yfinance as yf
import matplotlib.pyplot as plt
from io import BytesIO

def generate_price_plot(ticker: str):
    try:
        stock = yf.Ticker(ticker)
        data = stock.history(period="1mo")
        if data.empty:
            raise ValueError(f"Нет данных для {ticker}")
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
        return buffer
    except Exception as e:
        print(f"Ошибка при создании графика для {ticker}: {e}")
        return None