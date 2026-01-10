import os
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.config import settings
from src.storage.models import Base


class Database:
    """Database connection and session management."""

    def __init__(self, database_url: str):
        """
        Initialize database connection.

        Args:
            database_url: SQLAlchemy database URL
        """
        self._engine = create_async_engine(
            database_url,
            echo=settings.log_level == "DEBUG",
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def create_tables(self) -> None:
        """Create all tables if they don't exist."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_tables(self) -> None:
        """Drop all tables (use with caution!)."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    def session(self) -> AsyncSession:
        """
        Get a new database session.

        Returns:
            AsyncSession instance
        """
        return self._session_factory()

    async def close(self) -> None:
        """Close database connection."""
        await self._engine.dispose()


@lru_cache
def get_database() -> Database:
    """
    Get cached database instance.

    Returns:
        Database instance
    """
    # Ensure data directory exists for SQLite
    db_url = settings.database_url.get_secret_value()
    if "sqlite" in db_url:
        db_path = db_url.split("///")[-1]
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    return Database(db_url)
