import structlog
from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from src.bot.keyboards import get_main_menu_keyboard, get_start_keyboard
from src.bot.messages import Messages
from src.bot.states import MAIN_MENU
from src.storage import get_database
from src.storage.repositories import SessionRepository, UserRepository

logger = structlog.get_logger()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /start command."""
    user = update.effective_user
    if not user:
        return MAIN_MENU

    logger.info("start_command", user_id=user.id, username=user.username)

    db = get_database()
    async with db.session() as session:
        user_repo = UserRepository(session)
        session_repo = SessionRepository(session)

        db_user, created = await user_repo.get_or_create(user.id)

        # Check if user has valid session
        user_session = await session_repo.get_valid_session(user.id)

        if user_session:
            # User is already authorized
            await update.message.reply_text(
                Messages.WELCOME_BACK,
                reply_markup=get_main_menu_keyboard(),
            )
            return MAIN_MENU

        # New user or expired session
        await update.message.reply_text(
            Messages.START,
            reply_markup=get_start_keyboard(),
        )

    return MAIN_MENU


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.message.reply_text(Messages.HELP)


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle help button callback."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(Messages.HOW_IT_WORKS)


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle return to main menu."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        Messages.MAIN_MENU,
        reply_markup=get_main_menu_keyboard(),
    )

    return MAIN_MENU


def get_start_handlers() -> list:
    """Get handlers for start and help commands."""
    return [
        CommandHandler("start", start_command),
        CommandHandler("help", help_command),
        CallbackQueryHandler(help_callback, pattern="^action:help$"),
        CallbackQueryHandler(main_menu_callback, pattern="^action:main_menu$"),
    ]
