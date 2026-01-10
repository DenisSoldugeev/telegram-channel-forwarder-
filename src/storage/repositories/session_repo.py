from datetime import datetime

from sqlalchemy import select, update

from src.storage.models import Session
from src.storage.repositories.base import BaseRepository


class SessionRepository(BaseRepository[Session]):
    """Repository for Session operations."""

    model = Session

    async def get_by_user(self, user_id: int) -> Session | None:
        """
        Get session by user ID.

        Args:
            user_id: Telegram user ID

        Returns:
            Session or None if not found
        """
        stmt = select(Session).where(Session.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_valid_session(self, user_id: int) -> Session | None:
        """
        Get valid session for user.

        Args:
            user_id: Telegram user ID

        Returns:
            Valid session or None
        """
        stmt = select(Session).where(
            Session.user_id == user_id,
            Session.is_valid == True,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        user_id: int,
        session_data: bytes,
        session_hash: str,
    ) -> Session:
        """
        Create or update session for user.

        Args:
            user_id: Telegram user ID
            session_data: Encrypted session data
            session_hash: Hash for quick validation

        Returns:
            Created or updated session
        """
        existing = await self.get_by_user(user_id)

        if existing:
            existing.session_data = session_data
            existing.session_hash = session_hash
            existing.is_valid = True
            existing.last_used_at = datetime.utcnow()
            return await self.update(existing)

        session = Session(
            user_id=user_id,
            session_data=session_data,
            session_hash=session_hash,
            is_valid=True,
        )
        return await self.create(session)

    async def invalidate(self, user_id: int) -> None:
        """
        Mark session as invalid.

        Args:
            user_id: Telegram user ID
        """
        stmt = update(Session).where(Session.user_id == user_id).values(is_valid=False)
        await self._session.execute(stmt)
        await self._session.commit()

    async def touch(self, user_id: int) -> None:
        """
        Update last_used_at timestamp.

        Args:
            user_id: Telegram user ID
        """
        stmt = (
            update(Session)
            .where(Session.user_id == user_id)
            .values(last_used_at=datetime.utcnow())
        )
        await self._session.execute(stmt)
        await self._session.commit()
