from datetime import datetime, timedelta

from sqlalchemy import func, select, update

from src.shared.constants import DeliveryStatus
from src.storage.models import DeliveryLog
from src.storage.repositories.base import BaseRepository


class DeliveryRepository(BaseRepository[DeliveryLog]):
    """Repository for DeliveryLog operations."""

    model = DeliveryLog

    async def find_by_message(
        self,
        user_id: int,
        source_id: int,
        original_message_id: int,
    ) -> DeliveryLog | None:
        """
        Find delivery log by original message.

        Args:
            user_id: Telegram user ID
            source_id: Source ID
            original_message_id: Original message ID

        Returns:
            DeliveryLog or None
        """
        stmt = select(DeliveryLog).where(
            DeliveryLog.user_id == user_id,
            DeliveryLog.source_id == source_id,
            DeliveryLog.original_message_id == original_message_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_pending(
        self,
        user_id: int,
        source_id: int,
        destination_id: int | None,
        original_message_id: int,
    ) -> DeliveryLog:
        """
        Create pending delivery log.

        Args:
            user_id: Telegram user ID
            source_id: Source ID
            destination_id: Destination ID (None for DM mode)
            original_message_id: Original message ID

        Returns:
            Created delivery log
        """
        log = DeliveryLog(
            user_id=user_id,
            source_id=source_id,
            destination_id=destination_id,
            original_message_id=original_message_id,
            status=DeliveryStatus.PENDING.value,
        )
        return await self.create(log)

    async def mark_success(
        self,
        log_id: int,
        forwarded_message_id: int,
    ) -> None:
        """
        Mark delivery as successful.

        Args:
            log_id: Delivery log ID
            forwarded_message_id: ID of forwarded message
        """
        stmt = (
            update(DeliveryLog)
            .where(DeliveryLog.id == log_id)
            .values(
                status=DeliveryStatus.SUCCESS.value,
                forwarded_message_id=forwarded_message_id,
                completed_at=datetime.utcnow(),
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def mark_failed(
        self,
        log_id: int,
        error_message: str,
        increment_retry: bool = True,
    ) -> None:
        """
        Mark delivery as failed.

        Args:
            log_id: Delivery log ID
            error_message: Error description
            increment_retry: Whether to increment retry count
        """
        log = await self.get_by_id(log_id)
        if not log:
            return

        values = {
            "status": DeliveryStatus.FAILED.value,
            "error_message": error_message,
            "completed_at": datetime.utcnow(),
        }
        if increment_retry:
            values["retry_count"] = log.retry_count + 1

        stmt = update(DeliveryLog).where(DeliveryLog.id == log_id).values(**values)
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_stats(
        self,
        user_id: int,
        hours: int = 24,
    ) -> dict[str, int]:
        """
        Get delivery statistics for user.

        Args:
            user_id: Telegram user ID
            hours: Time period in hours

        Returns:
            Dict with success/failed/pending counts
        """
        since = datetime.utcnow() - timedelta(hours=hours)

        stmt = (
            select(DeliveryLog.status, func.count())
            .where(
                DeliveryLog.user_id == user_id,
                DeliveryLog.created_at >= since,
            )
            .group_by(DeliveryLog.status)
        )
        result = await self._session.execute(stmt)
        rows = result.all()

        stats = {"success": 0, "failed": 0, "pending": 0}
        for status, count in rows:
            stats[status] = count

        return stats

    async def get_last_delivery(self, user_id: int) -> DeliveryLog | None:
        """
        Get most recent delivery for user.

        Args:
            user_id: Telegram user ID

        Returns:
            Most recent DeliveryLog or None
        """
        stmt = (
            select(DeliveryLog)
            .where(
                DeliveryLog.user_id == user_id,
                DeliveryLog.status == DeliveryStatus.SUCCESS.value,
            )
            .order_by(DeliveryLog.completed_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_pending_retries(
        self,
        max_retries: int = 5,
        limit: int = 100,
    ) -> list[DeliveryLog]:
        """
        Get failed deliveries that can be retried.

        Args:
            max_retries: Maximum retry count
            limit: Maximum number of logs to return

        Returns:
            List of delivery logs eligible for retry
        """
        stmt = (
            select(DeliveryLog)
            .where(
                DeliveryLog.status == DeliveryStatus.FAILED.value,
                DeliveryLog.retry_count < max_retries,
            )
            .order_by(DeliveryLog.created_at)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
