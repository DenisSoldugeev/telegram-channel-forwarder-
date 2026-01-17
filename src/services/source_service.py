from dataclasses import dataclass, field

import structlog
from pyrogram.enums import ChatType
from pyrogram.types import Chat

from src.mtproto.client import MTProtoClient, MTProtoClientManager
from src.mtproto.session_manager import SessionManager
from src.shared.constants import MAX_SOURCES_PER_USER
from src.shared.exceptions import SourceError
from src.shared.utils.validators import (
    ChannelIdentifierType,
    ChannelValidationResult,
    parse_channel_links,
    validate_channel_link,
)
from src.storage.database import Database
from src.storage.models import Source
from src.storage.repositories import SourceRepository

logger = structlog.get_logger()


@dataclass
class SourceAddError:
    """Error for a single source."""

    link: str
    reason: str


@dataclass
class SourceValidationResult:
    """Result of source validation."""

    success: list[Source] = field(default_factory=list)
    errors: list[SourceAddError] = field(default_factory=list)


class SourceService:
    """Service for managing channel sources."""

    def __init__(
        self,
        database: Database,
        session_manager: SessionManager,
        client_manager: MTProtoClientManager,
    ):
        """
        Initialize source service.

        Args:
            database: Database instance
            session_manager: Session manager for loading sessions
            client_manager: MTProto client manager
        """
        self._db = database
        self._session_manager = session_manager
        self._client_manager = client_manager

    async def add_sources(
        self,
        user_id: int,
        links: list[str],
    ) -> SourceValidationResult:
        """
        Add multiple sources from links.

        Args:
            user_id: Telegram user ID
            links: List of channel links/usernames

        Returns:
            SourceValidationResult with successes and errors
        """
        logger.info("add_sources", user_id=user_id, count=len(links))

        result = SourceValidationResult()

        # Check limit
        async with self._db.session() as session:
            source_repo = SourceRepository(session)
            current_count = await source_repo.count_by_user(user_id)

        if current_count >= MAX_SOURCES_PER_USER:
            raise SourceError(
                "Source limit reached",
                f"Достигнут лимит источников ({MAX_SOURCES_PER_USER}).",
            )

        # Load session and get client
        session_string = await self._session_manager.load_session(user_id)
        if not session_string:
            raise SourceError("No session", "Сессия не найдена. Авторизуйся заново.")

        client = await self._client_manager.get_client(user_id, session_string)
        # Use start() to ensure client is fully initialized for API calls
        if not client.client.is_initialized:
            await client.client.start()

        try:
            for link in links:
                # Check limit for each addition
                if current_count >= MAX_SOURCES_PER_USER:
                    result.errors.append(
                        SourceAddError(link, f"Лимит источников ({MAX_SOURCES_PER_USER})")
                    )
                    continue

                # Validate link format
                validation = validate_channel_link(link)
                if not validation.is_valid:
                    result.errors.append(
                        SourceAddError(link, validation.error or "Неверный формат")
                    )
                    continue

                try:
                    # Get chat based on identifier type
                    chat = await self._resolve_channel(client, validation)
                    if not chat:
                        result.errors.append(
                            SourceAddError(link, "Не удалось найти канал или нет доступа")
                        )
                        continue

                    # Check that it's a channel or supergroup, not a bot/user
                    if chat.type not in (ChatType.CHANNEL, ChatType.SUPERGROUP):
                        result.errors.append(
                            SourceAddError(link, f"Это не канал (тип: {chat.type.name})")
                        )
                        continue

                    # Check if already added
                    async with self._db.session() as session:
                        source_repo = SourceRepository(session)
                        existing = await source_repo.get_by_channel(user_id, chat.id)

                        if existing:
                            if existing.is_active:
                                result.errors.append(
                                    SourceAddError(link, "Уже добавлен")
                                )
                            else:
                                # Reactivate
                                existing.is_active = True
                                await source_repo.update(existing)
                                result.success.append(existing)
                                current_count += 1
                            continue

                        # Add new source with fallback for title
                        channel_title = (
                            chat.title
                            or chat.username
                            or f"Channel {chat.id}"
                        )
                        logger.info(
                            "adding_source",
                            user_id=user_id,
                            channel_id=chat.id,
                            channel_username=chat.username,
                            channel_title=channel_title,
                        )
                        source = await source_repo.add_source(
                            user_id=user_id,
                            channel_id=chat.id,
                            channel_username=chat.username,
                            channel_title=channel_title,
                        )
                        result.success.append(source)
                        current_count += 1

                except Exception as e:
                    logger.error(
                        "add_source_error",
                        user_id=user_id,
                        link=link,
                        error=str(e),
                    )
                    result.errors.append(SourceAddError(link, str(e)))

        finally:
            pass  # Don't disconnect, client may be reused

        logger.info(
            "add_sources_complete",
            user_id=user_id,
            success=len(result.success),
            errors=len(result.errors),
        )

        return result

    async def add_sources_from_file(
        self,
        user_id: int,
        file_content: bytes,
        filename: str,
    ) -> SourceValidationResult:
        """
        Add sources from uploaded file.

        Args:
            user_id: Telegram user ID
            file_content: File content bytes
            filename: Original filename

        Returns:
            SourceValidationResult
        """
        logger.info("add_sources_from_file", user_id=user_id, filename=filename)

        # Parse file content
        try:
            text = file_content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = file_content.decode("cp1251")
            except UnicodeDecodeError:
                raise SourceError("Encoding error", "Не удалось прочитать файл.")

        # Extract links
        if filename.lower().endswith(".csv"):
            # For CSV, take first column
            lines = []
            for line in text.strip().split("\n"):
                if "," in line:
                    lines.append(line.split(",")[0].strip())
                else:
                    lines.append(line.strip())
        else:
            lines = [line.strip() for line in text.strip().split("\n")]

        # Filter out empty lines and comments
        links = [
            line for line in lines
            if line and not line.startswith("#")
        ]

        if not links:
            raise SourceError("Empty file", "Файл не содержит ссылок.")

        return await self.add_sources(user_id, links)

    async def remove_source(self, user_id: int, source_id: int) -> bool:
        """
        Remove a source.

        Args:
            user_id: Telegram user ID
            source_id: Source ID to remove

        Returns:
            True if removed
        """
        logger.info("remove_source", user_id=user_id, source_id=source_id)

        async with self._db.session() as session:
            source_repo = SourceRepository(session)
            source = await source_repo.get_by_id(source_id)

            if not source or source.user_id != user_id:
                return False

            await source_repo.delete(source)
            return True

    async def list_sources(
        self,
        user_id: int,
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[Source], int]:
        """
        List user's sources with pagination.

        Args:
            user_id: Telegram user ID
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Tuple of (sources, total_count)
        """
        async with self._db.session() as session:
            source_repo = SourceRepository(session)
            offset = (page - 1) * page_size

            sources = await source_repo.get_by_user(
                user_id,
                limit=page_size,
                offset=offset,
            )
            total = await source_repo.count_by_user(user_id)

            return sources, total

    async def get_active_sources(self, user_id: int) -> list[Source]:
        """
        Get all active sources for user.

        Args:
            user_id: Telegram user ID

        Returns:
            List of active sources
        """
        async with self._db.session() as session:
            source_repo = SourceRepository(session)
            return await source_repo.get_by_user(user_id, active_only=True, limit=100)

    async def _resolve_channel(
        self,
        client: MTProtoClient,
        validation: ChannelValidationResult,
    ) -> Chat | None:
        """
        Resolve channel from validation result.

        Handles username, channel ID, and invite links.

        Args:
            client: MTProto client
            validation: Validated channel identifier

        Returns:
            Chat object or None if not accessible
        """
        try:
            if validation.identifier_type == ChannelIdentifierType.USERNAME:
                if validation.username:
                    return await client.get_chat(validation.username)

            elif validation.identifier_type == ChannelIdentifierType.CHANNEL_ID:
                if validation.channel_id:
                    return await client.get_chat(validation.channel_id)

            elif validation.identifier_type == ChannelIdentifierType.INVITE_LINK:
                # For invite links, get_chat works if user is already a member
                if validation.invite_link:
                    return await client.get_chat(validation.invite_link)

            return None

        except Exception as e:
            logger.warning(
                "resolve_channel_failed",
                identifier_type=validation.identifier_type,
                error=str(e),
            )
            return None
