import pytest

from src.shared.utils.validators import (
    ChannelIdentifierType,
    normalize_phone,
    parse_channel_link,
    parse_channel_links,
    validate_channel_link,
    validate_phone,
)


class TestValidatePhone:
    """Tests for phone validation."""

    def test_valid_international_format(self):
        assert validate_phone("+79001234567") is True

    def test_valid_with_country_code(self):
        assert validate_phone("+1234567890123") is True

    def test_valid_with_spaces(self):
        assert validate_phone("+7 900 123 45 67") is True

    def test_valid_with_dashes(self):
        assert validate_phone("+7-900-123-45-67") is True

    def test_invalid_no_plus(self):
        assert validate_phone("79001234567") is False

    def test_invalid_too_short(self):
        assert validate_phone("+7900") is False

    def test_invalid_too_long(self):
        assert validate_phone("+79001234567890123456") is False

    def test_invalid_with_letters(self):
        assert validate_phone("+7900abc4567") is False


class TestNormalizePhone:
    """Tests for phone normalization."""

    def test_removes_spaces(self):
        assert normalize_phone("+7 900 123 45 67") == "+79001234567"

    def test_removes_dashes(self):
        assert normalize_phone("+7-900-123-45-67") == "+79001234567"

    def test_adds_plus(self):
        assert normalize_phone("79001234567") == "+79001234567"

    def test_keeps_existing_plus(self):
        assert normalize_phone("+79001234567") == "+79001234567"


class TestValidateChannelLink:
    """Tests for channel link validation."""

    def test_valid_https_link(self):
        result = validate_channel_link("https://t.me/durov")
        assert result.is_valid is True
        assert result.username == "durov"

    def test_valid_http_link(self):
        result = validate_channel_link("http://t.me/telegram")
        assert result.is_valid is True
        assert result.username == "telegram"

    def test_valid_telegram_me_link(self):
        result = validate_channel_link("https://telegram.me/channel")
        assert result.is_valid is True
        assert result.username == "channel"

    def test_valid_username_with_at(self):
        result = validate_channel_link("@durov")
        assert result.is_valid is True
        assert result.username == "durov"

    def test_valid_username_without_at(self):
        result = validate_channel_link("durov")
        assert result.is_valid is True
        assert result.username == "durov"

    def test_valid_username_with_underscore(self):
        result = validate_channel_link("@my_channel")
        assert result.is_valid is True
        assert result.username == "my_channel"

    def test_valid_username_with_numbers(self):
        result = validate_channel_link("@channel123")
        assert result.is_valid is True
        assert result.username == "channel123"

    def test_valid_private_invite_link(self):
        result = validate_channel_link("https://t.me/+abc123")
        assert result.is_valid is True
        assert result.identifier_type == ChannelIdentifierType.INVITE_LINK
        assert result.invite_link == "https://t.me/+abc123"

    def test_valid_joinchat_link(self):
        result = validate_channel_link("https://t.me/joinchat/abc123")
        assert result.is_valid is True
        assert result.identifier_type == ChannelIdentifierType.INVITE_LINK
        assert result.invite_link == "https://t.me/+abc123"

    def test_valid_channel_id_positive(self):
        result = validate_channel_link("1234567890")
        assert result.is_valid is True
        assert result.identifier_type == ChannelIdentifierType.CHANNEL_ID
        assert result.channel_id == -1001234567890

    def test_valid_channel_id_with_prefix(self):
        result = validate_channel_link("-1001234567890")
        assert result.is_valid is True
        assert result.identifier_type == ChannelIdentifierType.CHANNEL_ID
        assert result.channel_id == -1001234567890

    def test_invalid_short_username(self):
        result = validate_channel_link("@ab")
        assert result.is_valid is False

    def test_invalid_username_starts_with_number(self):
        result = validate_channel_link("@123channel")
        assert result.is_valid is False


class TestParseChannelLink:
    """Tests for channel link parsing."""

    def test_extracts_username(self):
        assert parse_channel_link("https://t.me/durov") == "durov"

    def test_returns_none_for_invalid(self):
        assert parse_channel_link("@ab") is None  # Too short


class TestParseChannelLinks:
    """Tests for multiple channel links parsing."""

    def test_parses_multiple_links(self):
        text = """https://t.me/channel1
        @channel2
        https://t.me/channel3"""

        results = parse_channel_links(text)
        assert len(results) == 3
        assert all(r[1].is_valid for r in results)

    def test_handles_mixed_valid_invalid(self):
        text = """https://t.me/valid
        @ab
        @valid2"""

        results = parse_channel_links(text)
        assert len(results) == 3
        assert results[0][1].is_valid is True
        assert results[1][1].is_valid is False  # @ab is too short
        assert results[2][1].is_valid is True

    def test_handles_empty_lines(self):
        text = """https://t.me/channel1

        @channel2

        """

        results = parse_channel_links(text)
        assert len(results) == 2
