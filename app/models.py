# app/models.py
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime
import enum

class Base(AsyncAttrs, DeclarativeBase):
    pass

# Константы для FIGI статуса (используем в коде для проверки)
class FigiStatus(enum.Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"

class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, unique=True, index=True)
    name = Column(String)
    last_price = Column(Float)
    volume = Column(Float)
    updated_at = Column(DateTime, default=datetime.utcnow)
    figi = Column(String, nullable=True)
    figi_status = Column(String, default=FigiStatus.PENDING.value)  # Изменено на String

    # Метод для проверки корректности значения figi_status
    def set_figi_status(self, status: FigiStatus):
        if status not in FigiStatus:
            raise ValueError(f"Недопустимое значение figi_status: {status}. Допустимые значения: {[e.value for e in FigiStatus]}")
        self.figi_status = status.value

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, index=True)
    tinkoff_token = Column(String, nullable=True)
    autotrading_enabled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    ticker = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class TradeHistory(Base):
    __tablename__ = "trade_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    ticker = Column(String)
    action = Column(String)  # "buy" or "sell"
    price = Column(Float)
    quantity = Column(Integer)
    total = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    signal_type = Column(String)
    value = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)