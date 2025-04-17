from sqlalchemy import Column, Integer, String, BigInteger, DateTime
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime

class Base(AsyncAttrs, DeclarativeBase):
    pass

class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    ticker = Column(String, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow)
