import re
from dataclasses import dataclass

from src.shared.constants import (
    CHANNEL_LINK_PATTERN,
    CHANNEL_USERNAME_PATTERN,
    PHONE_PATTERN,
)


@dataclass
class ChannelValidationResult:
    """Result of channel link validation."""

    is_valid: bool
    username: str | None = None
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


def validate_channel_link(link: str) -> ChannelValidationResult:
    """
    Validate Telegram channel link or username.

    Args:
        link: Channel link or @username

    Returns:
        ChannelValidationResult with validation status and extracted username
    """
    link = link.strip()

    # Check for private invite links (not supported)
    if "/+" in link or "/joinchat/" in link:
        return ChannelValidationResult(
            is_valid=False,
            error="Private invite links are not supported. Use public channel links.",
        )

    # Try to match full URL
    url_match = re.match(CHANNEL_LINK_PATTERN, link)
    if url_match:
        return ChannelValidationResult(
            is_valid=True,
            username=url_match.group("username"),
        )

    # Try to match @username format
    username_match = re.match(CHANNEL_USERNAME_PATTERN, link)
    if username_match:
        return ChannelValidationResult(
            is_valid=True,
            username=username_match.group("username"),
        )

    return ChannelValidationResult(
        is_valid=False,
        error="Invalid channel link format. Use https://t.me/channel or @channel",
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
