import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from src.storage.database import Database
from src.storage.models import Base


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def test_db() -> AsyncGenerator[Database, None]:
    """Create test database with in-memory SQLite."""
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    yield db
    await db.close()


@pytest_asyncio.fixture
async def db_session(test_db: Database):
    """Get database session for tests."""
    async with test_db.session() as session:
        yield session
