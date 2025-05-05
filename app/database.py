import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

# Настройка логирования для отладки
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Базовый класс для моделей
class Base(DeclarativeBase):
    pass

# Настройка подключения
DATABASE_URL = "postgresql+asyncpg://user:password@localhost:5432/dbname"  # Замените на ваш URL
engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "server_settings": {"application_name": "stockbot"}
    },
    pool_size=5,
    max_overflow=10
)
logger.info(f"Engine created with connect_args: {engine.url.query}")

# Создание фабрики сессий
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    execution_options={"prepared_statement_cache_size": 0}
)
logger.info(f"Session factory created with execution_options: {async_session.kw}")

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized")

async def dispose_engine():
    await engine.dispose()
    logger.info("Engine disposed")

async def get_session() -> AsyncSession:
    async with async_session() as session:
        logger.debug("Session created")
        yield session