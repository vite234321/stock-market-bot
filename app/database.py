import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from contextlib import asynccontextmanager

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Получаем DATABASE_URL и явно указываем asyncpg
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.error("Переменная DATABASE_URL не установлена")
    raise ValueError("DATABASE_URL не установлен")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif not DATABASE_URL.startswith("postgresql+asyncpg://"):
    logger.warning(f"Некорректный формат DATABASE_URL: {DATABASE_URL}. Попытка исправить...")
    DATABASE_URL = f"postgresql+asyncpg://{DATABASE_URL.split('://')[1]}"

logger.info(f"Используется DATABASE_URL: {DATABASE_URL}")

try:
    engine = create_async_engine(DATABASE_URL, echo=True, future=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
except Exception as e:
    logger.error(f"Не удалось создать асинхронный движок: {e}")
    raise

@asynccontextmanager
async def get_db():
    async with async_session() as session:
        try:
            yield session
        except Exception as e:
            logger.error(f"Ошибка сессии базы данных: {e}")
            await session.rollback()
            raise
        finally:
            await session.close()
