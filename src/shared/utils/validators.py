import re
from dataclasses import dataclass
from enum import Enum

from src.shared.constants import (
    CHANNEL_ID_PATTERN,
    CHANNEL_INVITE_PATTERN,
    CHANNEL_LINK_PATTERN,
    CHANNEL_USERNAME_PATTERN,
    PHONE_PATTERN,
)


class ChannelIdentifierType(str, Enum):
    """Type of channel identifier."""

    USERNAME = "username"
    CHANNEL_ID = "channel_id"
    INVITE_LINK = "invite_link"


@dataclass
class ChannelValidationResult:
    """Result of channel link validation."""

    is_valid: bool
    identifier_type: ChannelIdentifierType | None = None
    username: str | None = None
    channel_id: int | None = None
    invite_link: str | None = None
    error: str | None = None


def validate_phone(phone: str) -> bool:
    """
    Validate phone number in international format.

    Args:
        phone: Phone number string

    Returns:
        True if valid international format
    """
    # Remove spaces, dashes, parentheses
    cleaned = re.sub(r"[\s\-\(\)]", "", phone)
    return bool(re.match(PHONE_PATTERN, cleaned))


def normalize_phone(phone: str) -> str:
    """
    Normalize phone number to standard format.

    Args:
        phone: Phone number string

    Returns:
        Cleaned phone number with + prefix
    """
    cleaned = re.sub(r"[\s\-\(\)]", "", phone)
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


def validate_channel_link(link: str | None) -> ChannelValidationResult:
    """
    Validate Telegram channel link, username, ID, or invite link.

    Supports:
        - Public channels: @username, t.me/username
        - Private channels by ID: -1001234567890, 1234567890
        - Private channels by invite: t.me/+abc123, t.me/joinchat/abc123

    Args:
        link: Channel link, @username, numeric ID, or invite link

    Returns:
        ChannelValidationResult with validation status and identifier
    """
    if not link:
        return ChannelValidationResult(
            is_valid=False,
            error="Пустая ссылка",
        )

    link = link.strip()

    # Check for invite links first (t.me/+ or t.me/joinchat/)
    invite_match = re.match(CHANNEL_INVITE_PATTERN, link)
    if invite_match:
        # Reconstruct full invite link for Pyrogram
        invite_hash = invite_match.group("invite_hash")
        full_invite = f"https://t.me/+{invite_hash}"
        return ChannelValidationResult(
            is_valid=True,
            identifier_type=ChannelIdentifierType.INVITE_LINK,
            invite_link=full_invite,
        )

    # Check for numeric channel ID
    id_match = re.match(CHANNEL_ID_PATTERN, link)
    if id_match:
        raw_id = link.lstrip("-")
        # Normalize to full format with -100 prefix
        if raw_id.startswith("100") and len(raw_id) >= 13:
            # Already in full format (e.g., 1001234567890 or -1001234567890)
            channel_id = -int(raw_id)
        else:
            # Short format, add -100 prefix
            channel_id = int(f"-100{raw_id}")
        return ChannelValidationResult(
            is_valid=True,
            identifier_type=ChannelIdentifierType.CHANNEL_ID,
            channel_id=channel_id,
        )

    # Try to match full URL (public channel)
    url_match = re.match(CHANNEL_LINK_PATTERN, link)
    if url_match:
        return ChannelValidationResult(
            is_valid=True,
            identifier_type=ChannelIdentifierType.USERNAME,
            username=url_match.group("username"),
        )

    # Try to match @username format
    username_match = re.match(CHANNEL_USERNAME_PATTERN, link)
    if username_match:
        return ChannelValidationResult(
            is_valid=True,
            identifier_type=ChannelIdentifierType.USERNAME,
            username=username_match.group("username"),
        )

    return ChannelValidationResult(
        is_valid=False,
        error="Неверный формат. Используй @channel, t.me/channel, ID канала или invite-ссылку",
    )


def parse_channel_link(link: str) -> str | None:
    """
    Extract username from channel link.

    Args:
        link: Channel link or @username

    Returns:
        Username without @ prefix, or None if invalid
    """
    result = validate_channel_link(link)
    return result.username if result.is_valid else None


def parse_channel_links(text: str) -> list[tuple[str, ChannelValidationResult]]:
    """
    Parse multiple channel links from text.

    Args:
        text: Text containing channel links (one per line)

    Returns:
        List of (original_link, validation_result) tuples
    """
    results = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if line:
            results.append((line, validate_channel_link(line)))
    return results
