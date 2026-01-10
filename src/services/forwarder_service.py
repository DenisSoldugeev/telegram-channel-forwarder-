import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO

import structlog
from pyrogram.types import (
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from telegram import Bot as TelegramBot
from telegram import InputMediaPhoto as BotInputMediaPhoto
from telegram import InputMediaVideo as BotInputMediaVideo
from telegram import InputMediaDocument as BotInputMediaDocument

from src.app.config import settings
from src.mtproto.client import MTProtoClient, MTProtoClientManager
from src.mtproto.handlers.new_message import MessageHandler, determine_message_type
from src.mtproto.session_manager import SessionManager
from src.services.delivery_service import DeliveryService
from src.shared.constants import MessageType
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
        )

        # Add channels to monitor
        for source in sources:
            handler.add_channel(source.channel_id)

        # Start Pyrogram client
        if not client.client.is_initialized:
            logger.info("starting_pyrogram_client", user_id=user_id)
            await client.client.start()
        else:
            logger.info("client_already_initialized", user_id=user_id)

        # Register event handler for instant delivery (works for subscribed channels)
        client.client.add_handler(handler.get_pyrogram_handler())
        logger.info("event_handler_registered", user_id=user_id)

        self._active_users[user_id] = handler
        self._user_locks[user_id] = asyncio.Lock()

        # Build source info for polling: {channel_id: last_message_id}
        source_state = {}
        for source in sources:
            # Get current last message to start from
            try:
                # Try username first (more reliable), then channel_id
                chat_identifier = source.channel_username or source.channel_id

                logger.info(
                    "resolving_channel",
                    user_id=user_id,
                    channel_id=source.channel_id,
                    username=source.channel_username,
                    using=chat_identifier,
                )

                chat = await client.client.get_chat(chat_identifier)

                # Get latest message ID to not process old messages
                async for msg in client.client.get_chat_history(chat.id, limit=1):
                    source_state[source.channel_id] = {
                        'last_msg_id': msg.id,
                        'chat_id': chat.id,
                        'title': chat.title,
                    }
                    break
                else:
                    source_state[source.channel_id] = {
                        'last_msg_id': 0,
                        'chat_id': chat.id,
                        'title': chat.title,
                    }
                logger.info(
                    "source_initialized",
                    user_id=user_id,
                    channel_id=source.channel_id,
                    chat_title=chat.title,
                    last_msg_id=source_state[source.channel_id]['last_msg_id'],
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
            logger.info("fallback_polling_started", user_id=user_id, channels=list(source_state.keys()))

            while user_id in self._active_users:
                for channel_id, state in source_state.items():
                    try:
                        # Get new messages since last check
                        new_messages = []
                        async for msg in client.client.get_chat_history(
                            state['chat_id'],
                            limit=20,
                        ):
                            if msg.id <= state['last_msg_id']:
                                break
                            new_messages.append(msg)

                        if new_messages:
                            # Process in chronological order (oldest first)
                            new_messages.reverse()

                            logger.info(
                                "fallback_new_messages",
                                user_id=user_id,
                                channel=state['title'],
                                count=len(new_messages),
                            )

                            for msg in new_messages:
                                await handler.process_message(msg)
                                state['last_msg_id'] = max(state['last_msg_id'], msg.id)

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
        is_duplicate = await self._delivery_service.check_duplicate(
            user_id, source_id, message.id
        )
        if is_duplicate:
            logger.debug("duplicate_skipped", message_id=message.id)
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
            msg_type = determine_message_type(message)

            logger.info(
                "forwarding_attempt",
                user_id=user_id,
                from_chat_id=message.chat.id,
                to_target=target.title,
                is_dm=target.is_dm,
                message_id=message.id,
            )

            # Get client for both DM and channel forwarding
            client = await self._client_manager.get_client(user_id)

            if target.is_dm:
                # Forward to user's DM via Bot API (download via MTProto, upload via Bot API)
                result_id = await self._forward_to_dm(user_id, message, client)
            else:

                # Ensure destination channel is in Pyrogram's cache
                try:
                    if target.destination.channel_username:
                        await client.client.get_chat(target.destination.channel_username)
                    else:
                        await client.client.get_chat(target.destination.channel_id)
                except Exception as e:
                    logger.warning("destination_cache_error", error=str(e))

                if msg_type == MessageType.POLL:
                    result = await self._forward_poll(client, message, target.destination.channel_id)
                else:
                    result = await client.copy_message(
                        chat_id=target.destination.channel_id,
                        from_chat_id=message.chat.id,
                        message_id=message.id,
                    )
                result_id = result.id

            await self._delivery_service.mark_success(log_id, result_id)

            # Update source offset
            await self._update_source_offset(source_id, message.id)

            logger.info(
                "message_forwarded",
                user_id=user_id,
                source_id=source_id,
                message_id=message.id,
                type=msg_type.value,
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

            if target.is_dm:
                # Forward to DM via Bot API (download via MTProto, upload via Bot API)
                result_id = await self._forward_media_group_to_dm(user_id, messages, client)
            else:

                # Build media group
                media_list = []
                for i, msg in enumerate(messages):
                    caption = msg.caption if i == 0 else None
                    caption_entities = msg.caption_entities if i == 0 else None

                    if msg.photo:
                        media = InputMediaPhoto(
                            media=msg.photo.file_id,
                            caption=caption,
                            caption_entities=caption_entities,
                        )
                    elif msg.video:
                        media = InputMediaVideo(
                            media=msg.video.file_id,
                            caption=caption,
                            caption_entities=caption_entities,
                        )
                    elif msg.document:
                        media = InputMediaDocument(
                            media=msg.document.file_id,
                            caption=caption,
                        )
                    else:
                        continue

                    media_list.append(media)

                if media_list:
                    results = await client.send_media_group(
                        chat_id=target.destination.channel_id,
                        media=media_list,
                    )
                    result_id = results[0].id
                else:
                    return

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

    async def _forward_poll(
        self,
        client: MTProtoClient,
        message: Message,
        destination_id: int,
    ) -> Message:
        """Forward a poll by recreating it."""
        poll = message.poll

        return await client.send_poll(
            chat_id=destination_id,
            question=poll.question,
            options=[opt.text for opt in poll.options],
            is_anonymous=poll.is_anonymous,
            type=poll.type.value if poll.type else "regular",
            allows_multiple_answers=poll.allows_multiple_answers,
            correct_option_id=poll.correct_option_id,
            explanation=poll.explanation,
            explanation_entities=poll.explanation_entities,
        )

    async def _forward_to_dm(
        self,
        user_id: int,
        message: Message,
        client: MTProtoClient,
    ) -> int:
        """
        Forward message to user's DM via Bot API with full media support.

        Args:
            user_id: User ID to send to
            message: Pyrogram message to forward
            client: MTProto client for downloading media

        Returns:
            Sent message ID
        """
        if not self._bot:
            raise ForwardError("Bot not set", "Бот не инициализирован")

        # Build caption with source info
        source_title = message.chat.title or message.chat.username or "Unknown"
        source_link = self._get_message_link(message)

        original_text = message.text or message.caption or ""
        header = f"📢 <b>{source_title}</b>"
        if source_link:
            header += f" • <a href=\"{source_link}\">Оригинал</a>"

        caption = f"{header}\n\n{original_text}" if original_text else header

        # Check file size - if too large, send text only
        file_size = self._get_media_size(message)
        if file_size and file_size > settings.dm_max_media_size_mb * 1024 * 1024:
            logger.info(
                "media_too_large_for_dm",
                user_id=user_id,
                file_size_mb=round(file_size / (1024 * 1024), 1),
                limit_mb=settings.dm_max_media_size_mb,
            )
            # Send text with link only
            size_mb = file_size / (1024 * 1024)
            caption += f"\n\n⚠️ Медиа слишком большое ({size_mb:.1f} МБ), перейдите по ссылке для просмотра."

            if len(caption) > 4096:
                caption = caption[:4093] + "..."

            result = await self._bot.send_message(
                chat_id=user_id,
                text=caption,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return result.message_id

        # Truncate caption if too long (Telegram limit is 1024 for media, 4096 for text)
        max_caption_len = 1024 if (message.photo or message.video or message.document or message.animation) else 4096
        if len(caption) > max_caption_len:
            caption = caption[:max_caption_len - 3] + "..."

        # Handle different message types
        if message.photo:
            # Download photo to memory
            photo_bytes = await client.client.download_media(message, in_memory=True)
            photo_data = BytesIO(photo_bytes.getvalue() if hasattr(photo_bytes, 'getvalue') else photo_bytes)

            result = await self._bot.send_photo(
                chat_id=user_id,
                photo=photo_data,
                caption=caption,
                parse_mode="HTML",
            )
        elif message.video:
            # Download video to memory
            video_bytes = await client.client.download_media(message, in_memory=True)
            video_data = BytesIO(video_bytes.getvalue() if hasattr(video_bytes, 'getvalue') else video_bytes)

            result = await self._bot.send_video(
                chat_id=user_id,
                video=video_data,
                caption=caption,
                parse_mode="HTML",
            )
        elif message.animation:
            # Download animation (GIF) to memory
            anim_bytes = await client.client.download_media(message, in_memory=True)
            anim_data = BytesIO(anim_bytes.getvalue() if hasattr(anim_bytes, 'getvalue') else anim_bytes)

            result = await self._bot.send_animation(
                chat_id=user_id,
                animation=anim_data,
                caption=caption,
                parse_mode="HTML",
            )
        elif message.document:
            # Download document to memory
            doc_bytes = await client.client.download_media(message, in_memory=True)
            doc_data = BytesIO(doc_bytes.getvalue() if hasattr(doc_bytes, 'getvalue') else doc_bytes)

            result = await self._bot.send_document(
                chat_id=user_id,
                document=doc_data,
                caption=caption,
                parse_mode="HTML",
                filename=message.document.file_name,
            )
        elif message.audio:
            # Download audio to memory
            audio_bytes = await client.client.download_media(message, in_memory=True)
            audio_data = BytesIO(audio_bytes.getvalue() if hasattr(audio_bytes, 'getvalue') else audio_bytes)

            result = await self._bot.send_audio(
                chat_id=user_id,
                audio=audio_data,
                caption=caption,
                parse_mode="HTML",
            )
        elif message.voice:
            # Download voice to memory - may fail due to privacy settings
            try:
                voice_bytes = await client.client.download_media(message, in_memory=True)
                voice_data = BytesIO(voice_bytes.getvalue() if hasattr(voice_bytes, 'getvalue') else voice_bytes)

                result = await self._bot.send_voice(
                    chat_id=user_id,
                    voice=voice_data,
                    caption=caption,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning("voice_send_failed", user_id=user_id, error=str(e))
                # Fallback to link
                caption += "\n\n🎤 Голосовое сообщение — перейдите по ссылке для прослушивания."
                result = await self._bot.send_message(
                    chat_id=user_id,
                    text=caption,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
        elif message.video_note:
            # Video notes may fail due to privacy settings
            try:
                vnote_bytes = await client.client.download_media(message, in_memory=True)
                vnote_data = BytesIO(vnote_bytes.getvalue() if hasattr(vnote_bytes, 'getvalue') else vnote_bytes)

                result = await self._bot.send_video_note(
                    chat_id=user_id,
                    video_note=vnote_data,
                )
            except Exception as e:
                logger.warning("video_note_send_failed", user_id=user_id, error=str(e))
                # Fallback to link
                fallback_text = f"{header}\n\n🔵 Видеокружок — перейдите по ссылке для просмотра."
                result = await self._bot.send_message(
                    chat_id=user_id,
                    text=fallback_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
        elif message.sticker:
            # Download sticker to memory
            sticker_bytes = await client.client.download_media(message, in_memory=True)
            sticker_data = BytesIO(sticker_bytes.getvalue() if hasattr(sticker_bytes, 'getvalue') else sticker_bytes)

            result = await self._bot.send_sticker(
                chat_id=user_id,
                sticker=sticker_data,
            )
        else:
            # Text message or unsupported type - send as text
            result = await self._bot.send_message(
                chat_id=user_id,
                text=caption,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        return result.message_id

    async def _forward_media_group_to_dm(
        self,
        user_id: int,
        messages: list[Message],
        client: MTProtoClient,
    ) -> int:
        """
        Forward media group to user's DM via Bot API with full media support.

        Args:
            user_id: User ID to send to
            messages: List of Pyrogram messages in group
            client: MTProto client for downloading media

        Returns:
            First sent message ID
        """
        if not self._bot:
            raise ForwardError("Bot not set", "Бот не инициализирован")

        first_msg = messages[0]
        source_title = first_msg.chat.title or first_msg.chat.username or "Unknown"
        source_link = self._get_message_link(first_msg)

        # Build caption for first media item
        original_caption = first_msg.caption or ""
        header = f"📢 <b>{source_title}</b>"
        if source_link:
            header += f" • <a href=\"{source_link}\">Оригинал</a>"

        caption = f"{header}\n\n{original_caption}" if original_caption else header

        # Check total size of album
        total_size = sum(self._get_media_size(msg) or 0 for msg in messages)
        if total_size > settings.dm_max_media_size_mb * 1024 * 1024:
            logger.info(
                "media_group_too_large_for_dm",
                user_id=user_id,
                total_size_mb=round(total_size / (1024 * 1024), 1),
                count=len(messages),
                limit_mb=settings.dm_max_media_size_mb,
            )
            # Send text with link only
            size_mb = total_size / (1024 * 1024)
            caption += f"\n\n⚠️ Альбом слишком большой ({size_mb:.1f} МБ, {len(messages)} файлов), перейдите по ссылке для просмотра."

            if len(caption) > 4096:
                caption = caption[:4093] + "..."

            result = await self._bot.send_message(
                chat_id=user_id,
                text=caption,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return result.message_id

        if len(caption) > 1024:
            caption = caption[:1021] + "..."

        # Download all media and build media group
        media_list = []
        for i, msg in enumerate(messages):
            # Download to memory
            media_bytes = await client.client.download_media(msg, in_memory=True)
            media_data = BytesIO(media_bytes.getvalue() if hasattr(media_bytes, 'getvalue') else media_bytes)

            # Only first item gets caption
            item_caption = caption if i == 0 else None

            if msg.photo:
                media_list.append(BotInputMediaPhoto(
                    media=media_data,
                    caption=item_caption,
                    parse_mode="HTML" if item_caption else None,
                ))
            elif msg.video:
                media_list.append(BotInputMediaVideo(
                    media=media_data,
                    caption=item_caption,
                    parse_mode="HTML" if item_caption else None,
                ))
            elif msg.document:
                media_list.append(BotInputMediaDocument(
                    media=media_data,
                    caption=item_caption,
                    parse_mode="HTML" if item_caption else None,
                ))

        if media_list:
            results = await self._bot.send_media_group(
                chat_id=user_id,
                media=media_list,
            )
            return results[0].message_id
        else:
            # Fallback to text if no media
            result = await self._bot.send_message(
                chat_id=user_id,
                text=caption,
                parse_mode="HTML",
            )
            return result.message_id

    def _get_message_link(self, message: Message) -> str | None:
        """Get link to original message."""
        if message.chat.username:
            return f"https://t.me/{message.chat.username}/{message.id}"
        # For private channels, use c/ format
        chat_id = str(message.chat.id)
        if chat_id.startswith("-100"):
            chat_id = chat_id[4:]  # Remove -100 prefix
        return f"https://t.me/c/{chat_id}/{message.id}"

    def _get_media_size(self, message: Message) -> int | None:
        """Get file size of media in message."""
        if message.photo:
            # Photo has multiple sizes, get the largest
            return message.photo.file_size
        elif message.video:
            return message.video.file_size
        elif message.animation:
            return message.animation.file_size
        elif message.document:
            return message.document.file_size
        elif message.audio:
            return message.audio.file_size
        elif message.voice:
            return message.voice.file_size
        elif message.video_note:
            return message.video_note.file_size
        elif message.sticker:
            return message.sticker.file_size
        return None

    async def _get_source_id(self, user_id: int, channel_id: int) -> int | None:
        """Get source ID for channel."""
        # Normalize channel_id: convert from Pyrogram format (-100xxx) to raw format (xxx)
        normalized_id = channel_id
        channel_str = str(channel_id)
        if channel_str.startswith("-100"):
            normalized_id = int(channel_str[4:])

        logger.debug(
            "get_source_id",
            user_id=user_id,
            original_channel_id=channel_id,
            normalized_id=normalized_id,
        )

        async with self._db.session() as session:
            source_repo = SourceRepository(session)
            # Try normalized ID first
            source = await source_repo.get_by_channel(user_id, normalized_id)
            if not source:
                # Try original ID (in case DB stores full format)
                source = await source_repo.get_by_channel(user_id, channel_id)

            logger.debug(
                "get_source_id_result",
                user_id=user_id,
                found=source is not None,
                source_id=source.id if source else None,
            )
            return source.id if source else None

    async def _update_source_offset(self, source_id: int, message_id: int) -> None:
        """Update source's last processed message ID."""
        async with self._db.session() as session:
            source_repo = SourceRepository(session)
            await source_repo.update_last_message(source_id, message_id)
