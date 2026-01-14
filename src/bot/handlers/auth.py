import io

import structlog
from telegram import InputFile, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.bot.keyboards import (
    get_auth_method_keyboard,
    get_cancel_keyboard,
    get_main_menu_keyboard,
    get_qr_auth_keyboard,
)
from src.bot.messages import Messages
from src.bot.states import (
    AUTH_METHOD_CHOICE,
    AWAITING_2FA,
    AWAITING_CODE,
    AWAITING_PHONE,
    AWAITING_QR,
    MAIN_MENU,
)
from src.services import AuthService
from src.shared.constants import BotState
from src.shared.exceptions import AuthError
from src.shared.utils import validate_phone
from src.storage import get_database
from src.storage.repositories import UserRepository

logger = structlog.get_logger()


async def start_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start authentication flow - show method selection."""
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.message

    await message.reply_text(
        Messages.AUTH_METHOD_CHOICE,
        reply_markup=get_auth_method_keyboard(),
        parse_mode="Markdown",
    )

    return AUTH_METHOD_CHOICE


async def start_phone_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start phone number authentication."""
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


async def _delete_qr_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> None:
    """Delete QR code message."""
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass  # Message may already be deleted


async def _qr_polling_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job to check QR auth status."""
    job = context.job
    user_id = job.data["user_id"]
    chat_id = job.data["chat_id"]
    qr_message_id = job.data.get("qr_message_id")

    try:
        auth_service: AuthService = context.bot_data["auth_service"]
        result = await auth_service.check_qr_auth(user_id)

        if result.success:
            # Stop polling
            job.schedule_removal()

            # Delete QR message
            if qr_message_id:
                await _delete_qr_message(context, chat_id, qr_message_id)

            await context.bot.send_message(
                chat_id=chat_id,
                text=Messages.AUTH_SUCCESS,
                reply_markup=get_main_menu_keyboard(),
            )

            db = get_database()
            async with db.session() as session:
                user_repo = UserRepository(session)
                await user_repo.update_state(user_id, BotState.MAIN_MENU.value)

            logger.info("qr_auth_auto_success", user_id=user_id)

        elif result.needs_2fa:
            # Stop polling, user needs to enter 2FA
            job.schedule_removal()

            # Delete QR message
            if qr_message_id:
                await _delete_qr_message(context, chat_id, qr_message_id)

            # Set flag so text handler knows to expect 2FA password
            context.application.user_data[user_id]["awaiting_2fa_after_qr"] = True

            await context.bot.send_message(
                chat_id=chat_id,
                text=Messages.TWO_FA_REQUEST,
                reply_markup=get_cancel_keyboard(),
            )

            db = get_database()
            async with db.session() as session:
                user_repo = UserRepository(session)
                await user_repo.update_state(user_id, BotState.AWAITING_2FA.value)

    except Exception as e:
        logger.debug("qr_polling_check", user_id=user_id, status="pending", error=str(e))


def _stop_qr_polling(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Stop QR polling jobs for a user."""
    if context.job_queue is None:
        return
    job_name = f"qr_poll_{user_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()


