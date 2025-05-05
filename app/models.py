from sqlalchemy import Column, Integer, String, Boolean, Float, Enum, ForeignKey, DateTime
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
import enum
from datetime import datetime
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Определение перечисления для статуса FIGI
class FigiStatus(enum.Enum):
    PENDING = "pending"    # Ожидание получения FIGI
    SUCCESS = "success"    # FIGI успешно получен
    FAILED = "failed"      # Не удалось получить FIGI

# База для всех моделей
class Base(AsyncAttrs):
    pass

# Модель пользователя
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)  # ID пользователя в Telegram
    username: Mapped[str] = mapped_column(String, nullable=True)              # Имя пользователя (опционально)
    tinkoff_token: Mapped[str] = mapped_column(String, nullable=True)         # Токен Tinkoff API
    autotrading_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # Включена ли автоторговля
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    stocks: Mapped[list["Stock"]] = relationship(back_populates="user")

    def __repr__(self):
        return f"<User(user_id={self.user_id}, username={self.username}, autotrading={self.autotrading_enabled})>"

# Модель подписки на акции
class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(String, nullable=False, unique=True)  # Тикер акции
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="subscriptions")

    def __repr__(self):
        return f"<Subscription(user_id={self.user_id}, ticker={self.ticker})>"

# Модель акций
class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(String, nullable=False, unique=True)  # Тикер акции
    figi: Mapped[str] = mapped_column(String, nullable=True)                  # FIGI от Tinkoff
    figi_status: Mapped[str] = mapped_column(PgEnum(FigiStatus, name="figi_status"), default=FigiStatus.PENDING.value)
    last_price: Mapped[float] = mapped_column(Float, nullable=True)           # Последняя цена
    updated_at: Mapped[datetime] = mapped_column(DateTime, onupdate=datetime.utcnow, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="stocks")

    def set_figi_status(self, status: FigiStatus):
        """Устанавливает статус FIGI и логирует изменение."""
        self.figi_status = status.value
        logger.info(f"Статус FIGI для {self.ticker} изменён на {status.value}")

    def __repr__(self):
        return f"<Stock(ticker={self.ticker}, figi={self.figi}, status={self.figi_status})>"