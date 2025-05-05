import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Настройка логирования для отладки
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Настройка подключения
DATABASE_URL = "postgresql+asyncpg://user:password@localhost:5432/dbname"  # Замените на ваш URL
engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    connect_args={
        "statement_cache_size": 0,  # Отключаем кэш prepared statements
        "prepared_statement_cache_size": 0,  # Дополнительно для asyncpg
        "server_settings": {"application_name": "stockbot"}
    },
    pool_size=5,  # Используем встроенный пул asyncpg
    max_overflow=10
)
logger.info(f"Engine created with connect_args: {engine.url.query}")

# Создание фабрики сессий
async_session = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    execution_options={"prepared_statement_cache_size": 0}
)
logger.info(f"Session factory created with execution_options: {async_session.kw}")

async def get_session() -> AsyncSession:
    async with async_session() as session:
        logger.debug("Session created")
        yield session