from dataclasses import dataclass

import structlog

from src.mtproto.client import MTProtoClient, MTProtoClientManager
from src.mtproto.session_manager import SessionManager
from src.shared.constants import BotState
from src.shared.exceptions import AuthError
from src.shared.utils.validators import normalize_phone
from src.storage.database import Database
from src.storage.repositories import UserRepository

logger = structlog.get_logger()


@dataclass
class AuthResult:
    """Result of authentication operation."""

    success: bool = False
    needs_code: bool = False
    needs_2fa: bool = False
    phone_code_hash: str | None = None
    error: str | None = None


class AuthService:
    """Service for handling user authentication."""

    def __init__(
        self,
        database: Database,
        session_manager: SessionManager,
        client_manager: MTProtoClientManager,
    ):
        """
        Initialize auth service.

        Args:
            database: Database instance
            session_manager: Session manager
            client_manager: MTProto client manager
        """
        self._db = database
        self._session_manager = session_manager
        self._client_manager = client_manager
        self._pending_auth: dict[int, dict] = {}

    async def start_auth(self, user_id: int, phone: str) -> AuthResult:
        """
        Start authentication flow.

        Args:
            user_id: Telegram user ID
            phone: Phone number

        Returns:
            AuthResult with next step info
        """
        logger.info("start_auth", user_id=user_id)

        phone = normalize_phone(phone)

        # Get or create client
        client = await self._client_manager.get_client(user_id)

        try:
            result = await client.send_code(phone)

            # Store pending auth data
            self._pending_auth[user_id] = {
                "phone": phone,
                "phone_code_hash": result["phone_code_hash"],
            }

            # Update user state
            async with self._db.session() as session:
                user_repo = UserRepository(session)
                await user_repo.update_state(user_id, BotState.AWAITING_CODE.value)

            return AuthResult(
                needs_code=True,
                phone_code_hash=result["phone_code_hash"],
            )

        except Exception as e:
            logger.error("start_auth_error", user_id=user_id, error=str(e))
            raise AuthError(str(e), "Не удалось отправить код. Проверь номер.")

    async def verify_code(
        self,
        user_id: int,
        phone: str,
        code: str,
        phone_code_hash: str,
    ) -> AuthResult:
        """
        Verify SMS/Telegram code.

        Args:
            user_id: Telegram user ID
            phone: Phone number
            code: Verification code
            phone_code_hash: Hash from send_code

        Returns:
            AuthResult with status
        """
        logger.info("verify_code", user_id=user_id)

        client = await self._client_manager.get_client(user_id)
        pending = self._pending_auth.get(user_id, {})
        phone = pending.get("phone", phone)
        phone_code_hash = pending.get("phone_code_hash", phone_code_hash)

        try:
            result = await client.sign_in(phone, code, phone_code_hash)

            if result.get("needs_2fa"):
                async with self._db.session() as session:
                    user_repo = UserRepository(session)
                    await user_repo.update_state(user_id, BotState.AWAITING_2FA.value)

                return AuthResult(needs_2fa=True)

            if result.get("success"):
                await self._finalize_auth(user_id, client)
                return AuthResult(success=True)

            return AuthResult(error="Unknown error")

        except AuthError:
            raise
        except Exception as e:
            logger.error("verify_code_error", user_id=user_id, error=str(e))
            raise AuthError(str(e), "Ошибка проверки кода.")

    async def verify_2fa(self, user_id: int, password: str) -> AuthResult:
        """
        Verify 2FA password.

        Args:
            user_id: Telegram user ID
            password: Cloud password

        Returns:
            AuthResult with status
        """
        logger.info("verify_2fa", user_id=user_id)

        client = await self._client_manager.get_client(user_id)

        try:
            result = await client.check_password(password)

            if result.get("success"):
                await self._finalize_auth(user_id, client)
                return AuthResult(success=True)

            return AuthResult(error="Unknown error")

        except AuthError:
            raise
        except Exception as e:
            logger.error("verify_2fa_error", user_id=user_id, error=str(e))
            raise AuthError(str(e), "Ошибка проверки пароля.")

    async def _finalize_auth(self, user_id: int, client: MTProtoClient) -> None:
        """
        Finalize authentication and save session.

        Args:
            user_id: Telegram user ID
            client: Authenticated client
        """
        # Export and save session
        session_string = await client.get_session_string()
        await self._session_manager.save_session(user_id, session_string)

        # Clear pending auth data
        self._pending_auth.pop(user_id, None)

        # Remove client from cache so next call creates fresh one with saved session
        await self._client_manager.remove_client(user_id)

        # Update user state
        async with self._db.session() as session:
            user_repo = UserRepository(session)
            await user_repo.update_state(user_id, BotState.MAIN_MENU.value)

        logger.info("auth_finalized", user_id=user_id)

    async def check_session(self, user_id: int) -> bool:
        """
        Check if user has valid session.

        Args:
            user_id: Telegram user ID

        Returns:
            True if session is valid
        """
        return await self._session_manager.has_valid_session(user_id)

    async def invalidate_session(self, user_id: int) -> None:
        """
        Invalidate user session.

        Args:
            user_id: Telegram user ID
        """
        await self._session_manager.invalidate_session(user_id)
        await self._client_manager.remove_client(user_id)

        async with self._db.session() as session:
            user_repo = UserRepository(session)
            await user_repo.update_state(user_id, BotState.SESSION_EXPIRED.value)
