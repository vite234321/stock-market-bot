# app/database.py
import os
import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Получение строки подключения из переменной окружения
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не установлен в переменных окружения")

# Преобразуем postgresql:// на postgresql+asyncpg:// (оставим asyncpg, так как он уже используется)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

logger.info("Строка подключения к базе данных: %s", DATABASE_URL)

# Создание асинхронного движка с отключением кэша prepared statements
try:
    engine = create_async_engine(
        DATABASE_URL,
        echo=True,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 30,
            "statement_cache_size": 0  # Отключаем кэширование prepared statements
        }
    )
    logger.info("Движок SQLAlchemy создан успешно с отключением кэша prepared statements.")
except Exception as e:
    logger.error("Ошибка создания движка SQLAlchemy: %s", str(e))
    raise

# Создание фабрики сессий
async_session = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)
logger.info("Фабрика сессий SQLAlchemy создана успешно")

# Базовый класс для моделей SQLAlchemy
Base = declarative_base()

# Функция для инициализации базы данных
async def init_db():
    for attempt in range(1, 10):
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
            await asyncio.sleep(10)