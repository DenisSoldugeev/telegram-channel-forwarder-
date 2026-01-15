from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.shared.constants import CallbackAction
from src.storage.models import Source


def get_start_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard for /start command."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Начать авторизацию", callback_data=f"action:{CallbackAction.REAUTH.value}")],
        [InlineKeyboardButton("❓ Как это работает?", callback_data="action:help")],
    ])


def get_auth_method_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard for choosing auth method."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 QR-код (рекомендуется)", callback_data="action:auth_qr")],
        [InlineKeyboardButton("📞 По номеру телефона", callback_data="action:auth_phone")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"action:{CallbackAction.CANCEL.value}")],
    ])


def get_qr_auth_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard for QR auth state."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить QR", callback_data="action:refresh_qr")],
        [InlineKeyboardButton("✅ Я отсканировал", callback_data="action:check_qr")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"action:{CallbackAction.CANCEL.value}")],
    ])


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Get main menu keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📺 Каналы", callback_data=f"action:{CallbackAction.SOURCES.value}")],
        [InlineKeyboardButton("📤 Получатель", callback_data=f"action:{CallbackAction.DESTINATION.value}")],
    ])


def get_sources_menu_keyboard(source_count: int = 0) -> InlineKeyboardMarkup:
    """Get sources management menu keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Добавить", callback_data=f"action:{CallbackAction.ADD_SOURCE.value}"),
            InlineKeyboardButton("➖ Удалить", callback_data=f"action:{CallbackAction.REMOVE_SOURCE.value}"),
        ],
        [InlineKeyboardButton(f"📋 Список ({source_count})", callback_data=f"action:{CallbackAction.LIST_SOURCES.value}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"action:{CallbackAction.MAIN_MENU.value}")],
    ])


def get_add_source_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard for adding sources (text input mode)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Загрузить файл", callback_data=f"action:{CallbackAction.ADD_SOURCE_FILE.value}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"action:{CallbackAction.CANCEL.value}")],
    ])


def get_sources_keyboard(
    sources: list[Source],
    page: int = 1,
    total_pages: int = 1,
    for_removal: bool = False,
) -> InlineKeyboardMarkup:
    """
    Get keyboard with list of sources.

    Args:
        sources: List of sources to display
        page: Current page number (1-indexed)
        total_pages: Total number of pages
        for_removal: If True, sources are clickable for removal

    Returns:
        Inline keyboard with sources
    """
    buttons = []

    for source in sources:
        title = source.channel_title[:30] + "..." if len(source.channel_title) > 30 else source.channel_title
        username = f"@{source.channel_username}" if source.channel_username else ""
        label = f"{title} {username}".strip()

        if for_removal:
            callback_data = f"source:remove:{source.id}"
        else:
            callback_data = f"source:view:{source.id}"

        buttons.append([InlineKeyboardButton(label, callback_data=callback_data)])

    # Add pagination if needed
    if total_pages > 1:
        pagination_prefix = "sources_remove_page" if for_removal else "sources_page"
        pagination = get_pagination_keyboard(page, total_pages, pagination_prefix)
        buttons.append(pagination)

    # Add back button
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data=f"action:{CallbackAction.SOURCES.value}")])

    return InlineKeyboardMarkup(buttons)


def get_pagination_keyboard(
    current_page: int,
    total_pages: int,
    prefix: str,
) -> list[InlineKeyboardButton]:
    """
    Get pagination buttons.

    Args:
        current_page: Current page number (1-indexed)
        total_pages: Total number of pages
        prefix: Callback data prefix

    Returns:
        List of pagination buttons
    """
    buttons = []

    if current_page > 1:
        buttons.append(
            InlineKeyboardButton("⬅️", callback_data=f"{prefix}:{current_page - 1}")
        )

    buttons.append(
        InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="noop")
    )

    if current_page < total_pages:
        buttons.append(
            InlineKeyboardButton("➡️", callback_data=f"{prefix}:{current_page + 1}")
        )

    return buttons


def get_confirm_keyboard(
    action: str,
    entity_id: int,
) -> InlineKeyboardMarkup:
    """
    Get confirmation keyboard.

    Args:
        action: Action to confirm
        entity_id: Entity ID

    Returns:
        Confirmation keyboard
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да", callback_data=f"confirm:{action}:{entity_id}"),
            InlineKeyboardButton("❌ Нет", callback_data=f"action:{CallbackAction.CANCEL.value}"),
        ],
    ])


def get_reauth_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard for re-authentication prompt."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Авторизоваться", callback_data=f"action:{CallbackAction.REAUTH.value}")],
    ])


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard with cancel button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Отмена", callback_data=f"action:{CallbackAction.CANCEL.value}")],
    ])


def get_destination_keyboard(has_destination: bool = False) -> InlineKeyboardMarkup:
    """Get keyboard for destination menu."""
    buttons = []

    if has_destination:
        buttons.append([
            InlineKeyboardButton(
                "🔄 Сбросить (использовать ЛС)",
                callback_data=f"action:{CallbackAction.RESET_DESTINATION.value}"
            )
        ])

    buttons.append([
        InlineKeyboardButton("◀️ Назад", callback_data=f"action:{CallbackAction.MAIN_MENU.value}")
    ])

    return InlineKeyboardMarkup(buttons)


def get_done_cancel_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard with done and cancel buttons."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Готово", callback_data="action:done"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"action:{CallbackAction.CANCEL.value}"),
        ],
    ])
