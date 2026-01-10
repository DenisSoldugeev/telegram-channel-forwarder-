import structlog
from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from src.bot.keyboards import get_main_menu_keyboard
from src.bot.messages import Messages
from src.bot.states import MAIN_MENU
from src.services import ForwarderService
from src.shared.constants import BotState
from src.storage import get_database
from src.storage.repositories import (
    DeliveryRepository,
    DestinationRepository,
    SourceRepository,
    UserRepository,
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
    async with db.session() as session:
        user_repo = UserRepository(session)
        source_repo = SourceRepository(session)
        dest_repo = DestinationRepository(session)
        delivery_repo = DeliveryRepository(session)

        db_user = await user_repo.get_by_id(user.id)
        source_count = await source_repo.count_by_user(user.id)
        destination = await dest_repo.get_active_by_user(user.id)
        stats = await delivery_repo.get_stats(user.id, hours=24)
        last_delivery = await delivery_repo.get_last_delivery(user.id)

    # Determine session status
    session_status = "✅ Активна" if db_user and db_user.state != BotState.SESSION_EXPIRED.value else "❌ Требуется авторизация"

    # Destination name
    dest_name = destination.channel_title if destination else "ЛС бота (по умолчанию)"

    # Monitoring status
    is_running = db_user and db_user.state == BotState.RUNNING.value
    monitoring_status = "🟢 Активен" if is_running else "🔴 Остановлен"

    # Last forward time
    last_time = last_delivery.completed_at.strftime("%d.%m %H:%M") if last_delivery else "—"

    text = Messages.STATUS.format(
        session_status=session_status,
        source_count=source_count,
        destination_name=dest_name,
        monitoring_status=monitoring_status,
        forwarded_count=stats.get("success", 0),
        error_count=stats.get("failed", 0),
        last_forward_time=last_time,
    )

    if query:
        await query.edit_message_text(text, reply_markup=get_main_menu_keyboard(is_running))
    else:
        await message.reply_text(text, reply_markup=get_main_menu_keyboard(is_running))

    return MAIN_MENU


async def run_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start monitoring."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    logger.info("run_monitoring", user_id=user.id)

    db = get_database()
    async with db.session() as session:
        source_repo = SourceRepository(session)
        dest_repo = DestinationRepository(session)
        user_repo = UserRepository(session)

        # Check prerequisites
        source_count = await source_repo.count_by_user(user.id)
        destination = await dest_repo.get_active_by_user(user.id)

        if source_count == 0:
            await query.edit_message_text(
                "❌ Сначала добавь каналы-источники.",
                reply_markup=get_main_menu_keyboard(),
            )
            return MAIN_MENU

        # Update state
        await user_repo.update_state(user.id, BotState.RUNNING.value)

    # Determine target name for message
    target_name = destination.channel_title if destination else "ЛС бота"

    # Start forwarder for this user
    forwarder: ForwarderService = context.bot_data.get("forwarder_service")
    if forwarder:
        await forwarder.start_user_monitoring(user.id)

    await query.edit_message_text(
        f"▶️ Мониторинг запущен!\n\n"
        f"📺 Источников: {source_count}\n"
        f"📤 Получатель: {target_name}\n\n"
        f"Новые посты будут пересылаться автоматически.",
        reply_markup=get_main_menu_keyboard(is_running=True),
    )

    return MAIN_MENU


async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stop monitoring."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    logger.info("stop_monitoring", user_id=user.id)

    db = get_database()
    async with db.session() as session:
        user_repo = UserRepository(session)
        await user_repo.update_state(user.id, BotState.MAIN_MENU.value)

    # Stop forwarder for this user
    forwarder: ForwarderService = context.bot_data.get("forwarder_service")
    if forwarder:
        await forwarder.stop_user_monitoring(user.id)

    await query.edit_message_text(
        "⏹ Мониторинг остановлен.",
        reply_markup=get_main_menu_keyboard(is_running=False),
    )

    return MAIN_MENU


def get_monitoring_handlers() -> list:
    """Get monitoring handlers."""
    return [
        CallbackQueryHandler(status_command, pattern="^action:status$"),
        CommandHandler("status", status_command),
        CallbackQueryHandler(run_monitoring, pattern="^action:run$"),
        CommandHandler("run", run_monitoring),
        CallbackQueryHandler(stop_monitoring, pattern="^action:stop$"),
        CommandHandler("stop", stop_monitoring),
    ]
