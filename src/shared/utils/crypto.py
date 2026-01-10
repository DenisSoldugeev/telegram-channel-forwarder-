import base64
import hashlib

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class SessionEncryption:
    """Encrypt/decrypt Pyrogram sessions for secure storage."""

    SALT_PREFIX = "tg_forward_bot_"
    ITERATIONS = 100_000

    def __init__(self, master_key: str):
        """
        Initialize encryption with master key.

        Args:
            master_key: Master encryption key from environment
        """
        self._master_key = master_key.encode()

    def _derive_key(self, user_id: int) -> bytes:
        """
        Derive unique encryption key for each user.

        Args:
            user_id: Telegram user ID

        Returns:
            Derived Fernet-compatible key
        """
        salt = f"{self.SALT_PREFIX}{user_id}".encode()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self.ITERATIONS,
        )

        return base64.urlsafe_b64encode(kdf.derive(self._master_key))

    def encrypt(self, user_id: int, data: bytes) -> bytes:
        """
        Encrypt data with user-specific key.

        Args:
            user_id: Telegram user ID
            data: Data to encrypt

        Returns:
            Encrypted data
        """
        key = self._derive_key(user_id)
        fernet = Fernet(key)
        return fernet.encrypt(data)

    def decrypt(self, user_id: int, encrypted_data: bytes) -> bytes:
        """
        Decrypt data with user-specific key.

        Args:
            user_id: Telegram user ID
            encrypted_data: Data to decrypt

        Returns:
            Decrypted data
        """
        key = self._derive_key(user_id)
        fernet = Fernet(key)
        return fernet.decrypt(encrypted_data)

    @staticmethod
    def compute_hash(data: bytes) -> str:
        """
        Compute SHA-256 hash of data.

        Args:
            data: Data to hash

        Returns:
            Hex-encoded hash string
        """
        return hashlib.sha256(data).hexdigest()
