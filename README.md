# Telegram Forward Bot

Telegram bot for automatic message forwarding from source channels to a destination channel or direct messages.

## Features

- Forward from multiple channels simultaneously (up to 50)
- Support for all content types: text, photos, videos, documents, voice, polls, albums
- Keyword filtering (blacklist/whitelist modes)
- Forward to channel or DM
- User session encryption (AES-256)
- Docker deployment (PostgreSQL or SQLite)

## Quick Start

### Requirements

- Python 3.11+
- Telegram Bot Token ([@BotFather](https://t.me/BotFather))
- API ID and API Hash ([my.telegram.org](https://my.telegram.org/apps))

### Installation

```bash
# Clone repository
git clone https://github.com/your-username/tg-forward-bot.git
cd tg-forward-bot

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -e ".[dev]"
```

### Configuration

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Fill in required variables:
   ```env
   BOT_TOKEN=<from @BotFather>
   API_ID=<from my.telegram.org>
   API_HASH=<from my.telegram.org>
   SESSION_ENCRYPTION_KEY=<generate below>
   ```

3. Generate encryption key:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

### Run

```bash
python -m src.app.main
```

## Docker

### PostgreSQL (production)

```bash
# Configure .env (including POSTGRES_PASSWORD)
make deploy
```

### SQLite (lightweight)

```bash
make up-sqlite
```

### Commands

```bash
make build      # Build image
make up         # Start with PostgreSQL
make up-sqlite  # Start with SQLite
make down       # Stop containers
make logs       # View bot logs
make restart    # Restart bot
make clean      # Remove containers and volumes
```

## Usage

1. `/start` — start the bot
2. Authenticate with phone number (MTProto)
3. Add source channels (links or @username)
4. Set destination channel
5. `/run` — start forwarding
6. `/stop` — stop forwarding
7. `/status` — view statistics

## Keyword Filtering

In `.env`:

```env
# Mode: blacklist (skip messages) or whitelist (forward only matching)
FILTER_MODE=blacklist

# Keywords, comma-separated
FILTER_KEYWORDS_RAW=ads,#ad,buy,discount

# Case sensitivity
FILTER_CASE_SENSITIVE=false
```

## Architecture

```
src/
├── app/          # Entry point, configuration
├── bot/          # Telegram Bot API (menus, commands)
├── mtproto/      # Pyrogram MTProto client
├── services/     # Business logic
├── storage/      # Database, repositories
├── shared/       # Utilities, constants
└── workers/      # Background tasks
```

The bot uses two clients:
- **Bot API** (python-telegram-bot) — user interface
- **MTProto** (Pyrogram) — channel reading and forwarding

## Development

```bash
# Tests
pytest
pytest --cov=src --cov-report=term-missing

# Linting
ruff check src tests
ruff format src tests

# Type checking
mypy src
```

## License

MIT