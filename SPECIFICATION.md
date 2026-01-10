# Telegram Message Forwarding Bot — Техническая Спецификация

**Версия:** 1.0
**Дата:** 2026-01-07
**Статус:** Draft

---

## Оглавление

1. [Краткое описание и границы системы](#1-краткое-описание-и-границы-системы)
2. [User Flow и State Machine](#2-user-flow-и-state-machine)
3. [UX бота](#3-ux-бота)
4. [Архитектура](#4-архитектура)
5. [Модель данных](#5-модель-данных)
6. [Алгоритмы пересылки](#6-алгоритмы-пересылки)
7. [Обработка ошибок и ретраи](#7-обработка-ошибок-и-ретраи)
8. [Безопасность](#8-безопасность)
9. [Нефункциональные требования](#9-нефункциональные-требования)
10. [План тестирования](#10-план-тестирования)
11. [План реализации](#11-план-реализации)

---

## 1. Краткое описание и границы системы

### 1.1 Что делаем

**Telegram Forwarding Bot** — система для автоматической пересылки постов из каналов-доноров в целевой канал/группу пользователя.

**Ключевые возможности:**
- Авторизация под личным Telegram-аккаунтом пользователя (MTProto)
- Управление списком каналов-источников через Bot API интерфейс
- Real-time мониторинг новых постов в каналах-донорах
- Пересылка всех типов контента с сохранением форматирования
- Автоматическое восстановление при сбоях сессии

### 1.2 Что НЕ делаем (Out of Scope)

| Исключено | Причина |
|-----------|---------|
| Редактирование контента перед пересылкой | MVP scope |
| Фильтрация по ключевым словам | Отдельная фича для v2 |
| Scheduled posting (отложенный постинг) | Отдельная фича |
| Множественные destination-каналы | MVP: один destination на пользователя |
| Web-интерфейс администрирования | CLI/Bot достаточно для MVP |
| Мониторинг личных чатов/групп | Только публичные/приватные каналы |
| Подписка на каналы от имени пользователя | Пользователь подписывается сам |

### 1.3 Допущения

| ID | Допущение |
|----|-----------|
| A-01 | Один пользователь = один личный Telegram-аккаунт = одна MTProto-сессия |
| A-02 | Один destination-канал на пользователя в MVP |
| A-03 | Пользователь самостоятельно подписан на все каналы-доноры |
| A-04 | Бот запущен на VPS с постоянным соединением |
| A-05 | SQLite для MVP, PostgreSQL для production |
| A-06 | Pyrogram как MTProto-библиотека (лучше документация, активнее поддержка) |
| A-07 | python-telegram-bot для Bot API интерфейса |
| A-08 | Destination определяется явно командой пользователя (не автодетект) |

### 1.4 Ограничения Telegram API

| Ограничение | Значение | Митигация |
|-------------|----------|-----------|
| FloodWait | Динамический, 1-86400 сек | Exponential backoff, queue |
| Сообщений в секунду | ~30 msg/sec на аккаунт | Rate limiter |
| Размер файла | 2GB (premium), 4GB upload | Проверка перед отправкой |
| Количество каналов | ~500 на аккаунт | Валидация при добавлении |
| Session lifetime | Бессрочно, но может быть отозвана | Мониторинг + re-auth flow |

---

## 2. User Flow и State Machine

### 2.1 Состояния системы

```mermaid
stateDiagram-v2
    [*] --> IDLE: /start

    IDLE --> AWAITING_PHONE: Начать авторизацию
    AWAITING_PHONE --> AWAITING_CODE: Телефон принят
    AWAITING_PHONE --> AWAITING_PHONE: Неверный формат

    AWAITING_CODE --> AWAITING_2FA: Код верный, 2FA включена
    AWAITING_CODE --> AUTHORIZED: Код верный, 2FA выключена
    AWAITING_CODE --> AWAITING_PHONE: Неверный код (3 попытки)

    AWAITING_2FA --> AUTHORIZED: Пароль верный
    AWAITING_2FA --> AWAITING_PHONE: Неверный пароль (3 попытки)

    AUTHORIZED --> MAIN_MENU: Сессия создана

    MAIN_MENU --> SOURCES_MENU: Каналы
    MAIN_MENU --> DESTINATION_SETUP: Назначить получателя
    MAIN_MENU --> MONITORING: Статус

    SOURCES_MENU --> ADD_SOURCE_TEXT: Добавить → Текст
    SOURCES_MENU --> ADD_SOURCE_FILE: Добавить → Файл
    SOURCES_MENU --> REMOVE_SOURCE: Удалить
    SOURCES_MENU --> MAIN_MENU: Назад

    ADD_SOURCE_TEXT --> SOURCES_MENU: Источники добавлены
    ADD_SOURCE_FILE --> SOURCES_MENU: Файл обработан
    REMOVE_SOURCE --> SOURCES_MENU: Источник удалён

    DESTINATION_SETUP --> MAIN_MENU: Получатель назначен

    MONITORING --> MAIN_MENU: Назад

    AUTHORIZED --> SESSION_EXPIRED: Сессия истекла
    SESSION_EXPIRED --> AWAITING_PHONE: Повторная авторизация

    MAIN_MENU --> RUNNING: Запустить мониторинг
    RUNNING --> MAIN_MENU: Остановить
    RUNNING --> SESSION_EXPIRED: Ошибка сессии
```

### 2.2 Описание состояний

| Состояние | Описание | Допустимые действия |
|-----------|----------|---------------------|
| `IDLE` | Начальное, пользователь не авторизован | /start |
| `AWAITING_PHONE` | Ожидание номера телефона | Ввод телефона, /cancel |
| `AWAITING_CODE` | Ожидание SMS/Telegram кода | Ввод кода, /cancel |
| `AWAITING_2FA` | Ожидание облачного пароля | Ввод пароля, /cancel |
| `AUTHORIZED` | Сессия создана, но не настроена | Переход в меню |
| `MAIN_MENU` | Главное меню | Навигация |
| `SOURCES_MENU` | Управление источниками | Add/Remove/Back |
| `ADD_SOURCE_TEXT` | Ввод ссылок текстом | Ввод, /done, /cancel |
| `ADD_SOURCE_FILE` | Ожидание файла | Файл, /cancel |
| `REMOVE_SOURCE` | Выбор источника для удаления | Выбор, /cancel |
| `DESTINATION_SETUP` | Настройка получателя | Ввод @username или forward |
| `RUNNING` | Мониторинг активен | /stop, /status |
| `SESSION_EXPIRED` | Сессия недействительна | Re-auth |

### 2.3 Переходы состояний (Events)

```mermaid
flowchart LR
    subgraph Events
        E1[USER_INPUT]
        E2[VALID_PHONE]
        E3[VALID_CODE]
        E4[VALID_2FA]
        E5[SESSION_CREATED]
        E6[SESSION_EXPIRED]
        E7[TIMEOUT]
        E8[CANCEL]
    end
```

---

## 3. UX бота

### 3.1 Команды

| Команда | Описание | Доступность |
|---------|----------|-------------|
| `/start` | Запуск бота, приветствие | Всегда |
| `/auth` | Начать/повторить авторизацию | Не авторизован |
| `/channels` | Меню управления каналами | Авторизован |
| `/add` | Добавить источник | Авторизован |
| `/remove` | Удалить источник | Авторизован |
| `/destination` | Установить получателя | Авторизован |
| `/status` | Текущий статус системы | Авторизован |
| `/run` | Запустить мониторинг | Настроен |
| `/stop` | Остановить мониторинг | Запущен |
| `/help` | Справка | Всегда |
| `/cancel` | Отмена текущего действия | В процессе ввода |

### 3.2 Callback Data Schema

```
action:{action_name}
source:{action}:{source_id}
page:{menu}:{page_num}
confirm:{action}:{entity_id}
```

**Примеры:**
- `action:add_source_text`
- `source:remove:123`
- `page:sources:2`
- `confirm:delete:456`

### 3.3 Тексты сообщений

#### 3.3.1 Onboarding Flow

**START_MESSAGE:**
```
Привет! Я помогу автоматически пересылать посты из каналов в твой канал.

Для работы мне нужно авторизоваться под твоим Telegram-аккаунтом,
чтобы читать каналы, на которые ты подписан.

⚠️ Важно: я НЕ храню твой пароль. Сессия зашифрована и хранится безопасно.

Нажми «Начать авторизацию» для продолжения.
```

**Клавиатура:**
```
[🔐 Начать авторизацию]
[❓ Как это работает?]
```

**PHONE_REQUEST:**
```
Введи номер телефона в международном формате:

Пример: +79001234567

Этот номер используется только для авторизации в Telegram.
```

**CODE_REQUEST:**
```
Код подтверждения отправлен в Telegram.

Введи код из 5 цифр:

⚠️ Не делись этим кодом ни с кем, кроме этого бота.
```

**2FA_REQUEST:**
```
У тебя включена двухфакторная аутентификация.

Введи облачный пароль:

🔒 Пароль не сохраняется и используется только для текущей авторизации.
```

**AUTH_SUCCESS:**
```
✅ Авторизация успешна!

Сессия создана и сохранена. Теперь настроим пересылку.

Следующие шаги:
1. Добавь каналы-источники
2. Укажи канал-получатель
3. Запусти мониторинг
```

**Клавиатура:**
```
[📺 Каналы-источники]
[📤 Канал-получатель]
[▶️ Запустить]
[📊 Статус]
```

#### 3.3.2 Source Management

**SOURCES_MENU:**
```
📺 Управление источниками

Активных каналов: {count}

Выбери действие:
```

**Клавиатура:**
```
[➕ Добавить] [➖ Удалить]
[📋 Список ({count})]
[◀️ Назад]
```

**ADD_SOURCE_PROMPT:**
```
Как добавить источники?
```

**Клавиатура:**
```
[📝 Ввести ссылки] [📄 Загрузить файл]
[◀️ Назад]
```

**ADD_SOURCE_TEXT_PROMPT:**
```
Отправь ссылки на каналы (по одной на строку):

Формат: https://t.me/channel_name или @channel_name

Когда закончишь, нажми «Готово».
```

**SOURCE_VALIDATION_RESULT:**
```
Результат проверки:

✅ Добавлены ({success_count}):
{success_list}

❌ Ошибки ({error_count}):
{error_list}
```

#### 3.3.3 Destination Setup

**DESTINATION_PROMPT:**
```
📤 Настройка получателя

Отправь мне:
• Ссылку на канал (https://t.me/your_channel)
• Или username (@your_channel)
• Или перешли любое сообщение из канала

⚠️ Убедись, что бот добавлен в канал как администратор с правом публикации.
```

**DESTINATION_SUCCESS:**
```
✅ Канал-получатель установлен:

📺 {channel_title}
👤 @{channel_username}

Посты из {source_count} источников будут пересылаться сюда.
```

#### 3.3.4 Status & Monitoring

**STATUS_MESSAGE:**
```
📊 Статус системы

🔐 Сессия: {session_status}
📺 Источников: {source_count}
📤 Получатель: {destination_name}
⚡ Мониторинг: {monitoring_status}

📈 Статистика (24ч):
• Переслано: {forwarded_count}
• Ошибок: {error_count}
• Последний пост: {last_forward_time}
```

#### 3.3.5 Error Messages

| Код ошибки | Сообщение |
|------------|-----------|
| `ERR_INVALID_PHONE` | ❌ Неверный формат номера. Используй международный формат: +79001234567 |
| `ERR_INVALID_CODE` | ❌ Неверный код. Осталось попыток: {remaining} |
| `ERR_INVALID_2FA` | ❌ Неверный пароль. Осталось попыток: {remaining} |
| `ERR_SESSION_EXPIRED` | ⚠️ Сессия истекла. Требуется повторная авторизация. |
| `ERR_NOT_SUBSCRIBED` | ❌ Ты не подписан на канал {channel}. Подпишись и попробуй снова. |
| `ERR_BOT_NOT_ADMIN` | ❌ Бот не является администратором в канале {channel}. |
| `ERR_FLOOD_WAIT` | ⏳ Telegram временно ограничил запросы. Повтор через {seconds} сек. |
| `ERR_FORWARD_FAILED` | ❌ Не удалось переслать пост из {source}: {reason} |

### 3.4 Keyboard Layouts

```python
# Main Menu
MAIN_MENU_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📺 Каналы", callback_data="action:sources")],
    [InlineKeyboardButton("📤 Получатель", callback_data="action:destination")],
    [InlineKeyboardButton("▶️ Запустить", callback_data="action:run")],
    [InlineKeyboardButton("📊 Статус", callback_data="action:status")],
])

# Sources Menu
SOURCES_MENU_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("➕ Добавить", callback_data="action:add_source"),
        InlineKeyboardButton("➖ Удалить", callback_data="action:remove_source"),
    ],
    [InlineKeyboardButton("📋 Список", callback_data="action:list_sources")],
    [InlineKeyboardButton("◀️ Назад", callback_data="action:main_menu")],
])
```

---

## 4. Архитектура

### 4.1 Компонентная диаграмма

```mermaid
flowchart TB
    subgraph External["External Services"]
        TG_BOT_API["Telegram Bot API"]
        TG_MTPROTO["Telegram MTProto"]
    end

    subgraph Application["Application Layer"]
        BOT["Bot Controller<br/>(python-telegram-bot)"]
        MTPROTO_CLIENT["MTProto Client<br/>(Pyrogram)"]
        EVENT_DISPATCHER["Event Dispatcher"]
        FORWARDER["Forwarder Service"]
    end

    subgraph Workers["Background Workers"]
        SESSION_MONITOR["Session Monitor"]
        UPDATE_LISTENER["Update Listener"]
        QUEUE_PROCESSOR["Queue Processor"]
    end

    subgraph Storage["Storage Layer"]
        DB[(SQLite/PostgreSQL)]
        SESSION_STORE["Encrypted Session Store"]
        CACHE["Redis Cache<br/>(optional)"]
    end

    subgraph Core["Core Services"]
        AUTH_SERVICE["Auth Service"]
        SOURCE_SERVICE["Source Service"]
        DESTINATION_SERVICE["Destination Service"]
        DELIVERY_SERVICE["Delivery Service"]
    end

    TG_BOT_API <--> BOT
    TG_MTPROTO <--> MTPROTO_CLIENT

    BOT --> EVENT_DISPATCHER
    MTPROTO_CLIENT --> UPDATE_LISTENER

    EVENT_DISPATCHER --> AUTH_SERVICE
    EVENT_DISPATCHER --> SOURCE_SERVICE
    EVENT_DISPATCHER --> DESTINATION_SERVICE

    UPDATE_LISTENER --> FORWARDER
    FORWARDER --> DELIVERY_SERVICE
    FORWARDER --> QUEUE_PROCESSOR

    AUTH_SERVICE --> SESSION_STORE
    SOURCE_SERVICE --> DB
    DESTINATION_SERVICE --> DB
    DELIVERY_SERVICE --> DB

    SESSION_MONITOR --> SESSION_STORE
    SESSION_MONITOR --> AUTH_SERVICE

    QUEUE_PROCESSOR --> MTPROTO_CLIENT
```

### 4.2 Структура проекта

```
src/
├── app/
│   ├── __init__.py
│   ├── main.py                 # Entry point
│   ├── config.py               # Configuration loader
│   └── container.py            # DI container
│
├── bot/
│   ├── __init__.py
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── start.py            # /start, /help
│   │   ├── auth.py             # Authorization flow
│   │   ├── sources.py          # Source management
│   │   ├── destination.py      # Destination setup
│   │   ├── monitoring.py       # /run, /stop, /status
│   │   └── errors.py           # Error handlers
│   ├── keyboards/
│   │   ├── __init__.py
│   │   └── inline.py           # Keyboard builders
│   ├── states.py               # FSM states
│   ├── filters.py              # Custom filters
│   └── middlewares/
│       ├── __init__.py
│       ├── auth.py             # Auth check middleware
│       └── logging.py          # Request logging
│
├── mtproto/
│   ├── __init__.py
│   ├── client.py               # Pyrogram client wrapper
│   ├── auth.py                 # MTProto auth logic
│   ├── session_manager.py      # Session encryption/storage
│   └── handlers/
│       ├── __init__.py
│       └── new_message.py      # Channel message handler
│
├── services/
│   ├── __init__.py
│   ├── auth_service.py         # Auth business logic
│   ├── source_service.py       # Source management
│   ├── destination_service.py  # Destination management
│   ├── forwarder_service.py    # Forwarding logic
│   └── delivery_service.py     # Delivery tracking
│
├── workers/
│   ├── __init__.py
│   ├── session_monitor.py      # Session health check
│   ├── update_listener.py      # MTProto updates
│   └── queue_processor.py      # Message queue worker
│
├── storage/
│   ├── __init__.py
│   ├── database.py             # DB connection
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── user_repo.py
│   │   ├── source_repo.py
│   │   ├── destination_repo.py
│   │   └── delivery_repo.py
│   └── models/
│       ├── __init__.py
│       └── entities.py         # SQLAlchemy models
│
├── shared/
│   ├── __init__.py
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── validators.py       # Input validation
│   │   ├── formatters.py       # Text formatting
│   │   └── crypto.py           # Encryption utils
│   ├── exceptions.py           # Custom exceptions
│   └── constants.py            # App constants
│
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

### 4.3 Sequence Diagram: Новый пост → Пересылка

```mermaid
sequenceDiagram
    participant TC as Telegram Channel
    participant MTProto as MTProto Client
    participant UL as Update Listener
    participant FS as Forwarder Service
    participant DS as Delivery Service
    participant DB as Database
    participant QP as Queue Processor
    participant DEST as Destination Channel
    participant BOT as Bot (notify user)

    TC->>MTProto: New message event
    MTProto->>UL: on_message(message)

    UL->>DB: Check if source is tracked
    DB-->>UL: Source config

    alt Source is tracked
        UL->>DB: Check for duplicate (message_id)
        DB-->>UL: Not duplicate

        UL->>FS: forward_message(message, destination)

        FS->>FS: Determine message type
        FS->>FS: Prepare forward payload

        alt Media Group (Album)
            FS->>FS: Wait for grouped messages (2s timeout)
            FS->>FS: Collect all media in group
        end

        FS->>QP: Enqueue forward task
        QP->>QP: Apply rate limiting

        QP->>MTProto: send_message/copy_message
        MTProto->>DEST: Forward content

        alt Success
            DEST-->>MTProto: Message sent
            MTProto-->>QP: Success response
            QP->>DS: Log delivery success
            DS->>DB: Insert delivery log
        else Failure
            MTProto-->>QP: Error response
            QP->>QP: Retry with backoff

            alt Max retries exceeded
                QP->>DS: Log delivery failure
                DS->>DB: Insert error log
                DS->>BOT: Notify user
                BOT->>BOT: Send error notification
            end
        end
    else Source not tracked
        UL->>UL: Ignore message
    end
```

### 4.4 Sequence Diagram: Авторизация

```mermaid
sequenceDiagram
    participant U as User
    participant BOT as Bot API Handler
    participant AS as Auth Service
    participant MTProto as MTProto Client
    participant SM as Session Manager
    participant DB as Database
    participant TG as Telegram

    U->>BOT: /start
    BOT->>BOT: Check user state
    BOT-->>U: Welcome message + Auth button

    U->>BOT: Click "Начать авторизацию"
    BOT->>BOT: Set state: AWAITING_PHONE
    BOT-->>U: Request phone number

    U->>BOT: +79001234567
    BOT->>AS: validate_phone(phone)
    AS-->>BOT: Valid

    BOT->>AS: start_auth(user_id, phone)
    AS->>MTProto: send_code(phone)
    MTProto->>TG: auth.sendCode
    TG-->>MTProto: SentCode(phone_code_hash)
    MTProto-->>AS: phone_code_hash
    AS->>DB: Store temp auth state

    BOT->>BOT: Set state: AWAITING_CODE
    BOT-->>U: Request verification code

    U->>BOT: 12345
    BOT->>AS: verify_code(user_id, code)
    AS->>DB: Get phone_code_hash
    AS->>MTProto: sign_in(phone, code, hash)
    MTProto->>TG: auth.signIn

    alt 2FA Required
        TG-->>MTProto: SessionPasswordNeeded
        MTProto-->>AS: Needs 2FA
        AS-->>BOT: Requires 2FA
        BOT->>BOT: Set state: AWAITING_2FA
        BOT-->>U: Request 2FA password

        U->>BOT: my_password
        BOT->>AS: verify_2fa(user_id, password)
        AS->>MTProto: check_password(password)
        MTProto->>TG: auth.checkPassword
        TG-->>MTProto: Authorization success
    else No 2FA
        TG-->>MTProto: Authorization success
    end

    MTProto-->>AS: Session created
    AS->>SM: encrypt_and_store(session)
    SM->>SM: Encrypt with user key
    SM->>DB: Store encrypted session

    AS->>DB: Update user status = AUTHORIZED
    AS-->>BOT: Auth complete

    BOT->>BOT: Set state: MAIN_MENU
    BOT-->>U: Success message + Main menu
```

### 4.5 Component Interfaces (Псевдокод)

```python
# === Auth Service ===
class AuthService(Protocol):
    async def start_auth(self, user_id: int, phone: str) -> AuthState:
        """Initiates phone verification."""

    async def verify_code(self, user_id: int, code: str) -> AuthResult:
        """Verifies SMS/Telegram code."""

    async def verify_2fa(self, user_id: int, password: str) -> AuthResult:
        """Verifies 2FA cloud password."""

    async def check_session(self, user_id: int) -> SessionStatus:
        """Checks if session is valid."""

    async def invalidate_session(self, user_id: int) -> None:
        """Marks session as invalid, triggers re-auth."""


# === Source Service ===
class SourceService(Protocol):
    async def add_sources(
        self, user_id: int, links: list[str]
    ) -> SourceValidationResult:
        """Validates and adds channel sources."""

    async def add_sources_from_file(
        self, user_id: int, file_content: bytes, filename: str
    ) -> SourceValidationResult:
        """Parses file and adds sources."""

    async def remove_source(self, user_id: int, source_id: int) -> bool:
        """Removes a source."""

    async def list_sources(
        self, user_id: int, page: int = 1
    ) -> PaginatedResult[Source]:
        """Lists user's sources with pagination."""

    async def validate_subscription(
        self, user_id: int, channel_link: str
    ) -> SubscriptionStatus:
        """Checks if user is subscribed to channel."""


# === Forwarder Service ===
class ForwarderService(Protocol):
    async def forward_message(
        self,
        message: Message,
        source: Source,
        destination: Destination,
    ) -> ForwardResult:
        """Forwards a single message."""

    async def forward_media_group(
        self,
        messages: list[Message],
        source: Source,
        destination: Destination,
    ) -> ForwardResult:
        """Forwards album as a group."""

    def determine_message_type(self, message: Message) -> MessageType:
        """Classifies message type for proper handling."""


# === Delivery Service ===
class DeliveryService(Protocol):
    async def log_success(
        self,
        user_id: int,
        source_id: int,
        original_message_id: int,
        forwarded_message_id: int,
    ) -> None:
        """Logs successful delivery."""

    async def log_failure(
        self,
        user_id: int,
        source_id: int,
        original_message_id: int,
        error: str,
        will_retry: bool,
    ) -> None:
        """Logs failed delivery."""

    async def check_duplicate(
        self, user_id: int, source_id: int, message_id: int
    ) -> bool:
        """Checks if message was already forwarded."""

    async def get_stats(
        self, user_id: int, period_hours: int = 24
    ) -> DeliveryStats:
        """Returns delivery statistics."""
```

---

## 5. Модель данных

### 5.1 ER-диаграмма

```mermaid
erDiagram
    USERS ||--o{ SESSIONS : has
    USERS ||--o{ SOURCES : manages
    USERS ||--|| DESTINATIONS : has
    USERS ||--o{ FORWARDING_RULES : configures
    SOURCES ||--o{ DELIVERY_LOGS : generates
    DESTINATIONS ||--o{ DELIVERY_LOGS : receives

    USERS {
        bigint id PK "Telegram user_id"
        string phone "Encrypted"
        string state "FSM state"
        timestamp created_at
        timestamp updated_at
        boolean is_active
    }

    SESSIONS {
        int id PK
        bigint user_id FK
        blob session_data "Encrypted Pyrogram session"
        string session_hash "For quick validation"
        boolean is_valid
        timestamp created_at
        timestamp last_used_at
        timestamp expires_at "Optional"
    }

    SOURCES {
        int id PK
        bigint user_id FK
        bigint channel_id "Telegram channel ID"
        string channel_username
        string channel_title
        boolean is_active
        bigint last_message_id "For offset tracking"
        timestamp added_at
        timestamp last_checked_at
    }

    DESTINATIONS {
        int id PK
        bigint user_id FK "Unique constraint"
        bigint channel_id "Telegram channel ID"
        string channel_username
        string channel_title
        boolean is_active
        timestamp configured_at
    }

    FORWARDING_RULES {
        int id PK
        bigint user_id FK
        int source_id FK
        int destination_id FK
        boolean is_enabled
        json filters "Future: keyword filters etc"
        timestamp created_at
    }

    DELIVERY_LOGS {
        bigint id PK
        bigint user_id FK
        int source_id FK
        int destination_id FK
        bigint original_message_id
        bigint forwarded_message_id "Nullable if failed"
        string status "success/failed/pending"
        string error_message "Nullable"
        int retry_count
        timestamp created_at
        timestamp completed_at
    }

    AUTH_ATTEMPTS {
        int id PK
        bigint user_id FK
        string phone "Encrypted"
        string phone_code_hash "Temp storage"
        int attempt_count
        string stage "phone/code/2fa"
        timestamp created_at
        timestamp expires_at
    }
```

### 5.2 Таблицы: детальное описание

#### USERS

| Поле | Тип | Constraints | Описание |
|------|-----|-------------|----------|
| `id` | BIGINT | PK | Telegram user_id |
| `phone` | VARCHAR(255) | ENCRYPTED, NULLABLE | Зашифрованный номер телефона |
| `state` | VARCHAR(50) | NOT NULL, DEFAULT 'IDLE' | Текущее FSM состояние |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT NOW | Дата регистрации |
| `updated_at` | TIMESTAMP | NOT NULL | Последнее обновление |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT TRUE | Активен ли пользователь |

**Индексы:**
- `idx_users_state` ON (state) — для выборки по состоянию
- `idx_users_active` ON (is_active) — для фильтрации активных

#### SESSIONS

| Поле | Тип | Constraints | Описание |
|------|-----|-------------|----------|
| `id` | SERIAL | PK | Auto-increment |
| `user_id` | BIGINT | FK → USERS, UNIQUE | Один пользователь = одна сессия |
| `session_data` | BYTEA | NOT NULL, ENCRYPTED | Зашифрованные данные сессии Pyrogram |
| `session_hash` | VARCHAR(64) | NOT NULL | SHA-256 для быстрой валидации |
| `is_valid` | BOOLEAN | NOT NULL, DEFAULT TRUE | Валидна ли сессия |
| `created_at` | TIMESTAMP | NOT NULL | Когда создана |
| `last_used_at` | TIMESTAMP | NOT NULL | Последнее использование |
| `expires_at` | TIMESTAMP | NULLABLE | Срок действия (если есть) |

**Индексы:**
- `idx_sessions_user` ON (user_id) — UNIQUE
- `idx_sessions_valid` ON (is_valid, user_id)

#### SOURCES

| Поле | Тип | Constraints | Описание |
|------|-----|-------------|----------|
| `id` | SERIAL | PK | Auto-increment |
| `user_id` | BIGINT | FK → USERS, NOT NULL | Владелец |
| `channel_id` | BIGINT | NOT NULL | Telegram channel ID |
| `channel_username` | VARCHAR(255) | NULLABLE | @username канала |
| `channel_title` | VARCHAR(255) | NOT NULL | Название канала |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT TRUE | Активен ли источник |
| `last_message_id` | BIGINT | DEFAULT 0 | ID последнего обработанного сообщения |
| `added_at` | TIMESTAMP | NOT NULL | Когда добавлен |
| `last_checked_at` | TIMESTAMP | NULLABLE | Последняя проверка |

**Индексы:**
- `idx_sources_user_channel` ON (user_id, channel_id) — UNIQUE
- `idx_sources_active` ON (user_id, is_active)

#### DESTINATIONS

| Поле | Тип | Constraints | Описание |
|------|-----|-------------|----------|
| `id` | SERIAL | PK | Auto-increment |
| `user_id` | BIGINT | FK → USERS, UNIQUE | Один destination на пользователя |
| `channel_id` | BIGINT | NOT NULL | Telegram channel ID |
| `channel_username` | VARCHAR(255) | NULLABLE | @username |
| `channel_title` | VARCHAR(255) | NOT NULL | Название |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT TRUE | Активен |
| `configured_at` | TIMESTAMP | NOT NULL | Когда настроен |

**Индексы:**
- `idx_destinations_user` ON (user_id) — UNIQUE

#### DELIVERY_LOGS

| Поле | Тип | Constraints | Описание |
|------|-----|-------------|----------|
| `id` | BIGSERIAL | PK | Auto-increment |
| `user_id` | BIGINT | FK → USERS, NOT NULL | Владелец |
| `source_id` | INT | FK → SOURCES, NOT NULL | Источник |
| `destination_id` | INT | FK → DESTINATIONS, NOT NULL | Получатель |
| `original_message_id` | BIGINT | NOT NULL | ID оригинального сообщения |
| `forwarded_message_id` | BIGINT | NULLABLE | ID пересланного сообщения |
| `status` | VARCHAR(20) | NOT NULL | success/failed/pending |
| `error_message` | TEXT | NULLABLE | Текст ошибки |
| `retry_count` | INT | NOT NULL, DEFAULT 0 | Количество ретраев |
| `created_at` | TIMESTAMP | NOT NULL | Создано |
| `completed_at` | TIMESTAMP | NULLABLE | Завершено |

**Индексы:**
- `idx_delivery_user_status` ON (user_id, status)
- `idx_delivery_dedup` ON (user_id, source_id, original_message_id) — для дедупликации
- `idx_delivery_created` ON (created_at DESC) — для статистики

**Партиционирование:** По `created_at` (месячное), с автоудалением старых партиций (>90 дней).

---

## 6. Алгоритмы пересылки

### 6.1 Определение типа сообщения

```python
class MessageType(Enum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"
    VOICE = "voice"
    VIDEO_NOTE = "video_note"
    STICKER = "sticker"
    ANIMATION = "animation"  # GIF
    POLL = "poll"
    MEDIA_GROUP = "media_group"  # Album
    LOCATION = "location"
    CONTACT = "contact"
    UNSUPPORTED = "unsupported"


def determine_message_type(message: Message) -> MessageType:
    """Определяет тип сообщения для правильной обработки."""

    if message.media_group_id:
        return MessageType.MEDIA_GROUP
    if message.poll:
        return MessageType.POLL
    if message.sticker:
        return MessageType.STICKER
    if message.animation:
        return MessageType.ANIMATION
    if message.video_note:
        return MessageType.VIDEO_NOTE
    if message.voice:
        return MessageType.VOICE
    if message.video:
        return MessageType.VIDEO
    if message.photo:
        return MessageType.PHOTO
    if message.audio:
        return MessageType.AUDIO
    if message.document:
        return MessageType.DOCUMENT
    if message.location:
        return MessageType.LOCATION
    if message.contact:
        return MessageType.CONTACT
    if message.text:
        return MessageType.TEXT

    return MessageType.UNSUPPORTED
```

### 6.2 Пересылка альбомов (Media Groups)

**Проблема:** Telegram отправляет сообщения альбома по одному, но с общим `media_group_id`. Нужно собрать все части и отправить как единый альбом.

```python
class MediaGroupCollector:
    """Собирает сообщения альбома для групповой отправки."""

    def __init__(self, timeout_seconds: float = 2.0):
        self.timeout = timeout_seconds
        self.pending_groups: dict[str, MediaGroupBuffer] = {}
        self._lock = asyncio.Lock()

    async def add_message(self, message: Message) -> list[Message] | None:
        """
        Добавляет сообщение в буфер группы.
        Возвращает список сообщений, когда группа готова к отправке.
        """
        group_id = message.media_group_id
        if not group_id:
            return None

        async with self._lock:
            if group_id not in self.pending_groups:
                self.pending_groups[group_id] = MediaGroupBuffer(
                    group_id=group_id,
                    created_at=time.time(),
                )
                # Запускаем таймер на flush
                asyncio.create_task(self._schedule_flush(group_id))

            self.pending_groups[group_id].messages.append(message)
            return None

    async def _schedule_flush(self, group_id: str) -> None:
        """Ждёт timeout и возвращает группу для отправки."""
        await asyncio.sleep(self.timeout)

        async with self._lock:
            if group_id in self.pending_groups:
                buffer = self.pending_groups.pop(group_id)
                # Сортируем по message_id для правильного порядка
                messages = sorted(buffer.messages, key=lambda m: m.id)
                await self._forward_group(messages)

    async def _forward_group(self, messages: list[Message]) -> None:
        """Пересылает собранную группу."""
        # Формируем InputMedia[] для send_media_group
        media_list = []
        for i, msg in enumerate(messages):
            if msg.photo:
                media = InputMediaPhoto(
                    media=msg.photo.file_id,
                    caption=msg.caption if i == 0 else None,
                    caption_entities=msg.caption_entities if i == 0 else None,
                )
            elif msg.video:
                media = InputMediaVideo(
                    media=msg.video.file_id,
                    caption=msg.caption if i == 0 else None,
                    caption_entities=msg.caption_entities if i == 0 else None,
                )
            elif msg.document:
                media = InputMediaDocument(
                    media=msg.document.file_id,
                    caption=msg.caption if i == 0 else None,
                )
            else:
                continue
            media_list.append(media)

        if media_list:
            await client.send_media_group(
                chat_id=destination_id,
                media=media_list,
            )
```

### 6.3 Сохранение форматирования текста

```python
async def forward_with_formatting(
    message: Message,
    destination_id: int,
) -> Message:
    """
    Пересылает сообщение с сохранением форматирования.
    Использует copy_message для чистой копии без "Forwarded from".
    """

    # Метод 1: copy_message (рекомендуемый)
    # Копирует сообщение без метки пересылки
    result = await client.copy_message(
        chat_id=destination_id,
        from_chat_id=message.chat.id,
        message_id=message.id,
        # Сохраняет entities (bold, italic, links, etc.)
    )
    return result

    # Метод 2: Ручная отправка с entities (если нужна модификация)
    # text = message.text or message.caption
    # entities = message.entities or message.caption_entities
    #
    # await client.send_message(
    #     chat_id=destination_id,
    #     text=text,
    #     entities=entities,  # Сохраняет форматирование
    # )
```

### 6.4 Обработка специфичных типов

#### Polls (Опросы)

```python
async def forward_poll(message: Message, destination_id: int) -> None:
    """Пересылает опрос."""
    poll = message.poll

    # Polls нельзя скопировать — только переслать или создать заново
    # forward_message сохраняет "Forwarded from"
    # Для чистого копирования — пересоздаём

    await client.send_poll(
        chat_id=destination_id,
        question=poll.question,
        options=[opt.text for opt in poll.options],
        is_anonymous=poll.is_anonymous,
        type=poll.type,  # "regular" or "quiz"
        allows_multiple_answers=poll.allows_multiple_answers,
        correct_option_id=poll.correct_option_id,  # Для quiz
        explanation=poll.explanation,
        explanation_entities=poll.explanation_entities,
        is_closed=False,  # Новый опрос открыт
    )
```

#### Voice / Video Notes

```python
async def forward_voice(message: Message, destination_id: int) -> None:
    """Пересылает голосовое сообщение."""
    await client.send_voice(
        chat_id=destination_id,
        voice=message.voice.file_id,
        caption=message.caption,
        caption_entities=message.caption_entities,
        duration=message.voice.duration,
    )


async def forward_video_note(message: Message, destination_id: int) -> None:
    """Пересылает видео-кружок."""
    await client.send_video_note(
        chat_id=destination_id,
        video_note=message.video_note.file_id,
        duration=message.video_note.duration,
        length=message.video_note.length,
    )
```

### 6.5 Идемпотентность и дедупликация

```python
class DeduplicationService:
    """Предотвращает повторную пересылку одного сообщения."""

    async def should_forward(
        self,
        user_id: int,
        source_id: int,
        message_id: int,
    ) -> bool:
        """Проверяет, было ли сообщение уже переслано."""

        # Проверка в delivery_logs
        existing = await self.delivery_repo.find_by_message(
            user_id=user_id,
            source_id=source_id,
            original_message_id=message_id,
        )

        if existing:
            if existing.status == "success":
                return False  # Уже переслано
            if existing.status == "pending" and existing.retry_count < MAX_RETRIES:
                return True  # Retry
            if existing.status == "failed" and existing.retry_count >= MAX_RETRIES:
                return False  # Max retries exceeded

        return True  # Новое сообщение

    async def mark_as_processing(
        self,
        user_id: int,
        source_id: int,
        message_id: int,
    ) -> int:
        """Создаёт запись pending для предотвращения race condition."""
        return await self.delivery_repo.create(
            user_id=user_id,
            source_id=source_id,
            original_message_id=message_id,
            status="pending",
        )
```

### 6.6 Offset Tracking

```python
class OffsetTracker:
    """Отслеживает последние обработанные сообщения для каждого источника."""

    async def get_last_message_id(self, source_id: int) -> int:
        """Возвращает ID последнего обработанного сообщения."""
        source = await self.source_repo.get(source_id)
        return source.last_message_id or 0

    async def update_offset(self, source_id: int, message_id: int) -> None:
        """Обновляет offset после успешной обработки."""
        current = await self.get_last_message_id(source_id)
        if message_id > current:
            await self.source_repo.update(
                source_id,
                last_message_id=message_id,
                last_checked_at=datetime.utcnow(),
            )

    async def handle_message(self, source_id: int, message: Message) -> bool:
        """
        Проверяет, нужно ли обрабатывать сообщение.
        Возвращает True, если сообщение новое.
        """
        last_id = await self.get_last_message_id(source_id)

        if message.id <= last_id:
            return False  # Уже обработано

        return True
```

---

## 7. Обработка ошибок и ретраи

### 7.1 Типы ошибок Telegram

| Ошибка | Код | Описание | Действие |
|--------|-----|----------|----------|
| `FLOOD_WAIT_X` | 420 | Rate limit | Ждать X секунд |
| `CHAT_WRITE_FORBIDDEN` | 403 | Нет прав на запись | Уведомить пользователя |
| `CHANNEL_PRIVATE` | 400 | Канал приватный | Уведомить пользователя |
| `USER_BANNED_IN_CHANNEL` | 403 | Бан в канале | Уведомить, деактивировать источник |
| `AUTH_KEY_UNREGISTERED` | 401 | Сессия невалидна | Re-auth flow |
| `SESSION_REVOKED` | 401 | Сессия отозвана | Re-auth flow |
| `PHONE_CODE_INVALID` | 400 | Неверный код | Retry с лимитом |
| `PASSWORD_HASH_INVALID` | 400 | Неверный 2FA пароль | Retry с лимитом |
| `MEDIA_EMPTY` | 400 | Медиа недоступно | Skip, уведомить |
| `FILE_REFERENCE_EXPIRED` | 400 | Устаревшая ссылка на файл | Refresh, retry |

### 7.2 Стратегия ретраев

```python
class RetryStrategy:
    """Exponential backoff с jitter."""

    BASE_DELAY = 1.0  # секунды
    MAX_DELAY = 300.0  # 5 минут
    MAX_RETRIES = 5
    JITTER_FACTOR = 0.1

    @classmethod
    def calculate_delay(cls, attempt: int, flood_wait: int | None = None) -> float:
        """Вычисляет задержку перед следующей попыткой."""

        if flood_wait:
            # Telegram указал конкретное время ожидания
            return flood_wait + random.uniform(1, 5)

        # Exponential backoff: 1, 2, 4, 8, 16... секунд
        delay = min(cls.BASE_DELAY * (2 ** attempt), cls.MAX_DELAY)

        # Добавляем jitter для предотвращения thundering herd
        jitter = delay * cls.JITTER_FACTOR * random.random()

        return delay + jitter

    @classmethod
    def should_retry(cls, attempt: int, error: Exception) -> bool:
        """Определяет, нужно ли повторять попытку."""

        if attempt >= cls.MAX_RETRIES:
            return False

        # Ретраим только transient ошибки
        retryable_errors = [
            "FLOOD_WAIT",
            "TIMEOUT",
            "FILE_REFERENCE_EXPIRED",
            "INTERNAL_SERVER_ERROR",
            "SERVICE_UNAVAILABLE",
        ]

        error_name = type(error).__name__
        return any(err in str(error) for err in retryable_errors)


async def with_retry(
    func: Callable,
    *args,
    strategy: RetryStrategy = RetryStrategy(),
    **kwargs,
) -> Any:
    """Выполняет функцию с автоматическими ретраями."""

    last_error = None

    for attempt in range(strategy.MAX_RETRIES):
        try:
            return await func(*args, **kwargs)

        except FloodWait as e:
            delay = strategy.calculate_delay(attempt, flood_wait=e.value)
            logger.warning(f"FloodWait: sleeping {delay}s (attempt {attempt + 1})")
            await asyncio.sleep(delay)
            last_error = e

        except Exception as e:
            if not strategy.should_retry(attempt, e):
                raise

            delay = strategy.calculate_delay(attempt)
            logger.warning(f"Retrying after {delay}s: {e} (attempt {attempt + 1})")
            await asyncio.sleep(delay)
            last_error = e

    raise MaxRetriesExceeded(f"Max retries exceeded: {last_error}")
```

### 7.3 Уведомление пользователя (REQ-14)

```python
class UserNotificationService:
    """Отправляет уведомления пользователю о проблемах."""

    # Троттлинг уведомлений — не чаще раза в N минут для одного типа
    NOTIFICATION_COOLDOWN = 300  # 5 минут

    async def notify_forward_failed(
        self,
        user_id: int,
        source: Source,
        error: str,
        original_message_id: int,
    ) -> None:
        """Уведомляет о неудачной пересылке."""

        # Проверяем cooldown
        if await self._is_rate_limited(user_id, "forward_failed"):
            return

        text = (
            f"❌ **Ошибка пересылки**\n\n"
            f"Источник: {source.channel_title}\n"
            f"Сообщение ID: {original_message_id}\n"
            f"Причина: {error}\n\n"
            f"Попробую ещё раз автоматически."
        )

        await self.bot.send_message(user_id, text, parse_mode="Markdown")
        await self._set_rate_limit(user_id, "forward_failed")

    async def notify_session_expired(self, user_id: int) -> None:
        """Уведомляет об истёкшей сессии."""

        text = (
            "⚠️ **Сессия истекла**\n\n"
            "Telegram-сессия стала недействительной.\n"
            "Пересылка приостановлена.\n\n"
            "Нажми кнопку для повторной авторизации."
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔐 Авторизоваться", callback_data="action:reauth")]
        ])

        await self.bot.send_message(user_id, text, reply_markup=keyboard)

    async def notify_source_unavailable(
        self,
        user_id: int,
        source: Source,
        reason: str,
    ) -> None:
        """Уведомляет о недоступном источнике."""

        text = (
            f"⚠️ **Источник недоступен**\n\n"
            f"Канал: {source.channel_title}\n"
            f"Причина: {reason}\n\n"
            f"Источник деактивирован. Проверь подписку и добавь заново."
        )

        await self.bot.send_message(user_id, text)
```

### 7.4 Circuit Breaker

```python
class CircuitBreaker:
    """Предотвращает каскадные сбои при проблемах с внешним сервисом."""

    FAILURE_THRESHOLD = 5
    RECOVERY_TIMEOUT = 60  # секунд

    def __init__(self):
        self.failures = 0
        self.state = "closed"  # closed, open, half-open
        self.last_failure_time = None

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Выполняет функцию через circuit breaker."""

        if self.state == "open":
            if time.time() - self.last_failure_time > self.RECOVERY_TIMEOUT:
                self.state = "half-open"
            else:
                raise CircuitBreakerOpen("Service temporarily unavailable")

        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        self.failures = 0
        self.state = "closed"

    def _on_failure(self) -> None:
        self.failures += 1
        self.last_failure_time = time.time()

        if self.failures >= self.FAILURE_THRESHOLD:
            self.state = "open"
```

---

## 8. Безопасность

### 8.1 Хранение Session File

```python
# === Session Encryption ===

import os
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64


class SessionEncryption:
    """Шифрование сессий Pyrogram перед сохранением в БД."""

    def __init__(self, master_key: str):
        """
        master_key: Из переменной окружения SESSION_ENCRYPTION_KEY
        """
        self.master_key = master_key.encode()

    def _derive_key(self, user_id: int) -> bytes:
        """Генерирует уникальный ключ для каждого пользователя."""
        salt = f"tg_forward_bot_{user_id}".encode()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )

        key = base64.urlsafe_b64encode(kdf.derive(self.master_key))
        return key

    def encrypt(self, user_id: int, session_data: bytes) -> bytes:
        """Шифрует данные сессии."""
        key = self._derive_key(user_id)
        fernet = Fernet(key)
        return fernet.encrypt(session_data)

    def decrypt(self, user_id: int, encrypted_data: bytes) -> bytes:
        """Расшифровывает данные сессии."""
        key = self._derive_key(user_id)
        fernet = Fernet(key)
        return fernet.decrypt(encrypted_data)


# === Session Storage ===

class SecureSessionStorage:
    """Безопасное хранение сессий."""

    def __init__(
        self,
        db: Database,
        encryption: SessionEncryption,
    ):
        self.db = db
        self.encryption = encryption

    async def save_session(
        self,
        user_id: int,
        session_string: str,
    ) -> None:
        """Сохраняет зашифрованную сессию."""

        session_bytes = session_string.encode()
        encrypted = self.encryption.encrypt(user_id, session_bytes)
        session_hash = hashlib.sha256(session_bytes).hexdigest()

        await self.db.sessions.upsert(
            user_id=user_id,
            session_data=encrypted,
            session_hash=session_hash,
            is_valid=True,
            last_used_at=datetime.utcnow(),
        )

    async def load_session(self, user_id: int) -> str | None:
        """Загружает и расшифровывает сессию."""

        record = await self.db.sessions.get_by_user(user_id)
        if not record or not record.is_valid:
            return None

        try:
            decrypted = self.encryption.decrypt(user_id, record.session_data)
            return decrypted.decode()
        except Exception:
            # Сессия повреждена
            await self.invalidate_session(user_id)
            return None

    async def invalidate_session(self, user_id: int) -> None:
        """Помечает сессию как невалидную."""
        await self.db.sessions.update(
            user_id=user_id,
            is_valid=False,
        )
```

### 8.2 Безопасная обработка 2FA

```python
class SecureAuthHandler:
    """Обработка авторизации без сохранения паролей."""

    async def handle_2fa(self, user_id: int, password: str) -> AuthResult:
        """
        Обрабатывает 2FA без сохранения пароля.
        Пароль используется только в памяти для текущего запроса.
        """
        try:
            # Пароль передаётся напрямую в Pyrogram
            # и не сохраняется нигде
            result = await self.mtproto_client.check_password(password)

            # Очищаем пароль из памяти (best effort)
            # Python не гарантирует немедленную очистку
            del password

            return AuthResult(success=True)

        except PasswordHashInvalid:
            return AuthResult(
                success=False,
                error="INVALID_PASSWORD",
            )
        finally:
            # Убеждаемся, что пароль не остался в логах
            # Логгер не должен логировать этот метод с аргументами
            pass
```

### 8.3 Конфигурация и секреты

```python
# === config.py ===

from pydantic import BaseSettings, SecretStr


class Settings(BaseSettings):
    """Конфигурация приложения."""

    # Telegram Bot
    BOT_TOKEN: SecretStr

    # Telegram MTProto (для Pyrogram)
    API_ID: int
    API_HASH: SecretStr

    # Database
    DATABASE_URL: SecretStr

    # Session encryption
    SESSION_ENCRYPTION_KEY: SecretStr

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_SENSITIVE_DATA: bool = False  # НИКОГДА не True в production

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# === .env.example ===
"""
BOT_TOKEN=your_bot_token_here
API_ID=12345678
API_HASH=your_api_hash_here
DATABASE_URL=postgresql://user:pass@localhost/dbname
SESSION_ENCRYPTION_KEY=your_32_byte_key_base64_encoded
LOG_LEVEL=INFO
"""
```

### 8.4 Безопасное логирование

```python
import logging
import re


class SensitiveDataFilter(logging.Filter):
    """Фильтрует чувствительные данные из логов."""

    PATTERNS = [
        (r'\+\d{10,15}', '+XXX***XXX'),  # Телефоны
        (r'code["\s:=]+\d{5}', 'code=*****'),  # Коды
        (r'password["\s:=]+[^\s"]+', 'password=*****'),  # Пароли
        (r'session_string["\s:=]+[^\s"]+', 'session=*****'),  # Сессии
        (r'api_hash["\s:=]+[a-f0-9]+', 'api_hash=*****'),  # API hash
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, 'msg'):
            for pattern, replacement in self.PATTERNS:
                record.msg = re.sub(pattern, replacement, str(record.msg), flags=re.IGNORECASE)
        return True


# Настройка логгера
def setup_logging(settings: Settings) -> None:
    logger = logging.getLogger()
    logger.setLevel(settings.LOG_LEVEL)

    handler = logging.StreamHandler()
    handler.addFilter(SensitiveDataFilter())

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)

    logger.addHandler(handler)
```

### 8.5 Рекомендации по безопасности

| Область | Рекомендация | Приоритет |
|---------|--------------|-----------|
| Секреты | Использовать переменные окружения, никогда не коммитить | Critical |
| Сессии | AES-256 шифрование с уникальным ключом на пользователя | Critical |
| Пароли | Не хранить, использовать только в памяти | Critical |
| Логи | Фильтровать телефоны, коды, пароли | High |
| База данных | Шифрование at-rest для PostgreSQL | High |
| Сеть | HTTPS/TLS для всех соединений | High |
| Доступ | Принцип минимальных привилегий для сервисного аккаунта | Medium |
| Бэкапы | Шифрованные бэкапы, безопасное хранение | Medium |
| Ротация | Ротация master key раз в 90 дней | Medium |

---

## 9. Нефункциональные требования

### 9.1 Производительность

| Метрика | Требование | Комментарий |
|---------|------------|-------------|
| Latency (пересылка) | < 5 секунд от публикации | Real-time monitoring |
| Throughput | 100+ сообщений/минуту | На один инстанс |
| Concurrent users | 100+ пользователей | MVP |
| Message queue depth | < 1000 сообщений | Алерт при превышении |

### 9.2 Доступность

| Метрика | Требование |
|---------|------------|
| Uptime | 99.5% (месячный) |
| Recovery time | < 5 минут |
| Data durability | 99.99% |

### 9.3 Лимиты

| Лимит | Значение | Обоснование |
|-------|----------|-------------|
| Источников на пользователя | 50 | Telegram rate limits |
| Destinations на пользователя | 1 | MVP scope |
| Сообщений в очереди | 10,000 | Memory limits |
| Размер файла импорта | 1 MB | DoS prevention |
| Auth attempts | 3 per stage | Security |
| API requests/sec | 30 | Telegram limits |

### 9.4 Масштабирование

**Горизонтальное масштабирование:**

```mermaid
flowchart TB
    LB[Load Balancer]

    subgraph Workers["Worker Nodes"]
        W1[Worker 1]
        W2[Worker 2]
        W3[Worker N]
    end

    subgraph Shared["Shared Services"]
        DB[(PostgreSQL)]
        REDIS[(Redis)]
        QUEUE[Message Queue]
    end

    LB --> W1
    LB --> W2
    LB --> W3

    W1 --> DB
    W2 --> DB
    W3 --> DB

    W1 --> REDIS
    W2 --> REDIS
    W3 --> REDIS

    W1 --> QUEUE
    W2 --> QUEUE
    W3 --> QUEUE
```

**Стратегия:**
1. **User sharding:** Каждый воркер обслуживает подмножество пользователей
2. **Redis:** Distributed locking, rate limiting, session cache
3. **Message Queue (RabbitMQ/Redis Streams):** Асинхронная обработка

### 9.5 Мониторинг

```python
# === Метрики ===

from prometheus_client import Counter, Histogram, Gauge

# Counters
messages_forwarded = Counter(
    'bot_messages_forwarded_total',
    'Total forwarded messages',
    ['source_type', 'status']
)

auth_attempts = Counter(
    'bot_auth_attempts_total',
    'Authentication attempts',
    ['stage', 'result']
)

# Histograms
forward_latency = Histogram(
    'bot_forward_latency_seconds',
    'Message forwarding latency',
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
)

# Gauges
active_users = Gauge(
    'bot_active_users',
    'Currently active users'
)

queue_size = Gauge(
    'bot_queue_size',
    'Messages in forwarding queue'
)
```

**Алерты:**
- `queue_size > 1000` — Warning
- `forward_latency_p99 > 10s` — Warning
- `auth_failure_rate > 50%` — Critical
- `session_invalid_count > 10/hour` — Warning

---

## 10. План тестирования

### 10.1 Unit Tests

| Модуль | Тест-кейсы |
|--------|------------|
| `validators.py` | Валидация телефона, ссылок на каналы, форматов файлов |
| `message_type.py` | Определение типа для всех видов сообщений |
| `retry_strategy.py` | Расчёт задержек, условия ретрая |
| `encryption.py` | Шифрование/дешифрование сессий |
| `deduplication.py` | Логика дедупликации |

```python
# Пример unit test
import pytest
from src.shared.utils.validators import validate_phone, validate_channel_link


class TestValidatePhone:
    def test_valid_international_format(self):
        assert validate_phone("+79001234567") is True

    def test_valid_with_spaces(self):
        assert validate_phone("+7 900 123 45 67") is True

    def test_invalid_no_plus(self):
        assert validate_phone("79001234567") is False

    def test_invalid_too_short(self):
        assert validate_phone("+7900") is False


class TestValidateChannelLink:
    def test_valid_https_link(self):
        result = validate_channel_link("https://t.me/durov")
        assert result.is_valid
        assert result.username == "durov"

    def test_valid_username(self):
        result = validate_channel_link("@durov")
        assert result.is_valid

    def test_invalid_private_link(self):
        result = validate_channel_link("https://t.me/+abc123")
        assert not result.is_valid
```

### 10.2 Integration Tests

| Сценарий | Описание |
|----------|----------|
| Auth Flow | Полный цикл авторизации с mock Telegram |
| Source Management | CRUD операции с источниками |
| Message Forwarding | Пересылка разных типов контента |
| Session Recovery | Восстановление после expiry |
| Database Operations | Транзакции, constraints |

```python
# Пример integration test
import pytest
from src.services.source_service import SourceService


@pytest.mark.integration
class TestSourceService:
    async def test_add_sources_validates_subscription(
        self,
        source_service: SourceService,
        mock_mtproto_client,
        test_user,
    ):
        # Arrange
        mock_mtproto_client.get_dialogs.return_value = [
            MockDialog(username="subscribed_channel"),
        ]

        # Act
        result = await source_service.add_sources(
            user_id=test_user.id,
            links=["https://t.me/subscribed_channel", "https://t.me/not_subscribed"],
        )

        # Assert
        assert len(result.success) == 1
        assert len(result.errors) == 1
        assert "subscribed_channel" in result.success[0].username
        assert "not subscribed" in result.errors[0].reason
```

### 10.3 E2E Tests

| Сценарий | Шаги | Ожидаемый результат |
|----------|------|---------------------|
| Happy Path | /start → auth → add source → set destination → run | Сообщения пересылаются |
| Re-auth | Session expires → user gets notification → re-auth | Пересылка возобновляется |
| Error Handling | Source becomes unavailable → notification | Пользователь уведомлён |
| File Import | Upload .txt with 10 links | 10 источников добавлено |

### 10.4 Mapping: Requirements → Test Cases

| REQ | Test Cases |
|-----|------------|
| REQ-01 | `test_auth_creates_session`, `test_auth_stores_encrypted` |
| REQ-02 | `test_phone_validation_*` |
| REQ-03 | `test_2fa_prompt_when_enabled`, `test_2fa_validation` |
| REQ-04 | `test_code_verification`, `test_code_retry_limit` |
| REQ-05 | `test_session_encrypted`, `test_session_decrypt` |
| REQ-06 | `test_add_button_works`, `test_remove_button_works` |
| REQ-07 | `test_single_link`, `test_multiple_links`, `test_invalid_link` |
| REQ-08 | `test_txt_import`, `test_csv_import`, `test_large_file_rejection` |
| REQ-09 | `test_subscription_check`, `test_not_subscribed_error` |
| REQ-10 | `test_new_message_detected`, `test_latency_under_5s` |
| REQ-11 | `test_forward_to_destination`, `test_bot_admin_check` |
| REQ-12 | `test_forward_text`, `test_forward_photo`, `test_forward_video`, `test_forward_album` |
| REQ-13 | `test_forward_poll`, `test_forward_voice`, `test_forward_video_note` |
| REQ-14 | `test_user_notification_on_error`, `test_notification_throttling` |
| REQ-15 | `test_session_expired_triggers_reauth`, `test_reauth_flow` |

---

## 11. План реализации

### 11.1 Backlog (MVP)

#### Phase 1: Foundation (Приоритет: Critical)

| ID | Задача | Описание | Зависимости |
|----|--------|----------|-------------|
| T-001 | Project setup | Структура проекта, poetry, linting | — |
| T-002 | Database models | SQLAlchemy models, migrations | T-001 |
| T-003 | Config management | Pydantic settings, env loading | T-001 |
| T-004 | Logging setup | Structured logging, sensitive filter | T-001 |

#### Phase 2: Authentication (Приоритет: Critical)

| ID | Задача | Описание | Зависимости |
|----|--------|----------|-------------|
| T-005 | Bot handlers scaffold | python-telegram-bot setup | T-001 |
| T-006 | FSM states | State machine implementation | T-005 |
| T-007 | Pyrogram client | MTProto client wrapper | T-003 |
| T-008 | Auth flow - phone | Phone input, validation, send_code | T-006, T-007 |
| T-009 | Auth flow - code | Code verification | T-008 |
| T-010 | Auth flow - 2FA | Password verification | T-009 |
| T-011 | Session encryption | Encrypt/store/load sessions | T-002, T-010 |

#### Phase 3: Source Management (Приоритет: High)

| ID | Задача | Описание | Зависимости |
|----|--------|----------|-------------|
| T-012 | Source service | Business logic layer | T-002 |
| T-013 | Add source - text | Single/multiple links | T-012, T-007 |
| T-014 | Add source - file | .txt/.csv parsing | T-013 |
| T-015 | Remove source | Delete with confirmation | T-012 |
| T-016 | List sources | Pagination | T-012 |
| T-017 | Subscription validation | Check via MTProto | T-007, T-012 |

#### Phase 4: Destination & Forwarding (Приоритет: High)

| ID | Задача | Описание | Зависимости |
|----|--------|----------|-------------|
| T-018 | Destination service | Set/validate destination | T-002 |
| T-019 | Update listener | MTProto message handler | T-007 |
| T-020 | Forwarder service | Forward logic | T-019 |
| T-021 | Media group handling | Album collection | T-020 |
| T-022 | All content types | Polls, voice, etc. | T-020 |
| T-023 | Deduplication | Prevent duplicates | T-020 |
| T-024 | Delivery logging | Success/failure tracking | T-020 |

#### Phase 5: Error Handling & Polish (Приоритет: Medium)

| ID | Задача | Описание | Зависимости |
|----|--------|----------|-------------|
| T-025 | Retry mechanism | Exponential backoff | T-020 |
| T-026 | User notifications | Error alerts | T-005, T-024 |
| T-027 | Session monitor | Health check worker | T-011 |
| T-028 | Re-auth flow | Session recovery | T-027, T-008 |
| T-029 | Rate limiter | Telegram API compliance | T-020 |
| T-030 | Status command | Statistics display | T-024 |

### 11.2 Post-MVP Features

| Feature | Description | Priority |
|---------|-------------|----------|
| Multiple destinations | Несколько целевых каналов | Medium |
| Content filtering | Фильтры по ключевым словам | Medium |
| Scheduling | Отложенная публикация | Low |
| Content modification | Редактирование перед пересылкой | Low |
| Analytics dashboard | Web-интерфейс статистики | Low |
| Multi-tenancy | Несколько MTProto-аккаунтов | Low |

### 11.3 Риски

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Telegram API ban | Medium | Critical | Rate limiting, gradual rollout, ToS compliance |
| Session invalidation | Medium | High | Monitoring, quick re-auth flow |
| FloodWait limits | High | Medium | Queue, backoff, user notification |
| Data breach | Low | Critical | Encryption, access control, auditing |
| Library deprecation | Low | Medium | Abstract interfaces, easy replacement |

### 11.4 Допущения (итоговые)

1. Пользователи согласны на авторизацию под своим аккаунтом
2. Пользователи самостоятельно подписаны на каналы-доноры
3. Один destination в MVP достаточен
4. VPS с постоянным соединением доступен
5. Pyrogram стабильна для production use
6. Telegram не изменит API без предупреждения
7. 100 пользователей — достаточный масштаб для MVP
8. SQLite достаточен для MVP, миграция на PostgreSQL при масштабировании

---

## Приложения

### A. Выбор библиотек

| Библиотека | Назначение | Обоснование выбора |
|------------|------------|-------------------|
| **Pyrogram** | MTProto client | Активная поддержка, хорошая документация, async-first |
| **python-telegram-bot** | Bot API | Зрелая библиотека, ConversationHandler для FSM |
| **SQLAlchemy 2.0** | ORM | Type hints, async support |
| **Alembic** | Migrations | Стандарт для SQLAlchemy |
| **Pydantic** | Validation/Settings | Type safety, .env support |
| **cryptography** | Encryption | Industry standard, Fernet for simplicity |
| **pytest** | Testing | Fixtures, async support |
| **structlog** | Logging | Structured, filterable |

### B. Альтернативы и trade-offs

**Pyrogram vs Telethon:**
- Pyrogram: Лучше документация, более Pythonic API
- Telethon: Больше community, дольше существует
- **Решение:** Pyrogram для новых проектов

**SQLite vs PostgreSQL:**
- SQLite: Простота, zero-config, достаточно для MVP
- PostgreSQL: Масштабируемость, concurrent writes
- **Решение:** SQLite для MVP, миграция при необходимости

**python-telegram-bot vs aiogram:**
- PTB: Зрелая, много примеров
- aiogram: Более современная, native async
- **Решение:** PTB для стабильности, можно мигрировать позже

---

*Документ подготовлен: 2026-01-07*
*Версия: 1.0*
