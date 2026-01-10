from datetime import datetime

from sqlalchemy import func, select, update

from src.storage.models import Source
from src.storage.repositories.base import BaseRepository


class SourceRepository(BaseRepository[Source]):
    """Repository for Source operations."""

    model = Source

    async def get_by_user(
        self,
        user_id: int,
        active_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Source]:
        """
        Get sources for a user.

        Args:
            user_id: Telegram user ID
            active_only: Filter only active sources
            limit: Maximum number of sources
            offset: Number of sources to skip

        Returns:
            List of sources
        """
        stmt = select(Source).where(Source.user_id == user_id)
        if active_only:
            stmt = stmt.where(Source.is_active == True)
        stmt = stmt.order_by(Source.added_at.desc()).limit(limit).offset(offset)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_channel(self, user_id: int, channel_id: int) -> Source | None:
        """
        Get source by channel ID.

        Args:
            user_id: Telegram user ID
            channel_id: Telegram channel ID

        Returns:
            Source or None
        """
        stmt = select(Source).where(
            Source.user_id == user_id,
            Source.channel_id == channel_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_by_user(self, user_id: int, active_only: bool = True) -> int:
        """
        Count sources for a user.

        Args:
            user_id: Telegram user ID
            active_only: Count only active sources

        Returns:
            Number of sources
        """
        stmt = select(func.count()).select_from(Source).where(Source.user_id == user_id)
        if active_only:
            stmt = stmt.where(Source.is_active == True)

        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def add_source(
        self,
        user_id: int,
        channel_id: int,
        channel_username: str | None,
        channel_title: str,
    ) -> Source:
        """
        Add new source.

        Args:
            user_id: Telegram user ID
            channel_id: Telegram channel ID
            channel_username: Channel @username
            channel_title: Channel title

        Returns:
            Created source
        """
        source = Source(
            user_id=user_id,
            channel_id=channel_id,
            channel_username=channel_username,
            channel_title=channel_title,
        )
        return await self.create(source)

    async def deactivate(self, source_id: int) -> None:
        """
        Deactivate a source.

        Args:
            source_id: Source ID
        """
        stmt = update(Source).where(Source.id == source_id).values(is_active=False)
        await self._session.execute(stmt)
        await self._session.commit()

    async def update_last_message(self, source_id: int, message_id: int) -> None:
        """
        Update last processed message ID.

        Args:
            source_id: Source ID
            message_id: Last processed message ID
        """
        stmt = (
            update(Source)
            .where(Source.id == source_id)
            .values(last_message_id=message_id, last_checked_at=datetime.utcnow())
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_all_active(self) -> list[Source]:
        """
        Get all active sources across all users.

        Returns:
            List of active sources
        """
        stmt = select(Source).where(Source.is_active == True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
