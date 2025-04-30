# app/models.py
from sqlalchemy import Column, Integer, String, Float, BigInteger, DateTime, Boolean
from app.database import Base
from datetime import datetime

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, unique=True, index=True)
    tinkoff_token = Column(String, nullable=True)
    autotrading_enabled = Column(Boolean, default=False)  # Новое поле
    created_at = Column(DateTime, default=datetime.utcnow)

class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, unique=True, index=True)
    name = Column(String)
    last_price = Column(Float, nullable=True)
    volume = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    ticker = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    signal_type = Column(String)
    value = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)