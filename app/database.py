import os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

# Создаём движок с настройкой statement_cache_size=0
engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    connect_args={
        "statement_cache_size": 0,  # Отключаем кэширование prepared statements
        "server_settings": {"application_name": "stock-market-bot"}
    }
)

# Асинхронная сессия
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Синхронная сессия (для миграций и тестов)
sync_engine = create_engine(DATABASE_URL.replace("postgresql+asyncpg", "postgresql"))
SessionLocal = sessionmaker(sync_engine)

def get_session_pool():
    return async_session