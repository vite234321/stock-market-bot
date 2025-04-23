import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Получение DATABASE_URL из переменной окружения
DATABASE_URL = os.getenv("DATABASE_URL")
logger.info(f"Используется DATABASE_URL: {DATABASE_URL}")

# Создание асинхронного движка
engine = create_async_engine(DATABASE_URL, echo=True)

# Создание фабрики сессий
session_pool = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

def get_session_pool():
    return session_pool