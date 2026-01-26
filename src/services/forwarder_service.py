import asyncio
from collections.abc import Callable
from dataclasses import dataclass

import structlog
from pyrogram.types import Message
from telegram import Bot as TelegramBot

from src.app.config import settings
from src.mtproto.client import MTProtoClientManager
from src.mtproto.handlers.new_message import MessageHandler
from src.mtproto.session_manager import SessionManager
from src.services.delivery_service import DeliveryService
from src.shared.exceptions import ForwardError, RateLimitError
from src.storage.database import Database
from src.storage.models import Destination
from src.storage.repositories import DestinationRepository, SourceRepository


@dataclass
class ForwardTarget:
    """Target for forwarding messages."""

    is_dm: bool = False
    user_id: int | None = None
    destination: Destination | None = None

    @property
    def chat_id(self) -> int:
        """Get target chat ID."""
        if self.is_dm and self.user_id is not None:
            return self.user_id
        if self.destination is not None:
            return self.destination.channel_id
        raise ValueError("ForwardTarget has no valid chat_id")

    @property
    def title(self) -> str:
        """Get target title for logging."""
        if self.is_dm:
            return "ЛС бота"
        if self.destination is not None:
            return self.destination.channel_title
        return "Unknown"


logger = structlog.get_logger()


class ForwarderService:
    """Service for forwarding messages from sources to destination."""

    def __init__(
        self,
        database: Database,
        session_manager: SessionManager,
        client_manager: MTProtoClientManager,
        delivery_service: DeliveryService,
        bot: TelegramBot | None = None,
        notify_callback: Callable[[int, str], None] | None = None,
    ):
        """
        Initialize forwarder service.

        Args:
            database: Database instance
            session_manager: Session manager
            client_manager: MTProto client manager
            delivery_service: Delivery tracking service
            bot: Telegram bot instance for DM forwarding
            notify_callback: Callback for user notifications
        """
        self._db = database
        self._session_manager = session_manager
        self._client_manager = client_manager
        self._delivery_service = delivery_service
        self._bot = bot
        self._notify_callback = notify_callback

        self._active_users: dict[int, MessageHandler] = {}
        self._user_targets: dict[int, ForwardTarget] = {}
        self._user_locks: dict[int, asyncio.Lock] = {}
        self._polling_tasks: dict[int, asyncio.Task] = {}
        self._user_pyrogram_handlers: dict[int, any] = {}

    def set_bot(self, bot: TelegramBot) -> None:
        """Set bot instance (for late binding after app init)."""
        self._bot = bot

    async def start_user_monitoring(self, user_id: int) -> None:
        """
        Start monitoring for a user using polling.

        Args:
            user_id: Telegram user ID
        """
        logger.info("start_monitoring", user_id=user_id)

        # Stop existing monitoring to reload sources
        if user_id in self._active_users:
            logger.info("restarting_monitoring", user_id=user_id)
            await self.stop_user_monitoring(user_id)

        # Load session
        session_string = await self._session_manager.load_session(user_id)
        if not session_string:
            raise ForwardError("No session", "Сессия не найдена.")

        # Get client
        client = await self._client_manager.get_client(user_id, session_string)

        # Get sources and destination
        async with self._db.session() as session:
            source_repo = SourceRepository(session)
            dest_repo = DestinationRepository(session)

            sources = await source_repo.get_by_user(user_id, active_only=True, limit=100)
            destination = await dest_repo.get_active_by_user(user_id)

        if not sources:
            raise ForwardError("Not configured", "Источники не настроены.")

        # Create forward target: channel or DM
        if destination:
            target = ForwardTarget(is_dm=False, destination=destination)
        else:
            if not self._bot:
                raise ForwardError("Bot not set", "Бот не инициализирован для ЛС.")
            target = ForwardTarget(is_dm=True, user_id=user_id)

        self._user_targets[user_id] = target

        # Log source channel IDs
        source_ids = [s.channel_id for s in sources]
        logger.info("loaded_sources", user_id=user_id, source_ids=source_ids)

        # Create message handler for media group collection
        handler = MessageHandler(
            on_message=lambda msg: self._handle_message(user_id, msg, target),
            on_media_group=lambda msgs: self._handle_media_group(user_id, msgs, target),
            media_group_timeout=settings.media_group_timeout,
            user_id=user_id,
        )

        # Add channels to monitor (ensure full format -100xxx is added)
        for source in sources:
            handler.add_channel(source.channel_id)
            # Also add the resolved chat.id if different (will be resolved later in source_state)

        logger.info(
            "channels_added_to_handler",
            user_id=user_id,
            source_channel_ids=[s.channel_id for s in sources],
            monitored_channels=list(handler._monitored_channels),
        )

        # Start Pyrogram client
        if not client.client.is_initialized:
            logger.info("starting_pyrogram_client", user_id=user_id)
            await client.client.start()
        else:
            logger.info("client_already_initialized", user_id=user_id)

        # Warm up peer cache to prevent "Peer id invalid" errors
        await client.warm_cache()

        # Register event handler for instant delivery (works for subscribed channels)
        pyrogram_handler = handler.get_pyrogram_handler()
        client.client.add_handler(pyrogram_handler)
        self._user_pyrogram_handlers[user_id] = pyrogram_handler
        logger.info("event_handler_registered", user_id=user_id)

        self._active_users[user_id] = handler
        self._user_locks[user_id] = asyncio.Lock()

        # Build source info for polling: {channel_id: last_message_id}
        source_state = {}
        for source in sources:
            # Get current last message to start from
            try:
                # Try username first, then full channel_id with -100 prefix
                chat_identifier = source.channel_username
                if not chat_identifier:
                    # Ensure we use the full format for numeric IDs
                    channel_str = str(source.channel_id)
                    if not channel_str.startswith("-100") and not channel_str.startswith("-"):
                        # Raw ID without prefix, add -100
                        chat_identifier = int(f"-100{source.channel_id}")
                    else:
                        chat_identifier = source.channel_id

                logger.info(
                    "resolving_channel",
                    user_id=user_id,
                    channel_id=source.channel_id,
                    username=source.channel_username,
                    using=chat_identifier,
                )

                chat = await client.client.get_chat(chat_identifier)

                # IMPORTANT: Add resolved chat.id to monitored channels
                # The chat.id from Pyrogram is what we'll receive in messages
                if chat.id not in handler._monitored_channels:
                    handler.add_channel(chat.id)
                    logger.info(
                        "added_resolved_chat_id",
                        user_id=user_id,
                        original_channel_id=source.channel_id,
                        resolved_chat_id=chat.id,
                    )

                # Get latest message ID to not process old messages
                async for msg in client.client.get_chat_history(chat.id, limit=1):
                    source_state[source.channel_id] = {
                        "last_msg_id": msg.id,
                        "chat_id": chat.id,
                        "title": chat.title,
                    }
                    break
                else:
                    source_state[source.channel_id] = {
                        "last_msg_id": 0,
                        "chat_id": chat.id,
                        "title": chat.title,
                    }
                logger.info(
                    "source_initialized",
                    user_id=user_id,
                    channel_id=source.channel_id,
                    resolved_chat_id=chat.id,
                    chat_title=chat.title,
                    last_msg_id=source_state[source.channel_id]["last_msg_id"],
                )
            except Exception as e:
                logger.error(
                    "source_init_error",
                    user_id=user_id,
                    channel_id=source.channel_id,
                    username=source.channel_username,
                    error=str(e),
                )

        # Start fallback polling task (catches messages if event handler misses them)
        async def poll_channels():
            logger.info(
                "fallback_polling_started", user_id=user_id, channels=list(source_state.keys())
            )

            while user_id in self._active_users:
                for channel_id, state in source_state.items():
                    try:
                        # Get new messages since last check
                        new_messages = []
                        async for msg in client.client.get_chat_history(
                            state["chat_id"],
                            limit=20,
                        ):
                            if msg.id <= state["last_msg_id"]:
                                break
                            new_messages.append(msg)

                        if new_messages:
                            # Process in chronological order (oldest first)
                            new_messages.reverse()

                            logger.info(
                                "fallback_new_messages",
                                user_id=user_id,
                                channel=state["title"],
                                count=len(new_messages),
                            )

                            for msg in new_messages:
                                await handler.process_message(msg)
                                state["last_msg_id"] = max(state["last_msg_id"], msg.id)

                    except Exception as e:
                        logger.error(
                            "poll_error",
                            user_id=user_id,
                            channel_id=channel_id,
                            error=str(e),
                        )

                await asyncio.sleep(30)  # Fallback poll every 30 seconds

        # Cancel existing task if any
        if user_id in self._polling_tasks:
            self._polling_tasks[user_id].cancel()

        self._polling_tasks[user_id] = asyncio.create_task(poll_channels())

        logger.info(
            "monitoring_started",
            user_id=user_id,
            sources=len(sources),
            target=target.title,
            is_dm=target.is_dm,
        )

    async def stop_user_monitoring(self, user_id: int) -> None:
        """
        Stop monitoring for a user.

        Args:
            user_id: Telegram user ID
        """
        logger.info("stop_monitoring", user_id=user_id)

        # Cancel polling task
        if user_id in self._polling_tasks:
            self._polling_tasks[user_id].cancel()
            try:
                await self._polling_tasks[user_id]
            except asyncio.CancelledError:
                pass
            del self._polling_tasks[user_id]

        # Remove Pyrogram handler
        if user_id in self._user_pyrogram_handlers:
            try:
                client = await self._client_manager.get_client(user_id)
                client.client.remove_handler(self._user_pyrogram_handlers[user_id])
                logger.info("event_handler_removed", user_id=user_id)
            except Exception as e:
                logger.warning("event_handler_remove_failed", user_id=user_id, error=str(e))
            del self._user_pyrogram_handlers[user_id]

        if user_id in self._active_users:
            del self._active_users[user_id]
            self._user_locks.pop(user_id, None)

    async def _handle_message(
        self,
        user_id: int,
        message: Message,
        target: ForwardTarget,
    ) -> None:
        """Handle a single message."""
        logger.info(
            "handle_message_called",
            user_id=user_id,
            chat_id=message.chat.id,
            message_id=message.id,
        )
        async with self._user_locks.get(user_id, asyncio.Lock()):
            await self._forward_message(user_id, message, target)

    async def _handle_media_group(
        self,
        user_id: int,
        messages: list[Message],
        target: ForwardTarget,
    ) -> None:
        """Handle a media group (album)."""
        async with self._user_locks.get(user_id, asyncio.Lock()):
            await self._forward_media_group(user_id, messages, target)

    def _check_keyword_filter(self, message: Message) -> bool:
        """
        Check if message passes keyword filter using whole word matching.

        Returns:
            True if message should be forwarded, False if filtered out.
        """
        import re

        keywords = settings.filter_keywords
        if not keywords:
            # No filter configured - forward all
            return True

        # Get message text (text or caption)
        text = message.text or message.caption or ""
        if not text:
            # No text to check - in whitelist mode skip, in blacklist mode allow
            return settings.filter_mode == "blacklist"

        # Set regex flags
        flags = 0 if settings.filter_case_sensitive else re.IGNORECASE

        def matches_keyword(kw: str) -> bool:
            """Check if keyword matches as a whole word."""
            escaped_kw = re.escape(kw)

            if kw.startswith("#"):
                # For hashtags: match #tag at start of text or after whitespace
                pattern = r"(?:^|(?<=\s))" + escaped_kw + r"(?=\s|$)"
            else:
                # For regular words: use word boundaries
                pattern = r"\b" + escaped_kw + r"\b"

            return bool(re.search(pattern, text, flags))

        # Check for keyword matches
        has_match = any(matches_keyword(kw) for kw in keywords)

        if has_match:
            # Log which keyword matched for debugging
            matched_kw = next((kw for kw in keywords if matches_keyword(kw)), None)
            logger.debug(
                "keyword_matched",
                keyword=matched_kw,
                message_id=message.id,
            )

        if settings.filter_mode == "whitelist":
            # Whitelist: forward only if matches
            return has_match
        else:
            # Blacklist: forward only if NOT matches
            return not has_match

    async def _forward_message(
        self,
        user_id: int,
        message: Message,
        target: ForwardTarget,
    ) -> None:
        """
        Forward a single message.

        Args:
            user_id: User ID
            message: Message to forward
            target: Forward target (channel or DM)
        """
        logger.info(
            "forward_message_start",
            user_id=user_id,
            chat_id=message.chat.id,
            message_id=message.id,
        )

        source_id = await self._get_source_id(user_id, message.chat.id)
        if not source_id:
            logger.warning(
                "source_not_found",
                user_id=user_id,
                chat_id=message.chat.id,
            )
            return

        # Check duplicate
        is_duplicate = await self._delivery_service.check_duplicate(user_id, source_id, message.id)
        if is_duplicate:
            logger.debug("duplicate_skipped", message_id=message.id)
            return

        # Check keyword filter
        if not self._check_keyword_filter(message):
            logger.info(
                "message_filtered_by_keywords",
                message_id=message.id,
                chat_id=message.chat.id,
                filter_mode=settings.filter_mode,
            )
            return

        # Create pending delivery (destination_id is None for DM mode)
        dest_id = target.destination.id if target.destination else None
        log_id = await self._delivery_service.create_pending(
            user_id=user_id,
            source_id=source_id,
            destination_id=dest_id,
            message_id=message.id,
        )

        try:
            # Get client
            client = await self._client_manager.get_client(user_id)

            # Forward message directly - preserves all formatting
            chat_id = user_id if target.is_dm else target.destination.channel_id
            forwarded = await client.client.forward_messages(
                chat_id=chat_id,
                from_chat_id=message.chat.id,
                message_ids=message.id,
            )
            result = forwarded if not isinstance(forwarded, list) else forwarded[0]
            result_id = result.id

            await self._delivery_service.mark_success(log_id, result_id)

            # Update source offset
            await self._update_source_offset(source_id, message.id)

            logger.info(
                "message_forwarded",
                user_id=user_id,
                source_id=source_id,
                message_id=message.id,
                target=target.title,
            )

        except RateLimitError as e:
            await self._delivery_service.mark_failed(log_id, str(e), will_retry=True)

        except Exception as e:
            logger.error(
                "forward_error",
                user_id=user_id,
                message_id=message.id,
                error=str(e),
            )
            await self._delivery_service.mark_failed(log_id, str(e), will_retry=False)

            if self._notify_callback:
                await self._notify_callback(
                    user_id,
                    f"Ошибка пересылки: {str(e)[:100]}",
                )

    async def _forward_media_group(
        self,
        user_id: int,
        messages: list[Message],
        target: ForwardTarget,
    ) -> None:
        """
        Forward a media group.

        Args:
            user_id: User ID
            messages: List of messages in group
            target: Forward target (channel or DM)
        """
        if not messages:
            return

        first_msg = messages[0]
        source_id = await self._get_source_id(user_id, first_msg.chat.id)
        if not source_id:
            return

        # Check duplicate using first message
        is_duplicate = await self._delivery_service.check_duplicate(
            user_id, source_id, first_msg.id
        )
        if is_duplicate:
            return

        # Check keyword filter using first message (usually has caption)
        if not self._check_keyword_filter(first_msg):
            logger.info(
                "media_group_filtered_by_keywords",
                message_id=first_msg.id,
                chat_id=first_msg.chat.id,
                count=len(messages),
                filter_mode=settings.filter_mode,
            )
            return

        # Create pending delivery
        dest_id = target.destination.id if target.destination else None
        log_id = await self._delivery_service.create_pending(
            user_id=user_id,
            source_id=source_id,
            destination_id=dest_id,
            message_id=first_msg.id,
        )

        try:
            # Get client for both DM and channel forwarding
            client = await self._client_manager.get_client(user_id)

            # Forward all messages directly - preserves all formatting and media
            chat_id = user_id if target.is_dm else target.destination.channel_id
            forwarded = await client.client.forward_messages(
                chat_id=chat_id,
                from_chat_id=first_msg.chat.id,
                message_ids=[m.id for m in messages],
            )
            result = forwarded if not isinstance(forwarded, list) else forwarded[0]
            result_id = result.id

            await self._delivery_service.mark_success(log_id, result_id)

            # Update offset with last message
            await self._update_source_offset(source_id, messages[-1].id)

            logger.info(
                "media_group_forwarded",
                user_id=user_id,
                source_id=source_id,
                count=len(messages),
                target=target.title,
            )

        except Exception as e:
            logger.error(
                "media_group_forward_error",
                user_id=user_id,
                error=str(e),
            )
            await self._delivery_service.mark_failed(log_id, str(e))

    async def _get_source_id(self, user_id: int, channel_id: int) -> int | None:
        """Get source ID for channel."""
        # Normalize channel_id: convert from Pyrogram format (-100xxx) to raw format (xxx)
        normalized_id = channel_id
        channel_str = str(channel_id)
        if channel_str.startswith("-100"):
            normalized_id = int(channel_str[4:])

        # Also try with -100 prefix if not present
        full_id = channel_id
        if not channel_str.startswith("-100") and not channel_str.startswith("-"):
            full_id = int(f"-100{channel_id}")

        logger.info(
            "get_source_id_lookup",
            user_id=user_id,
            original_channel_id=channel_id,
            normalized_id=normalized_id,
            full_id=full_id,
        )

        async with self._db.session() as session:
            source_repo = SourceRepository(session)
            # Try all possible formats
            source = await source_repo.get_by_channel(user_id, channel_id)
            if not source and normalized_id != channel_id:
                source = await source_repo.get_by_channel(user_id, normalized_id)
            if not source and full_id != channel_id:
                source = await source_repo.get_by_channel(user_id, full_id)

            logger.info(
                "get_source_id_result",
                user_id=user_id,
                found=source is not None,
                source_id=source.id if source else None,
                source_channel_id=source.channel_id if source else None,
            )
            return source.id if source else None

    async def _update_source_offset(self, source_id: int, message_id: int) -> None:
        """Update source's last processed message ID."""
        async with self._db.session() as session:
            source_repo = SourceRepository(session)
            await source_repo.update_last_message(source_id, message_id)
