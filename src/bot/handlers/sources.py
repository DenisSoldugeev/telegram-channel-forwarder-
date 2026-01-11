import structlog
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.bot.keyboards import (
    get_add_source_keyboard,
    get_confirm_keyboard,
    get_done_cancel_keyboard,
    get_sources_keyboard,
    get_sources_menu_keyboard,
)
from src.bot.messages import Messages
from src.bot.states import ADD_SOURCE_FILE, ADD_SOURCE_TEXT, MAIN_MENU, REMOVE_SOURCE, SOURCES_MENU
from src.services import ForwarderService, SourceService
from src.shared.constants import MAX_FILE_SIZE_BYTES, MAX_SOURCES_PER_USER, SUPPORTED_FILE_EXTENSIONS
from src.shared.exceptions import SourceError
from src.shared.utils.validators import parse_channel_links
from src.storage import get_database
from src.storage.repositories import SourceRepository

logger = structlog.get_logger()


async def _restart_user_monitoring(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart monitoring for user after source changes."""
    forwarder: ForwarderService = context.bot_data.get("forwarder_service")
    if forwarder:
        try:
            # Stop existing monitoring if running
            await forwarder.stop_user_monitoring(user_id)
        except Exception:
            pass
        try:
            # Start fresh monitoring
            await forwarder.start_user_monitoring(user_id)
            logger.info("monitoring_restarted", user_id=user_id)
        except Exception as e:
            logger.error("monitoring_restart_failed", user_id=user_id, error=str(e))


async def sources_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show sources management menu."""
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.message

    user = update.effective_user

    db = get_database()
    async with db.session() as session:
        source_repo = SourceRepository(session)
        count = await source_repo.count_by_user(user.id)

    text = Messages.SOURCES_MENU.format(count=count)
    keyboard = get_sources_menu_keyboard(count)

    if query:
        await query.edit_message_text(text, reply_markup=keyboard)
    else:
        await message.reply_text(text, reply_markup=keyboard)

    return SOURCES_MENU


async def add_source_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start adding sources - go directly to text input mode."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user

    # Check source limit
    db = get_database()
    async with db.session() as session:
        source_repo = SourceRepository(session)
        count = await source_repo.count_by_user(user.id)

    if count >= MAX_SOURCES_PER_USER:
        await query.edit_message_text(
            Messages.ERR_SOURCE_LIMIT.format(limit=MAX_SOURCES_PER_USER),
            reply_markup=get_sources_menu_keyboard(count),
        )
        return SOURCES_MENU

    # Initialize pending sources list
    context.user_data["pending_sources"] = []

    await query.edit_message_text(
        Messages.ADD_SOURCE_TEXT_PROMPT,
        reply_markup=get_add_source_keyboard(),
        parse_mode="HTML",
    )

    return ADD_SOURCE_TEXT


async def handle_source_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text input with channel links."""
    user = update.effective_user
    text = update.message.text

    logger.info("handle_source_text", user_id=user.id)

    # Parse links from text
    parsed = parse_channel_links(text)
    valid_links = []
    errors = []

    for original, result in parsed:
        if result.is_valid:
            valid_links.append(result.username)
        else:
            errors.append(f"• {original}: {result.error}")

    # Add to pending list
    pending = context.user_data.get("pending_sources", [])
    pending.extend(valid_links)
    context.user_data["pending_sources"] = list(set(pending))  # Dedupe

    # Build response
    response_parts = []
    if valid_links:
        response_parts.append(f"✅ Добавлено в очередь: {len(valid_links)}")
    if errors:
        response_parts.append("❌ Ошибки:\n" + "\n".join(errors[:5]))  # Show first 5 errors
        if len(errors) > 5:
            response_parts.append(f"...и ещё {len(errors) - 5} ошибок")

    response_parts.append(f"\n📝 Всего в очереди: {len(pending)}")
    response_parts.append("\nОтправь ещё ссылки или нажми «Готово».")

    await update.message.reply_text(
        "\n".join(response_parts),
        reply_markup=get_done_cancel_keyboard(),
    )

    return ADD_SOURCE_TEXT


async def finish_add_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process pending sources and add them."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    pending = context.user_data.pop("pending_sources", [])

    if not pending:
        await query.edit_message_text(
            "Нет источников для добавления.",
            reply_markup=get_sources_menu_keyboard(0),
        )
        return SOURCES_MENU

    try:
        source_service: SourceService = context.bot_data["source_service"]
        result = await source_service.add_sources(user.id, pending)

        # Build result message
        parts = []
        if result.success:
            parts.append(f"✅ Добавлено ({len(result.success)}):")
            for src in result.success[:10]:
                parts.append(f"  • {src.channel_title}")

        if result.errors:
            parts.append(f"\n❌ Ошибки ({len(result.errors)}):")
            for err in result.errors[:5]:
                parts.append(f"  • {err.link}: {err.reason}")

        db = get_database()
        async with db.session() as session:
            source_repo = SourceRepository(session)
            count = await source_repo.count_by_user(user.id)

        # Restart monitoring with new sources
        if result.success:
            await _restart_user_monitoring(user.id, context)

        await query.edit_message_text(
            "\n".join(parts),
            reply_markup=get_sources_menu_keyboard(count),
        )

    except SourceError as e:
        await query.edit_message_text(
            f"❌ Ошибка: {e.user_message}",
            reply_markup=get_sources_menu_keyboard(0),
        )

    return SOURCES_MENU


async def add_source_file_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start file upload for adding sources."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        Messages.ADD_SOURCE_FILE_PROMPT,
        reply_markup=get_done_cancel_keyboard(),
    )

    return ADD_SOURCE_FILE


async def handle_source_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle file upload with channel links."""
    user = update.effective_user
    document = update.message.document

    logger.info("handle_source_file", user_id=user.id, filename=document.file_name)

    # Validate file
    if document.file_size > MAX_FILE_SIZE_BYTES:
        await update.message.reply_text(
            Messages.ERR_FILE_TOO_LARGE,
            reply_markup=get_done_cancel_keyboard(),
        )
        return ADD_SOURCE_FILE

    # Check extension
    ext = "." + document.file_name.split(".")[-1].lower() if "." in document.file_name else ""
    if ext not in SUPPORTED_FILE_EXTENSIONS:
        await update.message.reply_text(
            Messages.ERR_UNSUPPORTED_FILE,
            reply_markup=get_done_cancel_keyboard(),
        )
        return ADD_SOURCE_FILE

    try:
        # Download file
        file = await document.get_file()
        content = await file.download_as_bytearray()

        # Process with source service
        source_service: SourceService = context.bot_data["source_service"]
        result = await source_service.add_sources_from_file(
            user.id,
            bytes(content),
            document.file_name,
        )

        # Build result message
        parts = [f"📄 Файл обработан: {document.file_name}"]
        if result.success:
            parts.append(f"\n✅ Добавлено: {len(result.success)}")
        if result.errors:
            parts.append(f"❌ Ошибок: {len(result.errors)}")

        db = get_database()
        async with db.session() as session:
            source_repo = SourceRepository(session)
            count = await source_repo.count_by_user(user.id)

        # Restart monitoring with new sources
        if result.success:
            await _restart_user_monitoring(user.id, context)

        await update.message.reply_text(
            "\n".join(parts),
            reply_markup=get_sources_menu_keyboard(count),
        )

        return SOURCES_MENU

    except Exception as e:
        logger.error("file_processing_error", user_id=user.id, error=str(e))
        await update.message.reply_text(
            f"❌ Ошибка обработки файла: {str(e)}",
            reply_markup=get_done_cancel_keyboard(),
        )
        return ADD_SOURCE_FILE


async def list_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show list of sources."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user

    db = get_database()
    async with db.session() as session:
        source_repo = SourceRepository(session)
        sources = await source_repo.get_by_user(user.id, limit=20)

    if not sources:
        await query.edit_message_text(
            "📭 Список источников пуст.\nДобавь каналы для мониторинга.",
            reply_markup=get_sources_menu_keyboard(0),
        )
        return SOURCES_MENU

    await query.edit_message_text(
        "📺 Твои источники:",
        reply_markup=get_sources_keyboard(sources),
    )

    return SOURCES_MENU


async def remove_source_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start source removal flow."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user

    db = get_database()
    async with db.session() as session:
        source_repo = SourceRepository(session)
        sources = await source_repo.get_by_user(user.id, limit=20)

    if not sources:
        await query.edit_message_text(
            "📭 Нет источников для удаления.",
            reply_markup=get_sources_menu_keyboard(0),
        )
        return SOURCES_MENU

    await query.edit_message_text(
        "Выбери источник для удаления:",
        reply_markup=get_sources_keyboard(sources, for_removal=True),
    )

    return REMOVE_SOURCE


async def confirm_remove_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirm source removal."""
    query = update.callback_query
    await query.answer()

    # Extract source_id from callback data: source:remove:{id}
    source_id = int(query.data.split(":")[-1])

    db = get_database()
    async with db.session() as session:
        source_repo = SourceRepository(session)
        source = await source_repo.get_by_id(source_id)

    if not source:
        await query.edit_message_text("Источник не найден.")
        return SOURCES_MENU

    await query.edit_message_text(
        f"Удалить источник?\n\n📺 {source.channel_title}",
        reply_markup=get_confirm_keyboard("remove_source", source_id),
    )

    return REMOVE_SOURCE


async def execute_remove_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Execute source removal."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    source_id = int(query.data.split(":")[-1])

    logger.info("remove_source", user_id=user.id, source_id=source_id)

    db = get_database()
    async with db.session() as session:
        source_repo = SourceRepository(session)

        source = await source_repo.get_by_id(source_id)
        if source and source.user_id == user.id:
            await source_repo.delete(source)

        count = await source_repo.count_by_user(user.id)

    # Restart monitoring with updated sources
    await _restart_user_monitoring(user.id, context)

    await query.edit_message_text(
        "✅ Источник удалён.",
        reply_markup=get_sources_menu_keyboard(count),
    )

    return SOURCES_MENU


async def cancel_sources_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel current sources action."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user

    # Clear pending data
    context.user_data.pop("pending_sources", None)

    db = get_database()
    async with db.session() as session:
        source_repo = SourceRepository(session)
        count = await source_repo.count_by_user(user.id)

    await query.edit_message_text(
        Messages.SOURCES_MENU.format(count=count),
        reply_markup=get_sources_menu_keyboard(count),
    )

    return SOURCES_MENU


def get_sources_handlers() -> list:
    """Get source management handlers."""
    return [
        # Menu navigation
        CallbackQueryHandler(sources_menu, pattern="^action:sources$"),
        CommandHandler("channels", sources_menu),

        # Add sources
        CallbackQueryHandler(add_source_menu, pattern="^action:add_source$"),
        CallbackQueryHandler(add_source_file_start, pattern="^action:add_source_file$"),
        CallbackQueryHandler(finish_add_sources, pattern="^action:done$"),

        # List and remove
        CallbackQueryHandler(list_sources, pattern="^action:list_sources$"),
        CallbackQueryHandler(remove_source_start, pattern="^action:remove_source$"),
        CallbackQueryHandler(confirm_remove_source, pattern=r"^source:remove:\d+$"),
        CallbackQueryHandler(execute_remove_source, pattern=r"^confirm:remove_source:\d+$"),

        # Cancel
        CallbackQueryHandler(cancel_sources_action, pattern="^action:cancel$"),
    ]
