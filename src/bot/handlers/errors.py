import traceback

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from src.shared.exceptions import BotError

logger = structlog.get_logger()


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors in telegram handlers."""
    error = context.error

    # Get full traceback
    tb_str = ""
    if error is not None:
        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))

    # Log the error with traceback
    logger.error(
        "telegram_error",
        error=str(error),
        error_type=type(error).__name__,
        update=update.to_dict() if update else None,
        traceback=tb_str,
    )

    # Determine user-friendly message
    if isinstance(error, BotError):
        message = error.user_message
    else:
        message = "Произошла ошибка. Попробуй позже или начни сначала с /start"

    # Try to notify user
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"❌ {message}",
            )
        except Exception as e:
            logger.error("failed_to_send_error_message", error=str(e))
