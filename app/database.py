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

# Заменяем префикс для совместимости с asyncpg
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
logger.info(f"Используемый DATABASE_URL: {DATABASE_URL[:50]}... (обрезан для логов)")

# Создаём асинхронный движок без специфичных настроек для pgbouncer
engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    pool_size=2,           # Уменьшаем размер пула для Heroku
    max_overflow=3,        # Уменьшаем количество дополнительных соединений
    pool_timeout=30,       # Таймаут ожидания соединения
    pool_pre_ping=True,    # Проверяем соединения перед использованием
)

async_session = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession
)

async def init_db():
    logger.info("Инициализация базы данных...")
    for attempt in range(1, 6):  # 5 попыток
        try:
            async with engine.begin() as conn:
                # Проверяем подключение и логируем версию PostgreSQL
                try:
                    version = await conn.scalar(text("SHOW server_version"))
                    logger.info(f"Успешное подключение к базе данных. Версия PostgreSQL: {version}")
                except Exception as e:
                    logger.error(f"Ошибка при проверке версии PostgreSQL: {str(e)}")
                    raise

                # Создание таблиц
                from app.models import Base
                await conn.run_sync(Base.metadata.create_all)
                logger.info("Все таблицы успешно созданы или уже существуют.")
                return
        except OperationalError as e:
            logger.error(f"Ошибка подключения к базе данных на попытке {attempt}: {str(e)}")
            if attempt == 5:
                logger.error("Не удалось подключиться к базе данных после 5 попыток.")
                raise
            await asyncio.sleep(5)  # Задержка 5 секунд перед следующей попыткой
        except DatabaseError as e:
            logger.error(f"Ошибка базы данных при инициализации на попытке {attempt}: {str(e)}")
            if attempt == 5:
                logger.error("Не удалось подключиться к базе данных после 5 попыток.")
                raise
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Неизвестная ошибка при инициализации базы данных на попытке {attempt}: {str(e)}")
            if attempt == 5:
                logger.error("Не удалось подключиться к базе данных после 5 попыток.")
                raise
            await asyncio.sleep(5)
    logger.info("База данных успешно инициализирована.")

async def dispose_engine():
    logger.info("Закрытие соединения с базой данных...")
    await engine.dispose()
    logger.info("Соединение с базой данных закрыто")