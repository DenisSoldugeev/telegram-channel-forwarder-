import asyncio
from typing import Callable

import structlog
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler as PyrogramMessageHandler
from pyrogram.types import Message

from src.shared.constants import MessageType

logger = structlog.get_logger()


def determine_message_type(message: Message) -> MessageType:
    """
    Determine the type of message for forwarding.

    Args:
        message: Pyrogram message object

    Returns:
        MessageType enum value
    """
    if message.media_group_id:
        return MessageType.MEDIA_GROUP
    if message.poll:
        return MessageType.POLL
    if message.sticker:
        return MessageType.STICKER
    if message.animation:
        return MessageType.ANIMATION
    if message.video_note:
        return MessageType.VIDEO_NOTE
    if message.voice:
        return MessageType.VOICE
    if message.video:
        return MessageType.VIDEO
    if message.photo:
        return MessageType.PHOTO
    if message.audio:
        return MessageType.AUDIO
    if message.document:
        return MessageType.DOCUMENT
    if message.location:
        return MessageType.LOCATION
    if message.contact:
        return MessageType.CONTACT
    if message.text:
        return MessageType.TEXT

    return MessageType.UNSUPPORTED


class MediaGroupCollector:
    """Collects media group messages for batch forwarding."""

    def __init__(self, timeout: float = 2.0):
        """
        Initialize collector.

        Args:
            timeout: Seconds to wait for all group messages
        """
        self.timeout = timeout
        self._groups: dict[str, list[Message]] = {}
        self._timers: dict[str, asyncio.Task] = {}
        self._callbacks: dict[str, Callable] = {}
        self._lock = asyncio.Lock()

    async def add_message(
        self,
        message: Message,
        callback: Callable[[list[Message]], None],
    ) -> None:
        """
        Add message to group collector.

        Args:
            message: Message to add
            callback: Callback to call when group is complete
        """
        group_id = message.media_group_id
        if not group_id:
            return

        async with self._lock:
            if group_id not in self._groups:
                self._groups[group_id] = []
                self._callbacks[group_id] = callback

                # Schedule flush
                self._timers[group_id] = asyncio.create_task(
                    self._schedule_flush(group_id)
                )

            self._groups[group_id].append(message)

    async def _schedule_flush(self, group_id: str) -> None:
        """Wait and flush the group."""
        await asyncio.sleep(self.timeout)

        async with self._lock:
            if group_id in self._groups:
                messages = self._groups.pop(group_id)
                callback = self._callbacks.pop(group_id)
                self._timers.pop(group_id, None)

                # Sort by message ID for correct order
                messages.sort(key=lambda m: m.id)

                # Call the callback
                if callback:
                    await callback(messages)


class MessageHandler:
    """Handles new messages from monitored channels."""

    def __init__(
        self,
        on_message: Callable[[Message], None],
        on_media_group: Callable[[list[Message]], None],
        media_group_timeout: float = 2.0,
    ):
        """
        Initialize handler.

        Args:
            on_message: Callback for single messages
            on_media_group: Callback for media groups
            media_group_timeout: Timeout for collecting media groups
        """
        self._on_message = on_message
        self._on_media_group = on_media_group
        self._media_collector = MediaGroupCollector(timeout=media_group_timeout)
        self._monitored_channels: set[int] = set()

    def add_channel(self, channel_id: int) -> None:
        """Add channel to monitor."""
        self._monitored_channels.add(channel_id)

    def remove_channel(self, channel_id: int) -> None:
        """Remove channel from monitoring."""
        self._monitored_channels.discard(channel_id)

    async def handle_message(self, client: Client, message: Message) -> None:
        """
        Handle incoming message.

        Args:
            client: Pyrogram client
            message: Incoming message
        """
        # Log ALL incoming messages for debugging
        logger.info(
            "incoming_message",
            chat_id=message.chat.id,
            chat_title=getattr(message.chat, "title", None),
            message_id=message.id,
            monitored_channels=list(self._monitored_channels),
        )

        # Check if we're monitoring this channel
        if message.chat.id not in self._monitored_channels:
            logger.debug("skipping_unmonitored", chat_id=message.chat.id)
            return

        logger.info(
            "processing_message",
            chat_id=message.chat.id,
            message_id=message.id,
            type=determine_message_type(message).value,
        )

        message_type = determine_message_type(message)

        if message_type == MessageType.MEDIA_GROUP:
            # Collect media group
            await self._media_collector.add_message(
                message,
                self._on_media_group,
            )
        elif message_type != MessageType.UNSUPPORTED:
            # Forward single message
            await self._on_message(message)

    def get_pyrogram_handler(self) -> PyrogramMessageHandler:
        """Get Pyrogram handler for registration."""
        return PyrogramMessageHandler(
            self.handle_message,
            filters=filters.channel,  # Handle channel posts
        )
