import asyncio
import base64
import io
from collections.abc import AsyncIterator

import qrcode
import structlog
from pyrogram import Client, raw
from pyrogram.errors import (
    AuthKeyUnregistered,
    FloodWait,
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    SessionPasswordNeeded,
    UserDeactivated,
)
from pyrogram.types import Chat, Dialog, Message

from src.app.config import settings
from src.shared.exceptions import AuthError, RateLimitError, SessionError

logger = structlog.get_logger()


class MTProtoClient:
    """Wrapper around Pyrogram client for MTProto operations."""

    def __init__(
        self,
        user_id: int,
        session_string: str | None = None,
    ):
        """
        Initialize MTProto client for a user.

        Args:
            user_id: Telegram user ID (used as session name)
            session_string: Optional existing session string
        """
        self.user_id = user_id
        self._client: Client | None = None
        self._session_string = session_string
        self._connected = False

    @property
    def client(self) -> Client:
        """Get underlying Pyrogram client."""
        if not self._client:
            raise SessionError("Client not initialized")
        return self._client

    async def initialize(self) -> None:
        """Initialize the Pyrogram client."""
        self._client = Client(
            name=f"user_{self.user_id}",
            api_id=settings.api_id,
            api_hash=settings.api_hash.get_secret_value(),
            session_string=self._session_string,
            in_memory=True,
            # Make client look like regular Telegram Desktop
            device_model="Desktop",
            system_version="Windows 10",
            app_version="4.16.8",
        )

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._client is not None and self._client.is_connected

    async def connect(self) -> None:
        """Connect to Telegram."""
        if not self._client:
            await self.initialize()

        if not self.is_connected:
            await self._client.connect()

    async def disconnect(self) -> None:
        """Disconnect from Telegram."""
        if self._client:
            try:
                if self._client.is_initialized and self._client.is_connected:
                    await self._client.disconnect()
            except ConnectionError:
                # Client not properly initialized or already disconnected
                pass

    async def send_code(self, phone: str) -> dict:
        """
        Send verification code to phone.

        Args:
            phone: Phone number in international format

        Returns:
            Dict with phone_code_hash
        """
        await self.connect()

        try:
            sent_code = await self._client.send_code(phone)
            logger.info(
                "code_sent",
                user_id=self.user_id,
                hash_prefix=sent_code.phone_code_hash[:8],
                code_type=str(sent_code.type),
            )
            return {
                "phone_code_hash": sent_code.phone_code_hash,
                "type": sent_code.type,
            }
        except FloodWait as e:
            raise RateLimitError("Too many attempts", retry_after=e.value)
        except Exception as e:
            logger.error("send_code_error", user_id=self.user_id, error=str(e))
            raise AuthError(str(e), "Не удалось отправить код. Проверь номер телефона.")

    async def export_qr_token(self) -> dict:
        """
        Export QR login token.

        Returns:
            Dict with token, qr_url, qr_image_bytes, and expires_at
        """
        await self.connect()

        try:
            result = await self._client.invoke(
                raw.functions.auth.ExportLoginToken(
                    api_id=settings.api_id,
                    api_hash=settings.api_hash.get_secret_value(),
                    except_ids=[],
                )
            )

            if isinstance(result, raw.types.auth.LoginToken):
                # Convert token to base64url for QR code
                token_b64 = base64.urlsafe_b64encode(result.token).decode().rstrip("=")
                qr_url = f"tg://login?token={token_b64}"

                # Generate QR code image
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=4,
                )
                qr.add_data(qr_url)
                qr.make(fit=True)

                img = qr.make_image(fill_color="black", back_color="white")
                img_bytes = io.BytesIO()
                img.save(img_bytes, format="PNG")
                img_bytes.seek(0)

                logger.info(
                    "qr_token_exported",
                    user_id=self.user_id,
                    expires=result.expires,
                )

                return {
                    "token": result.token,
                    "qr_url": qr_url,
                    "qr_image": img_bytes.getvalue(),
                    "expires_at": result.expires,
                }

            elif isinstance(result, raw.types.auth.LoginTokenMigrateTo):
                # Need to switch DC
                raise AuthError("DC migration required", "Требуется переключение дата-центра.")

            elif isinstance(result, raw.types.auth.LoginTokenSuccess):
                # Already authorized
                return {"success": True, "authorization": result.authorization}

            raise AuthError("Unknown response", "Неизвестный ответ сервера.")

        except FloodWait as e:
            raise RateLimitError("Too many attempts", retry_after=e.value)
        except AuthError:
            raise
        except Exception as e:
            logger.error("export_qr_token_error", user_id=self.user_id, error=str(e))
            raise AuthError(str(e), "Не удалось создать QR-код.")

    async def check_qr_login(self) -> dict:
        """
        Check QR login status by re-exporting token.

        Returns:
            Dict with status: 'pending', 'success', or 'needs_2fa'
        """
        try:
            result = await self._client.invoke(
                raw.functions.auth.ExportLoginToken(
                    api_id=settings.api_id,
                    api_hash=settings.api_hash.get_secret_value(),
                    except_ids=[],
                )
            )

            if isinstance(result, raw.types.auth.LoginTokenSuccess):
                # Authorization successful
                auth = result.authorization

                if isinstance(auth, raw.types.auth.AuthorizationSignUpRequired):
                    raise AuthError("Sign up required", "Требуется регистрация аккаунта.")

                # Extract user from authorization and update Pyrogram's internal state
                if isinstance(auth, raw.types.auth.Authorization):
                    user = auth.user
                    # Update Pyrogram storage with user info
                    await self._client.storage.user_id(user.id)
                    await self._client.storage.is_bot(False)

                logger.info("qr_login_success", user_id=self.user_id)
                return {"status": "success"}

            elif isinstance(result, raw.types.auth.LoginToken):
                # Still waiting
                return {"status": "pending", "expires_at": result.expires}

            return {"status": "pending"}

        except SessionPasswordNeeded:
            return {"status": "needs_2fa"}

        except FloodWait as e:
            raise RateLimitError("Too many attempts", retry_after=e.value)

        except Exception as e:
            logger.error("check_qr_login_error", user_id=self.user_id, error=str(e))
            raise AuthError(str(e), "Ошибка проверки QR авторизации.")

    async def sign_in(
        self,
        phone: str,
        code: str,
        phone_code_hash: str,
    ) -> dict:
        """
        Sign in with verification code.

        Args:
            phone: Phone number
            code: Verification code
            phone_code_hash: Hash from send_code

        Returns:
            Dict with result status
        """
        logger.info(
            "sign_in_attempt",
            user_id=self.user_id,
            phone=phone[-4:],  # Last 4 digits only
            code_len=len(code),
            hash_prefix=phone_code_hash[:8] if phone_code_hash else None,
            is_connected=self.is_connected,
        )

        # Ensure connection is active
        if not self.is_connected:
            logger.warning("client_not_connected_reconnecting", user_id=self.user_id)
            await self.connect()

        try:
            await self._client.sign_in(
                phone_number=phone,
                phone_code_hash=phone_code_hash,
                phone_code=code,
            )
            return {"success": True, "needs_2fa": False}

        except SessionPasswordNeeded:
            return {"success": False, "needs_2fa": True}

        except PhoneCodeInvalid:
            raise AuthError("Invalid code", "Неверный код подтверждения.")

        except PhoneCodeExpired:
            raise AuthError("Code expired", "Код истёк. Запроси новый.")

        except FloodWait as e:
            raise RateLimitError("Too many attempts", retry_after=e.value)

    async def check_password(self, password: str) -> dict:
        """
        Check 2FA password.

        Args:
            password: Cloud password

        Returns:
            Dict with result status
        """
        try:
            await self._client.check_password(password)
            return {"success": True}

        except PasswordHashInvalid:
            raise AuthError("Invalid password", "Неверный облачный пароль.")

        except FloodWait as e:
            raise RateLimitError("Too many attempts", retry_after=e.value)

    async def get_session_string(self) -> str:
        """
        Export current session as string.

        Returns:
            Session string for storage
        """
        return await self._client.export_session_string()

    async def is_authorized(self) -> bool:
        """Check if client is authorized."""
        try:
            await self.connect()
            me = await self._client.get_me()
            return me is not None
        except (AuthKeyUnregistered, UserDeactivated):
            return False
        except Exception:
            return False

    async def get_dialogs(self, limit: int = 100) -> list[Dialog]:
        """
        Get user's dialogs (chats).

        Args:
            limit: Maximum number of dialogs

        Returns:
            List of dialogs
        """
        dialogs = []
        async for dialog in self._client.get_dialogs(limit=limit):
            dialogs.append(dialog)
        return dialogs

    async def warm_cache(self, limit: int = 200) -> int:
        """
        Warm up Pyrogram's peer cache by loading dialogs.

        This prevents 'Peer id invalid' errors when receiving updates
        from channels that aren't in the local cache.

        Args:
            limit: Maximum number of dialogs to load

        Returns:
            Number of dialogs loaded
        """
        try:
            dialogs = await self.get_dialogs(limit=limit)
            logger.info(
                "cache_warmed",
                user_id=self.user_id,
                dialogs_loaded=len(dialogs),
            )
            return len(dialogs)
        except Exception as e:
            logger.warning(
                "cache_warm_failed",
                user_id=self.user_id,
                error=str(e),
            )
            return 0

    async def get_chat(self, chat_id: int | str) -> Chat:
        """
        Get chat info.

        Args:
            chat_id: Chat ID or username

        Returns:
            Chat object
        """
        try:
            return await self._client.get_chat(chat_id)
        except FloodWait as e:
            raise RateLimitError("Rate limited", retry_after=e.value)

    async def is_subscribed(self, channel_username: str) -> bool:
        """
        Check if user is subscribed to channel.

        Args:
            channel_username: Channel username without @

        Returns:
            True if subscribed
        """
        try:
            chat = await self._client.get_chat(channel_username)
            # If we can access the chat, user is subscribed
            return True
        except Exception:
            return False

    async def iter_channel_messages(
        self,
        channel_id: int,
        offset_id: int = 0,
        limit: int = 100,
    ) -> AsyncIterator[Message]:
        """
        Iterate over channel messages.

        Args:
            channel_id: Channel ID
            offset_id: Start from this message ID
            limit: Maximum messages to fetch

        Yields:
            Message objects
        """
        async for message in self._client.get_chat_history(
            chat_id=channel_id,
            limit=limit,
            offset_id=offset_id,
        ):
            yield message

    async def copy_message(
        self,
        chat_id: int,
        from_chat_id: int,
        message_id: int,
    ) -> Message:
        """
        Copy message to another chat.

        Args:
            chat_id: Destination chat ID
            from_chat_id: Source chat ID
            message_id: Message ID to copy

        Returns:
            Sent message
        """
        try:
            return await self._client.copy_message(
                chat_id=chat_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
        except FloodWait as e:
            raise RateLimitError("Rate limited", retry_after=e.value)

    async def send_media_group(
        self,
        chat_id: int,
        media: list,
    ) -> list[Message]:
        """
        Send media group (album).

        Args:
            chat_id: Destination chat ID
            media: List of InputMedia objects

        Returns:
            List of sent messages
        """
        try:
            return await self._client.send_media_group(
                chat_id=chat_id,
                media=media,
            )
        except FloodWait as e:
            raise RateLimitError("Rate limited", retry_after=e.value)

    async def send_poll(
        self,
        chat_id: int,
        question: str,
        options: list[str],
        **kwargs,
    ) -> Message:
        """
        Send poll to chat.

        Args:
            chat_id: Destination chat ID
            question: Poll question
            options: Poll options

        Returns:
            Sent message
        """
        try:
            return await self._client.send_poll(
                chat_id=chat_id,
                question=question,
                options=options,
                **kwargs,
            )
        except FloodWait as e:
            raise RateLimitError("Rate limited", retry_after=e.value)


class MTProtoClientManager:
    """Manages multiple MTProto clients for different users."""

    def __init__(self):
        self._clients: dict[int, MTProtoClient] = {}
        self._lock = asyncio.Lock()

    async def get_client(
        self,
        user_id: int,
        session_string: str | None = None,
    ) -> MTProtoClient:
        """
        Get or create client for user.

        Args:
            user_id: Telegram user ID
            session_string: Optional session string

        Returns:
            MTProtoClient instance
        """
        async with self._lock:
            existing = self._clients.get(user_id)

            # If client exists but was created without session and now we have one,
            # recreate it with the proper session
            if existing is not None:
                if session_string and existing._session_string != session_string:
                    logger.info(
                        "recreating_client_with_session",
                        user_id=user_id,
                        had_session=existing._session_string is not None,
                    )
                    await existing.disconnect()
                    client = MTProtoClient(user_id, session_string)
                    await client.initialize()
                    self._clients[user_id] = client
                    return client
                return existing

            # Create new client
            client = MTProtoClient(user_id, session_string)
            await client.initialize()
            self._clients[user_id] = client
            return client

    async def remove_client(self, user_id: int) -> None:
        """
        Remove and disconnect client.

        Args:
            user_id: Telegram user ID
        """
        async with self._lock:
            if user_id in self._clients:
                client = self._clients.pop(user_id)
                await client.disconnect()

    async def close_all(self) -> None:
        """Close all clients."""
        async with self._lock:
            for client in self._clients.values():
                await client.disconnect()
            self._clients.clear()
