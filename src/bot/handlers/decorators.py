from collections.abc import Callable
from functools import wraps

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from src.bot.keyboards import get_start_keyboard
from src.bot.states import MAIN_MENU
from src.storage import get_database
from src.storage.repositories import SessionRepository

logger = structlog.get_logger()


def require_auth(func: Callable) -> Callable:
    """
    Decorator that checks if user is authorized before executing handler.

    If user has no valid session, sends authorization prompt and returns MAIN_MENU.
    """

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user:
            return MAIN_MENU

        db = get_database()
        async with db.session() as session:
            session_repo = SessionRepository(session)
            user_session = await session_repo.get_valid_session(user.id)

        if not user_session:
            logger.info("auth_required", user_id=user.id, command=func.__name__)

            message = update.message or (update.callback_query and update.callback_query.message)
            if message:
                await message.reply_text(
                    "🔐 Для использования этой функции необходимо авторизоваться.\n\n"
                    "Нажми «Авторизация» для входа в Telegram аккаунт.",
                    reply_markup=get_start_keyboard(),
                )
            return MAIN_MENU

        return await func(update, context, *args, **kwargs)

    return wrapper
