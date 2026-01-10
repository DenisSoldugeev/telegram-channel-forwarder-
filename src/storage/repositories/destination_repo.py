from sqlalchemy import select, update

from src.storage.models import Destination
from src.storage.repositories.base import BaseRepository


class DestinationRepository(BaseRepository[Destination]):
    """Repository for Destination operations."""

    model = Destination

    async def get_by_user(self, user_id: int) -> Destination | None:
        """
        Get destination for a user.

        Args:
            user_id: Telegram user ID

        Returns:
            Destination or None
        """
        stmt = select(Destination).where(Destination.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_user(self, user_id: int) -> Destination | None:
        """
        Get active destination for a user.

        Args:
            user_id: Telegram user ID

        Returns:
            Active destination or None
        """
        stmt = select(Destination).where(
            Destination.user_id == user_id,
            Destination.is_active == True,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        user_id: int,
        channel_id: int,
        channel_username: str | None,
        channel_title: str,
    ) -> Destination:
        """
        Create or update destination for user.

        Args:
            user_id: Telegram user ID
            channel_id: Telegram channel ID
            channel_username: Channel @username
            channel_title: Channel title

        Returns:
            Created or updated destination
        """
        existing = await self.get_by_user(user_id)

        if existing:
            existing.channel_id = channel_id
            existing.channel_username = channel_username
            existing.channel_title = channel_title
            existing.is_active = True
            return await self.update(existing)

        destination = Destination(
            user_id=user_id,
            channel_id=channel_id,
            channel_username=channel_username,
            channel_title=channel_title,
        )
        return await self.create(destination)

    async def deactivate(self, user_id: int) -> None:
        """
        Deactivate destination for user.

        Args:
            user_id: Telegram user ID
        """
        stmt = update(Destination).where(Destination.user_id == user_id).values(is_active=False)
        await self._session.execute(stmt)
        await self._session.commit()
