import asyncio
from typing import Callable

import structlog

from src.mtproto.session_manager import SessionManager
from src.shared.constants import BotState
from src.storage.database import Database
from src.storage.repositories import UserRepository

logger = structlog.get_logger()


class SessionMonitor:
    """Background worker for monitoring session health."""

    def __init__(
        self,
        database: Database,
        session_manager: SessionManager,
        check_interval: int = 300,  # 5 minutes
        notify_callback: Callable[[int, str], None] | None = None,
    ):
        """
        Initialize session monitor.

        Args:
            database: Database instance
            session_manager: Session manager
            check_interval: Check interval in seconds
            notify_callback: Callback to notify user of session issues
        """
        self._db = database
        self._session_manager = session_manager
        self._check_interval = check_interval
        self._notify_callback = notify_callback
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the monitor."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("session_monitor_started")

    async def stop(self) -> None:
        """Stop the monitor."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("session_monitor_stopped")

    async def _run(self) -> None:
        """Main monitor loop."""
        while self._running:
            try:
                await self._check_sessions()
            except Exception as e:
                logger.error("session_check_error", error=str(e))

            await asyncio.sleep(self._check_interval)

    async def _check_sessions(self) -> None:
        """Check all active user sessions."""
        async with self._db.session() as session:
            user_repo = UserRepository(session)
            # Get users that are running or have sessions
            running_users = await user_repo.get_by_state(BotState.RUNNING.value)

        for user in running_users:
            is_valid = await self._session_manager.verify_session(user.id)

            if not is_valid:
                logger.warning("session_invalid", user_id=user.id)

                # Update user state
                async with self._db.session() as session:
                    user_repo = UserRepository(session)
                    await user_repo.update_state(user.id, BotState.SESSION_EXPIRED.value)

                # Notify user
                if self._notify_callback:
                    await self._notify_callback(
                        user.id,
                        "Твоя сессия истекла. Требуется повторная авторизация.",
                    )

    async def check_user_session(self, user_id: int) -> bool:
        """
        Check specific user's session.

        Args:
            user_id: Telegram user ID

        Returns:
            True if session is valid
        """
        return await self._session_manager.verify_session(user_id)
