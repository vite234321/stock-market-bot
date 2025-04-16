from sqlalchemy import Column, Integer, String, BigInteger
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

class Base(AsyncAttrs, DeclarativeBase):
    pass

class Subscription(Base):
    __tablename__ = "subscriptions"  # Добавлено
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    ticker = Column(String, index=True)