async def start_qr_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start QR code authentication."""
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    chat = update.effective_chat

    logger.info("start_qr_auth", user_id=user.id)

    # Stop any existing polling
    _stop_qr_polling(context, user.id)

    try:
        auth_service: AuthService = context.bot_data["auth_service"]
        result = await auth_service.start_qr_auth(user.id)

        if result.success:
            await chat.send_message(
                Messages.AUTH_SUCCESS,
                reply_markup=get_main_menu_keyboard(),
            )
            return MAIN_MENU

        if result.qr_image:
            # Send QR code image
            qr_message = await chat.send_photo(
                photo=InputFile(io.BytesIO(result.qr_image), filename="qr_auth.png"),
                caption=Messages.QR_AUTH_PROMPT,
                reply_markup=get_qr_auth_keyboard(),
                parse_mode="Markdown",
            )

            # Save QR message id for later deletion
            context.user_data["qr_message_id"] = qr_message.message_id

            # Start background polling for QR status (if job_queue available)
            if context.job_queue is not None:
                context.job_queue.run_repeating(
                    _qr_polling_callback,
                    interval=3,  # Check every 3 seconds
                    first=3,  # First check after 3 seconds
                    data={
                        "user_id": user.id,
                        "chat_id": chat.id,
                        "qr_message_id": qr_message.message_id,
                    },
                    name=f"qr_poll_{user.id}",
                )

            return AWAITING_QR

    except AuthError as e:
        logger.error("qr_auth_error", user_id=user.id, error=str(e))
        await chat.send_message(
            f"Ошибка: {e.user_message}",
            reply_markup=get_auth_method_keyboard(),
        )
        return AUTH_METHOD_CHOICE

    return AUTH_METHOD_CHOICE


async def check_qr_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Check QR auth status after user claims to have scanned."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    chat = update.effective_chat

    logger.info("check_qr_auth", user_id=user.id)

    try:
        auth_service: AuthService = context.bot_data["auth_service"]
        result = await auth_service.check_qr_auth(user.id)

        if result.success:
            # Stop polling
            _stop_qr_polling(context, user.id)

            # Delete QR message (the message with the button that was clicked)
            try:
                await query.message.delete()
            except Exception:
                pass

            await chat.send_message(
                Messages.AUTH_SUCCESS,
                reply_markup=get_main_menu_keyboard(),
            )

            db = get_database()
            async with db.session() as session:
                user_repo = UserRepository(session)
                await user_repo.update_state(user.id, BotState.MAIN_MENU.value)

            return MAIN_MENU

        if result.needs_2fa:
            # Stop polling
            _stop_qr_polling(context, user.id)

            # Delete QR message
            try:
                await query.message.delete()
            except Exception:
                pass

            await chat.send_message(
                Messages.TWO_FA_REQUEST,
                reply_markup=get_cancel_keyboard(),
            )
            return AWAITING_2FA

        # Still pending
        await chat.send_message(
            Messages.QR_AUTH_PENDING,
            reply_markup=get_qr_auth_keyboard(),
        )
        return AWAITING_QR

    except AuthError as e:
        logger.error("check_qr_error", user_id=user.id, error=str(e))
        await chat.send_message(
            f"Ошибка: {e.user_message}",
            reply_markup=get_qr_auth_keyboard(),
        )
        return AWAITING_QR


async def refresh_qr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Refresh expired QR code."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    chat = update.effective_chat

    logger.info("refresh_qr", user_id=user.id)

    # Stop old polling, will restart with new QR
    _stop_qr_polling(context, user.id)

    # Delete old QR message
    try:
        await query.message.delete()
    except Exception:
        pass

    try:
        auth_service: AuthService = context.bot_data["auth_service"]
        result = await auth_service.refresh_qr(user.id)

        if result.qr_image:
            qr_message = await chat.send_photo(
                photo=InputFile(io.BytesIO(result.qr_image), filename="qr_auth.png"),
                caption=Messages.QR_AUTH_PROMPT,
                reply_markup=get_qr_auth_keyboard(),
                parse_mode="Markdown",
            )

            # Save new QR message id
            context.user_data["qr_message_id"] = qr_message.message_id

            # Restart polling (if job_queue available)
            if context.job_queue is not None:
                context.job_queue.run_repeating(
                    _qr_polling_callback,
                    interval=3,
                    first=3,
                    data={
                        "user_id": user.id,
                        "chat_id": chat.id,
                        "qr_message_id": qr_message.message_id,
                    },
                    name=f"qr_poll_{user.id}",
                )

            return AWAITING_QR

    except AuthError as e:
        logger.error("refresh_qr_error", user_id=user.id, error=str(e))
        await chat.send_message(
            f"Ошибка: {e.user_message}",
            reply_markup=get_auth_method_keyboard(),
        )
        return AUTH_METHOD_CHOICE

    return AWAITING_QR


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

    user = update.effective_user

    # Stop QR polling if running
    _stop_qr_polling(context, user.id)

    # Clear auth data
    context.user_data.pop("phone", None)
    context.user_data.pop("phone_code_hash", None)

    await message.reply_text(
        "Авторизация отменена.",
        reply_markup=get_main_menu_keyboard(),
    )

    return MAIN_MENU


async def handle_qr_state_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text input in QR state - for 2FA password after QR scan."""
    user = update.effective_user

    # Check if we're expecting 2FA password after QR auth
    if context.user_data.get("awaiting_2fa_after_qr"):
        # Clear the flag
        context.user_data.pop("awaiting_2fa_after_qr", None)
        # Delegate to 2FA handler
        return await handle_2fa(update, context)

    # Otherwise ignore text - user should use buttons
    await update.message.reply_text(
        "Используй кнопки для продолжения.",
        reply_markup=get_qr_auth_keyboard(),
    )
    return AWAITING_QR


def get_auth_handlers() -> list:
    """Get authentication-related handlers."""
    return [
        CallbackQueryHandler(start_auth, pattern="^action:reauth$"),
        CommandHandler("auth", start_auth),
        CallbackQueryHandler(cancel_auth, pattern="^action:cancel$"),
        CommandHandler("cancel", cancel_auth),
    ]


def get_auth_method_handlers() -> list:
    """Get handlers for auth method selection state."""
    return [
        CallbackQueryHandler(start_qr_auth, pattern="^action:auth_qr$"),
        CallbackQueryHandler(start_phone_auth, pattern="^action:auth_phone$"),
        CallbackQueryHandler(cancel_auth, pattern="^action:cancel$"),
    ]


def get_qr_auth_handlers() -> list:
    """Get handlers for QR auth state."""
    return [
        CallbackQueryHandler(check_qr_auth, pattern="^action:check_qr$"),
        CallbackQueryHandler(refresh_qr, pattern="^action:refresh_qr$"),
        CallbackQueryHandler(cancel_auth, pattern="^action:cancel$"),
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
