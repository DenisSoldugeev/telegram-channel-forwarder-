# Telegram Forward Bot

Автоматическая пересылка постов из каналов-доноров в целевой канал.

## Установка

```bash
# Создать виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# или: .venv\Scripts\activate  # Windows

# Установить зависимости
pip install -e ".[dev]"
```

## Настройка

1. Скопировать `.env.example` в `.env`
2. Заполнить:
   - `BOT_TOKEN` — токен от @BotFather
   - `API_ID`, `API_HASH` — с https://my.telegram.org
   - `SESSION_ENCRYPTION_KEY` — сгенерировать (см. ниже)

### Генерация ключа шифрования

```bash
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

## Запуск

```bash
python -m src.app.main
```

## Структура

```
src/
├── app/          # Entry point, config
├── bot/          # Telegram Bot API handlers
├── mtproto/      # Pyrogram MTProto client
├── services/     # Business logic
├── storage/      # Database models & repos
├── shared/       # Utils, constants
└── workers/      # Background tasks
```
