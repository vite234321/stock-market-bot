import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from tenacity import retry, stop_after_attempt, wait_fixed
from fastapi import Depends

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Получение DATABASE_URL
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.error("Переменная DATABASE_URL не установлена")
    raise ValueError("DATABASE_URL не установлен")

# Исправление формата DATABASE_URL
if DATABASE_URL.startswith("postgres://"):
    logger.warning(f"Некорректный формат DATABASE_URL: {DATABASE_URL}. Попытка исправить...")
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

logger.info(f"Используется DATABASE_URL: {DATABASE_URL}")

# Создание асинхронного движка с повторными попытками
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def init_engine():
    try:
        engine = create_async_engine(DATABASE_URL, echo=True)
        logger.info("Движок SQLAlchemy успешно создан")
        return engine
    except Exception as e:
        logger.error(f"Ошибка создания движка SQLAlchemy: {e}")
        raise

# Инициализация движка
engine = None
async def get_engine():
    global engine
    if engine is None:
        engine = await init_engine()
    return engine

# Создание фабрики сессий
session_factory = sessionmaker(
    bind=None,  # Будет обновлено после инициализации движка
    class_=AsyncSession,
    expire_on_commit=False
)

# Получение сессии
async def get_session() -> AsyncSession:
    global session_factory
    if session_factory.bind is None:
        session_factory.bind = await get_engine()
    async with session_factory() as session:
        yield session
