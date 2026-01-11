from enum import Enum


class BotState(str, Enum):
    """FSM states for bot conversation."""

    IDLE = "idle"
    AWAITING_PHONE = "awaiting_phone"
    AWAITING_CODE = "awaiting_code"
    AWAITING_2FA = "awaiting_2fa"
    AUTHORIZED = "authorized"
    MAIN_MENU = "main_menu"
    SOURCES_MENU = "sources_menu"
    ADD_SOURCE_TEXT = "add_source_text"
    ADD_SOURCE_FILE = "add_source_file"
    REMOVE_SOURCE = "remove_source"
    DESTINATION_SETUP = "destination_setup"
    RUNNING = "running"
    SESSION_EXPIRED = "session_expired"


class MessageType(str, Enum):
    """Types of Telegram messages for forwarding."""

    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"
    VOICE = "voice"
    VIDEO_NOTE = "video_note"
    STICKER = "sticker"
    ANIMATION = "animation"
    POLL = "poll"
    MEDIA_GROUP = "media_group"
    LOCATION = "location"
    CONTACT = "contact"
    UNSUPPORTED = "unsupported"


class DeliveryStatus(str, Enum):
    """Status of message delivery."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class CallbackAction(str, Enum):
    """Callback query action prefixes."""

    MAIN_MENU = "main_menu"
    SOURCES = "sources"
    ADD_SOURCE = "add_source"
    ADD_SOURCE_TEXT = "add_source_text"
    ADD_SOURCE_FILE = "add_source_file"
    REMOVE_SOURCE = "remove_source"
    LIST_SOURCES = "list_sources"
    DESTINATION = "destination"
    RESET_DESTINATION = "reset_destination"
    RUN = "run"
    STOP = "stop"
    STATUS = "status"
    REAUTH = "reauth"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    PAGE = "page"


# Limits
MAX_SOURCES_PER_USER = 50
MAX_FILE_SIZE_BYTES = 1_048_576  # 1 MB
SUPPORTED_FILE_EXTENSIONS = {".txt", ".csv"}
ITEMS_PER_PAGE = 10

# Telegram link patterns
CHANNEL_LINK_PATTERN = (
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?P<username>[a-zA-Z][a-zA-Z0-9_]{3,31})"
)
CHANNEL_USERNAME_PATTERN = r"^@?(?P<username>[a-zA-Z][a-zA-Z0-9_]{3,31})$"
PHONE_PATTERN = r"^\+\d{10,15}$"

# Private channel patterns
CHANNEL_ID_PATTERN = r"^-?(?P<channel_id>\d{10,14})$"
CHANNEL_INVITE_PATTERN = (
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:\+|joinchat/)(?P<invite_hash>[a-zA-Z0-9_-]+)"
)
