from telegram.ext import ConversationHandler

from src.shared.constants import BotState

# Conversation states for telegram.ext ConversationHandler
# Map BotState enum values to integer states

IDLE = 0
AUTH_METHOD_CHOICE = 1
AWAITING_PHONE = 2
AWAITING_CODE = 3
AWAITING_2FA = 4
AWAITING_QR = 5
MAIN_MENU = 6
SOURCES_MENU = 7
ADD_SOURCE_TEXT = 8
ADD_SOURCE_FILE = 9
REMOVE_SOURCE = 10
DESTINATION_SETUP = 11

# State mapping for easy conversion
STATE_MAP = {
    BotState.IDLE: IDLE,
    BotState.AUTH_METHOD_CHOICE: AUTH_METHOD_CHOICE,
    BotState.AWAITING_PHONE: AWAITING_PHONE,
    BotState.AWAITING_CODE: AWAITING_CODE,
    BotState.AWAITING_2FA: AWAITING_2FA,
    BotState.AWAITING_QR: AWAITING_QR,
    BotState.MAIN_MENU: MAIN_MENU,
    BotState.SOURCES_MENU: SOURCES_MENU,
    BotState.ADD_SOURCE_TEXT: ADD_SOURCE_TEXT,
    BotState.ADD_SOURCE_FILE: ADD_SOURCE_FILE,
    BotState.REMOVE_SOURCE: REMOVE_SOURCE,
    BotState.DESTINATION_SETUP: DESTINATION_SETUP,
}

# Reverse mapping
REVERSE_STATE_MAP = {v: k for k, v in STATE_MAP.items()}


def to_conversation_state(bot_state: BotState) -> int:
    """Convert BotState to conversation handler state."""
    return STATE_MAP.get(bot_state, IDLE)


def from_conversation_state(state: int) -> BotState:
    """Convert conversation handler state to BotState."""
    return REVERSE_STATE_MAP.get(state, BotState.IDLE)
