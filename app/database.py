import os
import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.exc import OperationalError
from app.models import Base
from contextlib import asynccontextmanager

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
    raise ValueError("Переменная окружения DATABASE_URL не установлена")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

logger.info("Строка подключения к базе данных: %s", DATABASE_URL)

engine = None
async_session = None

def create_async_engine():
    """Создание асинхронного движка SQLAlchemy."""
    try:
        return create_async_engine(DATABASE_URL)
    except Exception as e:
        logger.error(f"Ошибка при создании движка базы данных: {e}")
        raise

async def init_db():
    """Инициализация базы данных и создание таблиц с повторными попытками."""
    global engine, async_session
    for attempt in range(1, 11):  # 10 попыток
        try:
            logger.info("Попытка %d: подключение к базе данных...", attempt)
            engine = create_async_engine()
            async with engine.begin() as conn:
                logger.info("Соединение с базой данных успешно установлено.")
                await conn.run_sync(Base.metadata.create_all)
                logger.info("Таблицы успешно созданы или уже существуют.")
            async_session = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            logger.info("База данных инициализирована, таблицы созданы.")
            return
        except OperationalError as e:
            logger.error(f"Ошибка подключения к базе данных на попытке %d: {e}", attempt)
            if attempt == 10:
                logger.error("Не удалось подключиться к базе данных после 10 попыток.")
                raise
            await asyncio.sleep(10)  # Задержка 10 секунд
        except Exception as e:
            logger.error(f"Неожиданная ошибка при инициализации базы данных на попытке %d: {e}", attempt)
            if attempt == 10:
                logger.error("Не удалось инициализировать базу данных после 10 попыток.")
                raise
            await asyncio.sleep(10)

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