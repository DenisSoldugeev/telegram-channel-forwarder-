from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.models import Base

T = TypeVar("T", bound=Base)


class BaseRepository(Generic[T]):
    """Base repository with common CRUD operations."""

    model: type[T]

    def __init__(self, session: AsyncSession):
        """
        Initialize repository with database session.

        Args:
            session: SQLAlchemy async session
        """
        self._session = session

    async def get_by_id(self, entity_id: int) -> T | None:
        """
        Get entity by ID.

        Args:
            entity_id: Entity ID

        Returns:
            Entity or None if not found
        """
        return await self._session.get(self.model, entity_id)

    async def get_all(self, limit: int = 100, offset: int = 0) -> list[T]:
        """
        Get all entities with pagination.

        Args:
            limit: Maximum number of entities
            offset: Number of entities to skip

        Returns:
            List of entities
        """
        stmt = select(self.model).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, entity: T) -> T:
        """
        Create new entity.

        Args:
            entity: Entity to create

        Returns:
            Created entity
        """
        self._session.add(entity)
        await self._session.commit()
        await self._session.refresh(entity)
        return entity

    async def update(self, entity: T) -> T:
        """
        Update existing entity.

        Args:
            entity: Entity to update

        Returns:
            Updated entity
        """
        await self._session.commit()
        await self._session.refresh(entity)
        return entity

    async def delete(self, entity: T) -> None:
        """
        Delete entity.

        Args:
            entity: Entity to delete
        """
        await self._session.delete(entity)
        await self._session.commit()
