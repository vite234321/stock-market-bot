from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не установлен в переменных окружения")

# Создаём асинхронный движок с отключённым кэшем подготовленных запросов
engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_pre_ping=True,
    connect_args={"statement_cache_size": 0}  # Отключаем кэш подготовленных запросов
)

async_session = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession
)

async def init_db():
    logger.info("Инициализация базы данных...")
    async with engine.begin() as conn:
        # Здесь можно добавить создание таблиц, если нужно
        from app.models import Base
        await conn.run_sync(Base.metadata.create_all)
    logger.info("База данных успешно инициализирована.")

async def dispose_engine():
    logger.info("Закрытие соединения с базой данных...")
    await engine.dispose()
    logger.info("Соединение с базой данных закрыто")