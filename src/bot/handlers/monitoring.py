import structlog
from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from src.bot.keyboards import get_main_menu_keyboard
from src.bot.messages import Messages
from src.bot.states import MAIN_MENU
from src.services import ForwarderService
from src.storage import get_database
from src.storage.repositories import (
    DestinationRepository,
    SessionRepository,
    SourceRepository,
)

logger = structlog.get_logger()


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show current status."""
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.message

    user = update.effective_user

    db = get_database()
    async with db.session() as db_session:
        source_repo = SourceRepository(db_session)
        dest_repo = DestinationRepository(db_session)
        session_repo = SessionRepository(db_session)

        source_count = await source_repo.count_by_user(user.id)
        destination = await dest_repo.get_active_by_user(user.id)
        user_session = await session_repo.get_by_user(user.id)

    # Determine session status
    has_session = user_session and user_session.is_valid
    session_status = "✅ Активна" if has_session else "❌ Требуется авторизация"

    # Destination name
    dest_name = destination.channel_title if destination else "ЛС бота (по умолчанию)"

    # Monitoring status - active if there are sources and session exists
    forwarder: ForwarderService = context.bot_data.get("forwarder_service")
    is_monitoring = forwarder and user.id in forwarder._active_users

    if source_count > 0 and has_session:
        monitoring_status = "🟢 Активен" if is_monitoring else "🟡 Перезапустите бота"
    else:
        monitoring_status = "⚪ Нет источников" if source_count == 0 else "❌ Требуется авторизация"

    text = Messages.STATUS.format(
        session_status=session_status,
        source_count=source_count,
        destination_name=dest_name,
        monitoring_status=monitoring_status,
    )

    if query:
        await query.edit_message_text(text, reply_markup=get_main_menu_keyboard())
    else:
        await message.reply_text(text, reply_markup=get_main_menu_keyboard())

    return MAIN_MENU


def get_monitoring_handlers() -> list:
    """Get monitoring handlers."""
    return [
        CallbackQueryHandler(status_command, pattern="^action:status$"),
        CommandHandler("status", status_command),
    ]
