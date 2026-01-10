from sqlalchemy import select, update

from src.storage.models import User
from src.storage.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Repository for User operations."""

    model = User

    async def get_or_create(self, user_id: int) -> tuple[User, bool]:
        """
        Get existing user or create new one.

        Args:
            user_id: Telegram user ID

        Returns:
            Tuple of (user, created) where created is True if new user
        """
        user = await self.get_by_id(user_id)
        if user:
            return user, False

        user = User(id=user_id)
        await self.create(user)
        return user, True

    async def update_state(self, user_id: int, state: str) -> None:
        """
        Update user's FSM state.

        Args:
            user_id: Telegram user ID
            state: New state value
        """
        stmt = update(User).where(User.id == user_id).values(state=state)
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_by_state(self, state: str) -> list[User]:
        """
        Get all users in a specific state.

        Args:
            state: FSM state to filter by

        Returns:
            List of users in that state
        """
        stmt = select(User).where(User.state == state, User.is_active == True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_users(self) -> list[User]:
        """
        Get all active users.

        Returns:
            List of active users
        """
        stmt = select(User).where(User.is_active == True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
