from datetime import datetime

import structlog

from src.shared.constants import DeliveryStatus
from src.storage.database import Database
from src.storage.repositories import DeliveryRepository

logger = structlog.get_logger()


class DeliveryService:
    """Service for tracking message deliveries."""

    def __init__(self, database: Database):
        """
        Initialize delivery service.

        Args:
            database: Database instance
        """
        self._db = database

    async def check_duplicate(
        self,
        user_id: int,
        source_id: int,
        message_id: int,
    ) -> bool:
        """
        Check if message was already forwarded.

        Args:
            user_id: Telegram user ID
            source_id: Source ID
            message_id: Original message ID

        Returns:
            True if duplicate
        """
        async with self._db.session() as session:
            repo = DeliveryRepository(session)
            existing = await repo.find_by_message(user_id, source_id, message_id)

            if not existing:
                return False

            return existing.status == DeliveryStatus.SUCCESS.value

    async def create_pending(
        self,
        user_id: int,
        source_id: int,
        destination_id: int | None,
        message_id: int,
    ) -> int:
        """
        Create pending delivery record.

        Args:
            user_id: Telegram user ID
            source_id: Source ID
            destination_id: Destination ID (None for DM mode)
            message_id: Original message ID

        Returns:
            Delivery log ID
        """
        async with self._db.session() as session:
            repo = DeliveryRepository(session)
            log = await repo.create_pending(
                user_id=user_id,
                source_id=source_id,
                destination_id=destination_id,
                original_message_id=message_id,
            )
            return log.id

    async def mark_success(
        self,
        log_id: int,
        forwarded_message_id: int,
    ) -> None:
        """
        Mark delivery as successful.

        Args:
            log_id: Delivery log ID
            forwarded_message_id: Forwarded message ID
        """
        async with self._db.session() as session:
            repo = DeliveryRepository(session)
            await repo.mark_success(log_id, forwarded_message_id)

        logger.debug("delivery_success", log_id=log_id)

    async def mark_failed(
        self,
        log_id: int,
        error: str,
        will_retry: bool = True,
    ) -> None:
        """
        Mark delivery as failed.

        Args:
            log_id: Delivery log ID
            error: Error message
            will_retry: Whether retry will be attempted
        """
        async with self._db.session() as session:
            repo = DeliveryRepository(session)
            await repo.mark_failed(log_id, error, increment_retry=will_retry)

        logger.warning("delivery_failed", log_id=log_id, error=error)

    async def get_stats(
        self,
        user_id: int,
        hours: int = 24,
    ) -> dict[str, int]:
        """
        Get delivery statistics.

        Args:
            user_id: Telegram user ID
            hours: Time period in hours

        Returns:
            Dict with success/failed/pending counts
        """
        async with self._db.session() as session:
            repo = DeliveryRepository(session)
            return await repo.get_stats(user_id, hours)

    async def get_pending_retries(
        self,
        max_retries: int = 5,
    ) -> list:
        """
        Get failed deliveries eligible for retry.

        Args:
            max_retries: Maximum retry attempts

        Returns:
            List of delivery logs
        """
        async with self._db.session() as session:
            repo = DeliveryRepository(session)
            return await repo.get_pending_retries(max_retries)
