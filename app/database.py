# app/database.py
import logging
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не установлен в переменных окружения")

# Преобразуем postgres:// в postgresql+asyncpg://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

logger.info("Строка подключения к базе данных: %s", DATABASE_URL)

# Создаём движок SQLAlchemy с использованием пула подключений asyncpg
logger.info("Создание движка SQLAlchemy с отключением кэша prepared statements...")
engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    connect_args={
        "statement_cache_size": 0,  # Отключаем кэш prepared statements
        "prepared_statement_cache_size": 0,  # Дополнительно отключаем кэш на уровне asyncpg
        "server_settings": {
            "application_name": "stock-market-bott"
        }
    },
    pool_size=5,  # Максимальное количество подключений в пуле
    max_overflow=10,  # Максимальное количество дополнительных подключений
    pool_timeout=30,  # Таймаут ожидания подключения
    pool_pre_ping=True  # Проверка подключения перед использованием
)
logger.info("Движок SQLAlchemy создан успешно.")

# Создаём фабрику сессий
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

class Base(DeclarativeBase):
    pass

# Функция для инициализации базы данных (создание таблиц) с повторными попытками
async def init_db():
    for attempt in range(1, 10):  # 10 попыток
        try:
            logger.info("Попытка %d: подключение к базе данных...", attempt)
            async with engine.begin() as conn:
                logger.info("Соединение с базой данных успешно установлено.")
                await conn.run_sync(Base.metadata.create_all)
                logger.info("Таблицы успешно созданы или уже существуют.")
                return
        except Exception as e:
            logger.error("Ошибка подключения к базе данных на попытке %d: %s", attempt, str(e))
            if attempt == 9:
                logger.error("Не удалось подключиться к базе данных после 10 попыток. Завершаем работу.")
                raise
            await asyncio.sleep(10)  # Задержка 10 секунд перед следующей попыткой

# Функция для получения сессии базы данных
async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session