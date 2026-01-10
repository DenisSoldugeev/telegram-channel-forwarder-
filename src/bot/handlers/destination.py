import structlog
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.bot.keyboards import get_cancel_keyboard, get_destination_keyboard, get_main_menu_keyboard
from src.bot.messages import Messages
from src.bot.states import DESTINATION_SETUP, MAIN_MENU
from src.services import DestinationService
from src.shared.exceptions import DestinationError
from src.shared.utils import validate_channel_link
from src.storage import get_database
from src.storage.repositories import DestinationRepository

logger = structlog.get_logger()


async def destination_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show destination setup menu."""
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.message

    user = update.effective_user

    # Get current destination
    db = get_database()
    async with db.session() as session:
        dest_repo = DestinationRepository(session)
        destination = await dest_repo.get_active_by_user(user.id)

    if destination:
        text = Messages.DESTINATION_CURRENT.format(
            title=destination.channel_title,
            username=f"@{destination.channel_username}" if destination.channel_username else "—",
        )
    else:
        text = Messages.DESTINATION_NOT_SET

    text += "\n\n" + Messages.DESTINATION_PROMPT

    # Show keyboard with reset button if destination is set
    keyboard = get_destination_keyboard(has_destination=destination is not None)

    if query:
        await query.edit_message_text(text, reply_markup=keyboard)
    else:
        await message.reply_text(text, reply_markup=keyboard)

    return DESTINATION_SETUP


async def handle_destination_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle destination channel input."""
    user = update.effective_user
    channel_id = None
    channel_username = None
    channel_title = None

    # Check if it's a forwarded message (PTB v20+ uses forward_origin)
    forward_origin = getattr(update.message, "forward_origin", None)
    if forward_origin and hasattr(forward_origin, "chat"):
        chat = forward_origin.chat
        channel_id = chat.id
        channel_username = getattr(chat, "username", None)
        channel_title = getattr(chat, "title", None)
    elif update.message.text:
        # Parse text input
        text = update.message.text.strip()
        result = validate_channel_link(text)

        if not result.is_valid:
            await update.message.reply_text(
                f"❌ {result.error}",
                reply_markup=get_cancel_keyboard(),
            )
            return DESTINATION_SETUP

        # Will be resolved by service
        channel_username = result.username
    else:
        await update.message.reply_text(
            "❌ Отправь ссылку на канал или перешли сообщение из него.",
            reply_markup=get_cancel_keyboard(),
        )
        return DESTINATION_SETUP

    logger.info(
        "handle_destination",
        user_id=user.id,
        channel_id=channel_id,
        channel_username=channel_username,
    )

    try:
        dest_service: DestinationService = context.bot_data["destination_service"]

        # Set destination (service will validate bot is admin)
        destination = await dest_service.set_destination(
            user_id=user.id,
            channel_id=channel_id,
            channel_username=channel_username,
            channel_title=channel_title,
        )

        await update.message.reply_text(
            Messages.DESTINATION_SUCCESS.format(
                title=destination.channel_title,
                username=f"@{destination.channel_username}" if destination.channel_username else "—",
            ),
            reply_markup=get_main_menu_keyboard(),
        )

        return MAIN_MENU

    except DestinationError as e:
        logger.error("destination_error", user_id=user.id, error=str(e))
        await update.message.reply_text(
            f"❌ {e.user_message}",
            reply_markup=get_cancel_keyboard(),
        )
        return DESTINATION_SETUP


async def reset_destination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Reset destination to use DM mode."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    logger.info("reset_destination", user_id=user.id)

    # Clear destination
    dest_service: DestinationService = context.bot_data["destination_service"]
    await dest_service.clear_destination(user.id)

    await query.edit_message_text(
        "✅ Получатель сброшен.\n\nТеперь посты будут приходить в ЛС бота.",
        reply_markup=get_main_menu_keyboard(),
    )

    return MAIN_MENU


async def cancel_destination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel destination setup."""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(
            "Настройка получателя отменена.",
            reply_markup=get_main_menu_keyboard(),
        )
    else:
        await update.message.reply_text(
            "Настройка получателя отменена.",
            reply_markup=get_main_menu_keyboard(),
        )

    return MAIN_MENU


def get_destination_handlers() -> list:
    """Get destination management handlers."""
    return [
        CallbackQueryHandler(destination_menu, pattern="^action:destination$"),
        CallbackQueryHandler(reset_destination, pattern="^action:reset_destination$"),
        CommandHandler("destination", destination_menu),
    ]
