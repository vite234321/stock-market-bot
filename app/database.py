import logging
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.exc import OperationalError
from app.models import Base
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("Переменная окружения DATABASE_URL не установлена")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

engine = None
async_session = None

def create_async_engine():
    """Создание асинхронного движка SQLAlchemy с настройками пула."""
    try:
        return create_async_engine(
            DATABASE_URL,
            echo=False,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_pre_ping=True,
            connect_args={
                "statement_cache_size": 0,  # Отключение кэша подготовленных выражений
                "server_settings": {
                    "application_name": "stockbot",
                    "tcp_keepalives_idle": "30",
                    "tcp_keepalives_interval": "10",
                    "tcp_keepalives_count": "5",
                }
            }
        )
    except Exception as e:
        logger.error(f"Ошибка при создании движка базы данных: {e}")
        raise

async def init_db():
    """Инициализация базы данных и создание таблиц."""
    global engine, async_session
    try:
        engine = create_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async_session = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        logger.info("База данных инициализирована, таблицы созданы.")
    except OperationalError as e:
        logger.error(f"Ошибка подключения к базе данных при инициализации: {e}")
        raise
    except Exception as e:
        logger.error(f"Неожиданная ошибка при инициализации базы данных: {e}")
        raise

async def dispose_engine():
    """Закрытие движка базы данных."""
    global engine
    if engine:
        await engine.dispose()
        logger.info("Движок базы данных закрыт.")
        engine = None

@asynccontextmanager
async def get_session():
    """Контекстный менеджер для получения сессии базы данных."""
    global async_session
    if async_session is None:
        logger.error("Сессия базы данных не инициализирована")
        raise RuntimeError("Сессия базы данных не инициализирована")
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Ошибка в сессии базы данных: {e}")
            raise
        finally:
            await session.close()