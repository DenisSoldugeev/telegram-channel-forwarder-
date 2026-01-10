import structlog

from src.app.config import settings
from src.shared.exceptions import SessionError
from src.shared.utils.crypto import SessionEncryption
from src.storage.database import Database
from src.storage.repositories import SessionRepository

logger = structlog.get_logger()


class SessionManager:
    """Manages encrypted session storage and retrieval."""

    def __init__(self, database: Database):
        """
        Initialize session manager.

        Args:
            database: Database instance
        """
        self._db = database
        self._encryption = SessionEncryption(
            settings.session_encryption_key.get_secret_value()
        )

    async def save_session(
        self,
        user_id: int,
        session_string: str,
    ) -> None:
        """
        Encrypt and save session.

        Args:
            user_id: Telegram user ID
            session_string: Pyrogram session string
        """
        logger.info("saving_session", user_id=user_id)

        session_bytes = session_string.encode()
        encrypted = self._encryption.encrypt(user_id, session_bytes)
        session_hash = self._encryption.compute_hash(session_bytes)

        async with self._db.session() as session:
            repo = SessionRepository(session)
            await repo.upsert(
                user_id=user_id,
                session_data=encrypted,
                session_hash=session_hash,
            )

        logger.info("session_saved", user_id=user_id)

    async def load_session(self, user_id: int) -> str | None:
        """
        Load and decrypt session.

        Args:
            user_id: Telegram user ID

        Returns:
            Session string or None if not found
        """
        async with self._db.session() as session:
            repo = SessionRepository(session)
            db_session = await repo.get_valid_session(user_id)

            if not db_session:
                return None

            try:
                decrypted = self._encryption.decrypt(
                    user_id,
                    db_session.session_data,
                )
                await repo.touch(user_id)
                return decrypted.decode()

            except Exception as e:
                logger.error(
                    "session_decrypt_error",
                    user_id=user_id,
                    error=str(e),
                )
                await repo.invalidate(user_id)
                return None

    async def invalidate_session(self, user_id: int) -> None:
        """
        Mark session as invalid.

        Args:
            user_id: Telegram user ID
        """
        logger.info("invalidating_session", user_id=user_id)

        async with self._db.session() as session:
            repo = SessionRepository(session)
            await repo.invalidate(user_id)

    async def has_valid_session(self, user_id: int) -> bool:
        """
        Check if user has valid session.

        Args:
            user_id: Telegram user ID

        Returns:
            True if valid session exists
        """
        async with self._db.session() as session:
            repo = SessionRepository(session)
            db_session = await repo.get_valid_session(user_id)
            return db_session is not None

    async def verify_session(self, user_id: int) -> bool:
        """
        Verify session is still valid with Telegram.

        Args:
            user_id: Telegram user ID

        Returns:
            True if session is valid
        """
        from src.mtproto.client import MTProtoClient

        session_string = await self.load_session(user_id)
        if not session_string:
            return False

        client = MTProtoClient(user_id, session_string)
        try:
            await client.initialize()
            is_valid = await client.is_authorized()

            if not is_valid:
                await self.invalidate_session(user_id)

            return is_valid

        except Exception as e:
            logger.error("session_verify_error", user_id=user_id, error=str(e))
            await self.invalidate_session(user_id)
            return False

        finally:
            await client.disconnect()
