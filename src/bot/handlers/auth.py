import structlog
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.bot.keyboards import get_cancel_keyboard, get_main_menu_keyboard
from src.bot.messages import Messages
from src.bot.states import AWAITING_2FA, AWAITING_CODE, AWAITING_PHONE, MAIN_MENU
from src.services import AuthService
from src.shared.constants import BotState
from src.shared.exceptions import AuthError
from src.shared.utils import validate_phone
from src.storage import get_database
from src.storage.repositories import UserRepository

logger = structlog.get_logger()


async def start_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start authentication flow."""
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.message

    await message.reply_text(
        Messages.PHONE_REQUEST,
        reply_markup=get_cancel_keyboard(),
    )

    return AWAITING_PHONE


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle phone number input."""
    user = update.effective_user
    phone = update.message.text.strip()

    logger.info("handle_phone", user_id=user.id)

    if not validate_phone(phone):
        await update.message.reply_text(
            Messages.ERR_INVALID_PHONE,
            reply_markup=get_cancel_keyboard(),
        )
        return AWAITING_PHONE

    # Store phone in context for later use
    context.user_data["phone"] = phone

    try:
        # Initialize auth service and send code
        auth_service: AuthService = context.bot_data["auth_service"]
        result = await auth_service.start_auth(user.id, phone)

        if result.needs_code:
            context.user_data["phone_code_hash"] = result.phone_code_hash
            await update.message.reply_text(
                Messages.CODE_REQUEST,
                reply_markup=get_cancel_keyboard(),
            )
            return AWAITING_CODE

    except AuthError as e:
        logger.error("auth_error", user_id=user.id, error=str(e))
        await update.message.reply_text(
            f"❌ Ошибка авторизации: {e.user_message}",
            reply_markup=get_cancel_keyboard(),
        )
        return AWAITING_PHONE

    return AWAITING_CODE


async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle verification code input."""
    user = update.effective_user
    raw_code = update.message.text.strip()

    # Immediately delete message with code to avoid Telegram's anti-phishing detection
    try:
        await update.message.delete()
    except Exception:
        pass

    logger.info("handle_code", user_id=user.id)

    chat = update.effective_chat

    # Remove spaces, dashes and other separators to extract digits
    # This allows users to enter code as "1 2 3 4 5" or "1-2-3-4-5" to bypass Telegram's anti-phishing
    code = "".join(c for c in raw_code if c.isdigit())

    # Validate code format (digits only, 4-6 length)
    if not code or not (4 <= len(code) <= 6):
        await chat.send_message(
            Messages.ERR_INVALID_CODE.format(remaining=3),
            reply_markup=get_cancel_keyboard(),
        )
        return AWAITING_CODE

    try:
        auth_service: AuthService = context.bot_data["auth_service"]
        phone = context.user_data.get("phone")
        phone_code_hash = context.user_data.get("phone_code_hash")

        result = await auth_service.verify_code(
            user_id=user.id,
            phone=phone,
            code=code,
            phone_code_hash=phone_code_hash,
        )

        if result.needs_2fa:
            await chat.send_message(
                Messages.TWO_FA_REQUEST,
                reply_markup=get_cancel_keyboard(),
            )
            return AWAITING_2FA

        if result.success:
            # Clear sensitive data
            context.user_data.pop("phone", None)
            context.user_data.pop("phone_code_hash", None)

            await chat.send_message(
                Messages.AUTH_SUCCESS,
                reply_markup=get_main_menu_keyboard(),
            )

            # Update user state
            db = get_database()
            async with db.session() as session:
                user_repo = UserRepository(session)
                await user_repo.update_state(user.id, BotState.MAIN_MENU.value)

            return MAIN_MENU

    except AuthError as e:
        logger.error("code_verification_error", user_id=user.id, error=str(e))
        await chat.send_message(
            f"❌ {e.user_message}",
            reply_markup=get_cancel_keyboard(),
        )
        return AWAITING_CODE

    return AWAITING_CODE


async def handle_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 2FA password input."""
    user = update.effective_user
    password = update.message.text

    logger.info("handle_2fa", user_id=user.id)

    try:
        auth_service: AuthService = context.bot_data["auth_service"]

        result = await auth_service.verify_2fa(
            user_id=user.id,
            password=password,
        )

        # Immediately delete message with password
        try:
            await update.message.delete()
        except Exception:
            pass

        if result.success:
            # Clear sensitive data
            context.user_data.pop("phone", None)
            context.user_data.pop("phone_code_hash", None)

            await update.effective_chat.send_message(
                Messages.AUTH_SUCCESS,
                reply_markup=get_main_menu_keyboard(),
            )

            # Update user state
            db = get_database()
            async with db.session() as session:
                user_repo = UserRepository(session)
                await user_repo.update_state(user.id, BotState.MAIN_MENU.value)

            return MAIN_MENU

    except AuthError as e:
        logger.error("2fa_error", user_id=user.id, error=str(e))
        await update.effective_chat.send_message(
            f"❌ {e.user_message}",
            reply_markup=get_cancel_keyboard(),
        )
        return AWAITING_2FA

    return AWAITING_2FA


async def cancel_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel authentication flow."""
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.message

    # Clear auth data
    context.user_data.pop("phone", None)
    context.user_data.pop("phone_code_hash", None)

    await message.reply_text(
        "Авторизация отменена.",
        reply_markup=get_main_menu_keyboard(),
    )

    return MAIN_MENU


def get_auth_handlers() -> list:
    """Get authentication-related handlers."""
    return [
        CallbackQueryHandler(start_auth, pattern="^action:reauth$"),
        CommandHandler("auth", start_auth),
        CallbackQueryHandler(cancel_auth, pattern="^action:cancel$"),
        CommandHandler("cancel", cancel_auth),
    ]


def get_auth_conversation_handler() -> ConversationHandler:
    """Get conversation handler for auth flow."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_auth, pattern="^action:reauth$"),
            CommandHandler("auth", start_auth),
        ],
        states={
            AWAITING_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone),
            ],
            AWAITING_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code),
            ],
            AWAITING_2FA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_2fa),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_auth, pattern="^action:cancel$"),
            CommandHandler("cancel", cancel_auth),
        ],
        name="auth_conversation",
        persistent=False,
    )
