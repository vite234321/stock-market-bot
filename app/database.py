from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.sql import text
import os
import logging
import asyncio
from sqlalchemy.exc import OperationalError, DatabaseError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Получаем DATABASE_URL из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.error("DATABASE_URL не установлен в переменных окружения")
    raise ValueError("DATABASE_URL не установлен в переменных окружения")

# Заменяем префикс для совместимости с psycopg
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
logger.info(f"Используемый DATABASE_URL: {DATABASE_URL[:50]}... (обрезан для логов)")

engine = None
async_session = None

def create_engine_wrapper():
    """Создание асинхронного движка SQLAlchemy."""
    try:
        logger.info("Создание асинхронного движка SQLAlchemy...")
        engine = create_async_engine(
            DATABASE_URL,
            echo=True,
            pool_size=2,
            max_overflow=3,
            pool_timeout=30,
            pool_pre_ping=True,
            connect_args={
                "statement_cache_size": 0,
                "server_settings": {
                    "application_name": "trading-bot",
                }
            }
        )
        logger.info("Движок SQLAlchemy успешно создан")
        return engine
    except Exception as e:
        logger.error(f"Ошибка при создании движка базы данных: {e}")
        raise

async def init_db():
    """Инициализация базы данных и создание таблиц с повторными попытками."""
    global engine, async_session
    logger.info("Начало инициализации базы данных")
    for attempt in range(1, 6):  # 5 попыток
        try:
            logger.info("Попытка %d: подключение к базе данных", attempt)
            engine = create_engine_wrapper()
            logger.info("Попытка создания таблиц...")
            async with engine.begin() as conn:
                logger.info("Соединение с базой данных успешно установлено")
                await conn.run_sync(Base.metadata.create_all)
                logger.info("Все таблицы успешно созданы или уже существуют")
            global async_session
            async_session = async_sessionmaker(
                engine,
                expire_on_commit=False,
                class_=AsyncSession
            )
            logger.info("async_session инициализирован")
            logger.info("База данных успешно инициализирована")
            return
        except OperationalError as e:
            logger.error(f"Ошибка подключения к базе данных на попытке {attempt}: {e}")
            if attempt == 5:
                logger.error("Не удалось подключиться к базе данных после 5 попыток")
                raise
            await asyncio.sleep(5)
        except DatabaseError as e:
            logger.error(f"Ошибка базы данных при инициализации на попытке {attempt}: {e}")
            if attempt == 5:
                logger.error("Не удалось подключиться к базе данных после 5 попыток")
                raise
        except Exception as e:
            logger.error(f"Неизвестная ошибка при инициализации базы данных на попытке {attempt}: {e}")
            if attempt == 5:
                logger.error("Не удалось подключиться к базе данных после 5 попыток")
                raise
    logger.error("init_db завершился без успешной инициализации")
    raise RuntimeError("Не удалось инициализировать базу данных")

async def dispose_engine():
    """Закрытие движка базы данных."""
    global engine
    if engine:
        try:
            await engine.dispose()
            logger.info("Движок базы данных закрыт")
        except Exception as e:
            logger.error(f"Ошибка при закрытии движка базы данных: {e}")
        finally:
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

# Базовый класс для моделей SQLAlchemy
Base = DeclarativeBase()