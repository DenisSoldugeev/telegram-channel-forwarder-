import re
import time
from typing import Any, Callable, Coroutine

import structlog
from telegram import Update
from telegram.ext import BaseHandler

logger = structlog.get_logger()


class SensitiveDataFilter:
    """Filter sensitive data from log messages."""

    PATTERNS = [
        (r"\+\d{10,15}", "+XXX***XXX"),  # Phone numbers
        (r"code[\"\\s:=]+\\d{5}", "code=*****"),  # Verification codes
        (r"password[\"\\s:=]+[^\\s\"]+", "password=*****"),  # Passwords
    ]

    @classmethod
    def filter(cls, text: str) -> str:
        """Remove sensitive data from text."""
        for pattern, replacement in cls.PATTERNS:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text


class LoggingMiddleware:
    """Middleware for logging all updates."""

    async def __call__(
        self,
        update: Update,
        handler: Callable[[Update, Any], Coroutine],
        *args,
        **kwargs,
    ) -> Any:
        """Log update and measure handler execution time."""
        start_time = time.time()

        user_id = update.effective_user.id if update.effective_user else None
        chat_id = update.effective_chat.id if update.effective_chat else None

        # Log incoming update
        update_type = self._get_update_type(update)
        logger.info(
            "incoming_update",
            update_type=update_type,
            user_id=user_id,
            chat_id=chat_id,
        )

        try:
            result = await handler(update, *args, **kwargs)
            elapsed = time.time() - start_time

            logger.info(
                "update_processed",
                update_type=update_type,
                user_id=user_id,
                elapsed_ms=round(elapsed * 1000, 2),
            )

            return result

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(
                "update_error",
                update_type=update_type,
                user_id=user_id,
                elapsed_ms=round(elapsed * 1000, 2),
                error=str(e),
            )
            raise

    def _get_update_type(self, update: Update) -> str:
        """Determine update type for logging."""
        if update.message:
            if update.message.text:
                # Mask sensitive text
                text = update.message.text[:20]
                if text.startswith("+"):
                    return "message:phone"
                if text.isdigit() and len(text) == 5:
                    return "message:code"
                return "message:text"
            if update.message.document:
                return "message:document"
            return "message:other"
        if update.callback_query:
            return f"callback:{update.callback_query.data}"
        return "unknown"
