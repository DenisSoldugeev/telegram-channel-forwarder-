from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Telegram Bot API
    bot_token: SecretStr

    # Telegram MTProto (Pyrogram)
    api_id: int
    api_hash: SecretStr

    # Database
    database_url: SecretStr = SecretStr("sqlite+aiosqlite:///./data/bot.db")

    # Session encryption
    session_encryption_key: SecretStr

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "console"

    # Optional Redis
    redis_url: SecretStr | None = None

    # Rate limiting
    max_messages_per_second: int = 30
    flood_wait_multiplier: float = 1.5

    # Retry settings
    max_retries: int = 5
    base_retry_delay: float = 1.0
    max_retry_delay: float = 300.0

    # Media group settings
    media_group_timeout: float = 2.0

    # DM forwarding settings
    dm_max_media_size_mb: int = 20  # Max file size in MB for DM forwarding

    # Keyword filtering settings
    filter_keywords_raw: str = ""  # Comma-separated trigger words from env
    filter_mode: Literal["whitelist", "blacklist"] = "blacklist"
    filter_case_sensitive: bool = False

    @computed_field
    @property
    def filter_keywords(self) -> list[str]:
        """Parse comma-separated keywords into list."""
        if not self.filter_keywords_raw.strip():
            return []
        return [kw.strip() for kw in self.filter_keywords_raw.split(",") if kw.strip()]

    # Auth settings
    max_auth_attempts: int = 3
    auth_code_timeout: int = 300  # 5 minutes


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
