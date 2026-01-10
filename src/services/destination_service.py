import structlog

from src.mtproto.client import MTProtoClientManager
from src.mtproto.session_manager import SessionManager
from src.shared.exceptions import DestinationError
from src.storage.database import Database
from src.storage.models import Destination
from src.storage.repositories import DestinationRepository

logger = structlog.get_logger()


class DestinationService:
    """Service for managing destination channel."""

    def __init__(
        self,
        database: Database,
        session_manager: SessionManager,
        client_manager: MTProtoClientManager,
        bot_id: int | None = None,
    ):
        """
        Initialize destination service.

        Args:
            database: Database instance
            session_manager: Session manager
            client_manager: MTProto client manager
            bot_id: Bot's Telegram ID for admin check
        """
        self._db = database
        self._session_manager = session_manager
        self._client_manager = client_manager
        self._bot_id = bot_id

    async def set_destination(
        self,
        user_id: int,
        channel_id: int | None = None,
        channel_username: str | None = None,
        channel_title: str | None = None,
    ) -> Destination:
        """
        Set destination channel.

        Args:
            user_id: Telegram user ID
            channel_id: Channel ID (if known)
            channel_username: Channel username
            channel_title: Channel title (if known)

        Returns:
            Created/updated Destination
        """
        logger.info(
            "set_destination",
            user_id=user_id,
            channel_id=channel_id,
            channel_username=channel_username,
        )

        # If we don't have channel_id, resolve it
        if not channel_id and channel_username:
            session_string = await self._session_manager.load_session(user_id)
            if not session_string:
                raise DestinationError("No session", "Сессия не найдена.")

            client = await self._client_manager.get_client(user_id, session_string)
            if not client.client.is_initialized:
                await client.client.start()

            try:
                chat = await client.get_chat(channel_username)
                channel_id = chat.id
                channel_title = chat.title
                channel_username = chat.username
            except Exception as e:
                logger.error("get_chat_error", error=str(e))
                raise DestinationError(str(e), "Не удалось найти канал.")

        if not channel_id:
            raise DestinationError("No channel ID", "Не удалось определить канал.")

        # TODO: Verify bot is admin in the channel
        # This requires Bot API call, not MTProto
        # For now, we'll validate on first forward attempt

        # Save destination
        async with self._db.session() as session:
            dest_repo = DestinationRepository(session)
            destination = await dest_repo.upsert(
                user_id=user_id,
                channel_id=channel_id,
                channel_username=channel_username,
                channel_title=channel_title or "Unknown",
            )

        logger.info(
            "destination_set",
            user_id=user_id,
            channel_id=channel_id,
            channel_title=channel_title,
        )

        return destination

    async def get_destination(self, user_id: int) -> Destination | None:
        """
        Get current destination for user.

        Args:
            user_id: Telegram user ID

        Returns:
            Destination or None
        """
        async with self._db.session() as session:
            dest_repo = DestinationRepository(session)
            return await dest_repo.get_active_by_user(user_id)

    async def clear_destination(self, user_id: int) -> None:
        """
        Clear destination for user.

        Args:
            user_id: Telegram user ID
        """
        async with self._db.session() as session:
            dest_repo = DestinationRepository(session)
            await dest_repo.deactivate(user_id)
