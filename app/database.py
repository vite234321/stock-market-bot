import os
import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.exc import OperationalError
from app.models import Base
from contextlib import asynccontextmanager
import re

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)  # Логирование SQL-запросов

# Получение строки подключения
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.error("Переменная окружения DATABASE_URL не установлена")
    raise ValueError("Переменная окружения DATABASE_URL не установлена")

# Проверка формата DATABASE_URL
if not re.match(r'^postgresql\+asyncpg://', DATABASE_URL):
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
        logger.info("DATABASE_URL преобразован из postgres:// в postgresql+asyncpg://")
    else:
        logger.error("Недопустимый формат DATABASE_URL: %s", DATABASE_URL)
        raise ValueError(f"Недопустимый формат DATABASE_URL: {DATABASE_URL}")

logger.info("Строка подключения к базе данных: %s", DATABASE_URL)

engine = None
async_session = None

def create_engine_wrapper():
    """Создание асинхронного движка SQLAlchemy."""
    try:
        logger.info("Создание асинхронного движка SQLAlchemy...")
        engine = create_async_engine(DATABASE_URL, echo=True, connect_args={"timeout": 30})
        logger.info("Движок SQLAlchemy успешно создан")
        return engine
    except Exception as e:
        logger.error(f"Ошибка при создании движка базы данных: {e}")
        raise

async def init_db():
    """Инициализация базы данных и создание таблиц с повторными попытками."""
    global engine, async_session
    logger.info("Начало инициализации базы данных")
    for attempt in range(1, 6):  # Уменьшено до 5 попыток для оптимизации
        try:
            logger.info("Попытка %d: подключение к базе данных", attempt)
            engine = create_engine_wrapper()
            logger.info("Попытка создания таблиц...")
            async with engine.begin() as conn:
                logger.info("Соединение с базой данных успешно установлено")
                await conn.run_sync(Base.metadata.create_all)
                logger.info("Таблицы успешно созданы или уже существуют")
            async_session = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            logger.info("async_session инициализирован: %s", async_session)
            logger.info("База данных успешно инициализирована")
            return
        except OperationalError as e:
            logger.error(f"Ошибка подключения к базе данных на попытке %d: {e}", attempt)
            if attempt == 5:
                logger.error("Не удалось подключиться к базе данных после 5 попыток")
                raise
            await asyncio.sleep(5)  # Задержка 5 секунд
        except Exception as e:
            logger.error(f"Неожиданная ошибка при инициализации базы данных на попытке %d: {e}", attempt)
            if attempt == 5:
                logger.error("Не удалось инициализировать базу данных после 5 попыток")
                raise
            await asyncio.sleep(5)
        finally:
            if engine and attempt < 5:
                await engine.dispose()
                logger.info("Движок базы данных закрыт после неудачной попытки")
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