# TeamTrustGate 🛡️

> ИИ-агент для автоматической обработки клиентских запросов из Telegram в Jira.  
> Telegram → LLM → дедупликация → скоринг → подтверждение → Jira.

**Стек:** Python 3.11 · python-telegram-bot · Gemini / DeepSeek / OpenAI / Anthropic · Jira REST API v2 + v3 · SQLite · matplotlib · Railway

---

## О проекте

TeamTrustGate помогает сейлзам передавать запросы клиентов продуктовой команде без ручного заполнения Jira.

Сейлз пишет сообщение обычным языком, а бот:

1. анализирует запрос через LLM;
2. извлекает проблему, клиента, охват, бизнес-риск и дедлайн;
3. задаёт уточняющие вопросы, если данных недостаточно;
4. проверяет Jira на похожие открытые тикеты;
5. рассчитывает приоритет по нормализованной RICE-модели;
6. показывает превью перед созданием;
7. после подтверждения создаёт структурированный тикет в Jira;
8. уведомляет пользователя при изменении статуса;
9. отправляет администраторам SLA-алерты.

---

## Основные возможности

### Анализ запроса

- приём запросов через Telegram обычным текстом;
- извлечение структурированных данных через LLM;
- поддержка абсолютных и относительных дедлайнов;
- уточняющие вопросы при `confidence < 0.7`;
- максимум 3 раунда уточнений по умолчанию;
- фильтрация нерелевантных и спам-запросов;
- безопасный fallback при недоступности LLM.

### Дедупликация

- поиск недавних открытых тикетов через Jira JQL;
- семантическое сравнение через LLM;
- обработка кандидатов батчами по 15 тикетов;
- проверка возвращённых LLM ключей по реальному списку Jira;
- keyword fallback через Jaccard similarity;
- при нескольких совпадениях возвращается самый новый дубликат;
- при найденном дубликате новый тикет не создаётся.

### Скоринг

Используется нормализованная модель:

```text
raw_score = Reach × Impact × Confidence × Strategy Fit
total_score = raw_score / 1000 × 400
```

Диапазоны компонентов:

| Компонент | Диапазон |
|---|---:|
| Reach | 1 / 5 / 10 |
| Impact | 1–10 |
| Confidence | 0.0–1.0 |
| Strategy Fit | 1–10 |
| Итоговый score | 0–400 |

Приоритеты:

| Score | Приоритет |
|---:|---|
| `>= 250` | 🔴 Highest |
| `120–249` | 🟠 High |
| `50–119` | 🟡 Medium |
| `< 50` | 🟢 Low |

Итоговый score всегда рассчитывается Python-кодом. Значение `total_score`, возвращённое LLM, используется только для диагностики.

### Enterprise-правило

Enterprise-множитель применяется, только когда одновременно выполняются условия:

```text
is_enterprise_risk = true
revenue_at_risk >= 9
```

Тогда:

```text
reach_score = минимум 5
strategy_fit_score = 10
```

### Telegram UX

- превью запроса перед созданием тикета;
- кнопки «Создать», «Уточнить» и «Отменить»;
- новый текст не перезаписывает неподтверждённое превью;
- добавление комментария к Jira-тикету из Telegram;
- просмотр статуса;
- уведомления при изменении статуса;
- корректное экранирование динамического Markdown;
- разные права и команды для сейлзов и администраторов.

### Jira

- создание Story-тикетов;
- приоритеты, labels и Due Date;
- управление статусами из Telegram;
- удаление с подтверждением;
- Jira API v3 для JQL-поиска;
- Jira API v2 для операций с тикетами;
- пагинация поиска через `nextPageToken`;
- retry и circuit breaker.

### Аналитика

- `/stats` за последние 30 дней;
- распределение по приоритетам и статусам;
- топ пользователей за последние 30 дней;
- PNG-график через matplotlib;
- генерация графика и чтение SQLite вынесены из async event loop;
- `/export [дней]` создаёт CSV для Excel и Google Sheets.

### SLA

Раз в сутки бот проверяет открытые тикеты:

- Highest/High находится в To Do дольше заданного срока;
- дедлайн приближается, а работа ещё не начата.

Каждый тип алерта отправляется один раз. Для получения SLA-алертов администратор должен хотя бы один раз выполнить `/start`, чтобы бот сохранил его Telegram `chat_id`.

---

## Архитектура

```text
┌───────────────────────┐
│       Telegram        │
│   сейлз / администратор
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│        bot.py         │
│ handlers / UX / jobs  │
└──────┬────┬────┬──────┘
       │    │    │
       │    │    └──────────────► state_manager.py
       │    │                     SQLite: sessions,
       │    │                     tracking, alerts, stats
       │    │
       │    └───────────────────► deduplicator.py
       │                          LLM batches + fallback
       │
       ├────────────────────────► llm_adapter.py
       │                          providers + retry + failover
       │
       ├────────────────────────► scorer.py
       │                          score 0–400
       │
       └────────────────────────► jira_client.py
                                  Jira v2/v3 + pagination
```

---

## Обработка запроса

```text
1. Пользователь отправляет сообщение
                │
                ▼
2. LLM извлекает структурированные данные
                │
                ├── confidence < 0.7
                │       └── уточняющий вопрос
                │
                ▼
3. Проверка на дубликаты в Jira
                │
                ├── дубликат найден
                │       └── показать существующий тикет
                │
                ▼
4. Расчёт score 0–400
                │
                ▼
5. Превью в Telegram
                │
        ┌───────┼────────┐
        ▼       ▼        ▼
     Создать  Уточнить  Отменить
        │
        ▼
6. Создание тикета в Jira
                │
                ▼
7. Tracking статуса и SLA-проверки
```

---

## Роли

### Сейлз — `ALLOWED_USERNAMES`

Может:

- создавать запросы;
- подтверждать и уточнять превью;
- смотреть свои последние тикеты;
- проверять статус;
- добавлять комментарии;
- получать уведомления об изменении статуса.

Не может:

- управлять статусами;
- удалять тикеты;
- смотреть общую аналитику;
- экспортировать все тикеты.

### Администратор — `ADMIN_USERNAMES`

Может всё, что доступно сейлзу, а также:

- смотреть последние тикеты всего проекта;
- менять статус;
- удалять тикеты;
- использовать `/stats`;
- использовать `/export`;
- получать SLA-алерты.

Username указываются без `@`, через запятую. Сравнение выполняется без учёта регистра.

---

## Команды

### Общие

| Команда | Описание |
|---|---|
| `/start` | Регистрация чата, приветствие и определение роли |
| `/help` | Инструкция и примеры |
| `/cancel` | Отмена текущей сессии |
| `/status TT-42` | Проверка статуса тикета |
| `/list` | Сейлзу — свои тикеты, админу — последние тикеты проекта |

### Только администратору

| Команда | Описание |
|---|---|
| `/delete TT-42` | Удаление тикета с подтверждением |
| `/stats` | Аналитика за последние 30 дней |
| `/export [дней]` | CSV-экспорт, по умолчанию за 90 дней |
| `/debug` | Проверка конфигурации и доступности Jira |

---

## Структура проекта

```text
teamtrustgate/
├── bot.py
├── config.py
├── deduplicator.py
├── jira_client.py
├── llm_adapter.py
├── parse_utils.py
├── scorer.py
├── state_manager.py
├── requirements.txt
├── runtime.txt
├── Procfile
├── README.md
├── .env.example
└── prompts/
    ├── extraction.md
    ├── scoring.md
    ├── dedup.md
    └── dedup_batch.md
```

`runtime.txt` и `Procfile` должны находиться в корне проекта, а не внутри `prompts/`.

---

## Промпты

Бот ищет промпты в папке `prompts/` сначала с расширением `.md`, затем `.txt`.

| Файл | Назначение |
|---|---|
| `extraction.md` | Анализ сообщения и извлечение данных |
| `scoring.md` | Оценка компонентов скоринга |
| `dedup_batch.md` | Сравнение запроса с батчем Jira-кандидатов |
| `dedup.md` | Одиночное сравнение, если используется |

В `extraction.md` подставляется `{TODAY}`, чтобы LLM правильно интерпретировал выражения вроде «через неделю» или «до конца месяца».

---

## Установка

### Требования

- Python 3.11;
- Telegram Bot Token;
- Jira Cloud;
- API-ключ хотя бы одного поддерживаемого LLM.

### Клонирование

```bash
git clone https://github.com/karabekdamir428/teamtrustgate0
cd teamtrustgate0
```

### Виртуальное окружение

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### Зависимости

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### `.env`

Linux/macOS:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Заполни переменные и запусти:

```bash
python bot.py
```

---

## Переменные окружения

### Обязательные

| Переменная | Описание |
|---|---|
| `TELEGRAM_TOKEN` | Токен Telegram-бота |
| `LLM_PROVIDER` | Основной провайдер LLM |
| `LLM_API_KEY` | API-ключ основного провайдера |
| `JIRA_URL` | Например `https://company.atlassian.net` |
| `JIRA_EMAIL` | Email Jira-аккаунта |
| `JIRA_API_TOKEN` | Jira API token |
| `JIRA_PROJECT_KEY` | Например `TT` |

### Дополнительные

| Переменная | Значение по умолчанию | Описание |
|---|---:|---|
| `DEEPSEEK_API_KEY` | пусто | Резервный DeepSeek |
| `GEMINI_MODEL` | зависит от config | Модель Gemini |
| `DEEPSEEK_MODEL` | зависит от config | Модель DeepSeek |
| `ALLOWED_USERNAMES` | пусто | Сейлзы через запятую |
| `ADMIN_USERNAMES` | пусто | Администраторы через запятую |
| `PRODUCT_STRATEGY` | пусто | Контекст продуктовой стратегии |
| `CONFIDENCE_THRESHOLD` | `0.7` | Порог для уточнения |
| `DEDUP_DAYS` | `90` | Период поиска дубликатов |
| `MAX_CLARIFICATION_ROUNDS` | `3` | Максимум уточнений |
| `JIRA_TIMEOUT` | `10` | Таймаут Jira |
| `LLM_TIMEOUT` | `30` | Таймаут LLM |
| `LOG_LEVEL` | `INFO` | Уровень логов |
| `DB_PATH` | `teamtrustgate.db` | Путь к SQLite |

Пример:

```env
TELEGRAM_TOKEN=your_telegram_token

LLM_PROVIDER=gemini
LLM_API_KEY=your_gemini_api_key
DEEPSEEK_API_KEY=your_deepseek_api_key
GEMINI_MODEL=gemini-2.5-flash
DEEPSEEK_MODEL=deepseek-chat

JIRA_URL=https://your-company.atlassian.net
JIRA_EMAIL=your-email@example.com
JIRA_API_TOKEN=your_jira_api_token
JIRA_PROJECT_KEY=TT

ALLOWED_USERNAMES=sales_one,sales_two
ADMIN_USERNAMES=product_manager
PRODUCT_STRATEGY=Удержание enterprise клиентов и рост конверсии

CONFIDENCE_THRESHOLD=0.7
DEDUP_DAYS=90
MAX_CLARIFICATION_ROUNDS=3
JIRA_TIMEOUT=10
LLM_TIMEOUT=30
LOG_LEVEL=INFO
DB_PATH=teamtrustgate.db
```

Не добавляй `.env`, Jira token или LLM API keys в Git.

---

## LLM-провайдеры

Поддерживаются:

```text
gemini
deepseek
openai
anthropic
```

При основном провайдере Gemini и заполненном `DEEPSEEK_API_KEY` создаётся резервная цепочка:

```text
Gemini → DeepSeek
```

Для временных ошибок используются:

- до 3 попыток;
- экспоненциальная задержка;
- retry для timeout, 429, 503 и перегрузки;
- немедленная остановка для 400, 401 и 403.

---

## Jira API

### Поиск

JQL-поиск выполняется через:

```text
POST /rest/api/3/search/jql
```

Параметры передаются JSON-телом:

```json
{
  "jql": "project=TT ORDER BY created DESC",
  "fields": ["summary", "status", "priority"],
  "maxResults": 100
}
```

Для нескольких страниц используется `nextPageToken`. Метод `_search()` собирает результаты, пока:

- не получено требуемое количество;
- `isLast` не равен `true`;
- Jira не перестала возвращать токен;
- не сработала защита от повторного токена.

### Работа с тикетами

Создание, комментарии, переходы и удаление выполняются через Jira REST API v2.

### Circuit breaker

После 5 подряд серверных ошибок circuit breaker блокирует новые Jira-запросы на 5 минут.

---

## SQLite

По умолчанию используется файл:

```text
teamtrustgate.db
```

Основные таблицы:

| Таблица | Назначение |
|---|---|
| `sessions` | Текущие диалоги и превью |
| `failed_requests` | Запросы, завершившиеся ошибкой |
| `tracked_tickets` | Владельцы и отслеживание статусов |
| `sent_alerts` | Защита SLA-уведомлений от повторов |
| `admin_chats` | Telegram chat ID администраторов |

Для production рекомендуется подключить persistent volume, иначе SQLite-файл может потеряться после пересоздания контейнера.

---

## Фоновые задачи

### Проверка статусов

```text
каждые 5 минут
```

Бот проверяет отслеживаемые тикеты и уведомляет создателя при изменении статуса.

### SLA

```text
раз в сутки
```

Настройки находятся в `bot.py`:

| Константа | Значение | Назначение |
|---|---:|---|
| `SLA_CHECK_INTERVAL` | `86400` | Интервал проверки |
| `SLA_STALE_DAYS` | `3` | Дни без движения |
| `SLA_DEADLINE_WARN_DAYS` | `2` | Предупреждение до дедлайна |

После добавления username в `ADMIN_USERNAMES` администратор должен выполнить `/start`.

---

## Деплой на Railway

### Файлы в корне

`Procfile`:

```text
worker: python bot.py
```

`runtime.txt`:

```text
python-3.11.9
```

Telegram-бот не принимает HTTP-трафик, поэтому процесс запускается как `worker`, а не `web`.

### Шаги

1. Создай новый проект в Railway.
2. Подключи GitHub-репозиторий.
3. Добавь переменные окружения.
4. Добавь persistent volume для SQLite.
5. Выполни deploy.
6. После запуска отправь боту `/start`.
7. Каждый администратор также должен выполнить `/start`.

---

## Проверка перед деплоем

Проверка синтаксиса:

```bash
python -m py_compile \
  bot.py \
  config.py \
  deduplicator.py \
  jira_client.py \
  llm_adapter.py \
  parse_utils.py \
  scorer.py \
  state_manager.py
```

Windows PowerShell:

```powershell
python -m py_compile bot.py config.py deduplicator.py jira_client.py llm_adapter.py parse_utils.py scorer.py state_manager.py
```

Запуск:

```bash
python bot.py
```

Минимальная ручная проверка:

1. `/start`;
2. отправить новый запрос;
3. дождаться превью;
4. отправить ещё один текст и проверить защиту превью;
5. нажать «Уточнить»;
6. создать тикет;
7. `/list` от имени сейлза;
8. `/list` от имени администратора;
9. `/status KEY-1`;
10. добавить комментарий;
11. `/stats`;
12. `/export 30`;
13. `/debug`;
14. проверить SLA-регистрацию администратора.

---

## Возможные проблемы

### Администратор не получает SLA-алерты

Проверь:

1. username присутствует в `ADMIN_USERNAMES`;
2. username указан без `@`;
3. администратор выполнил `/start`;
4. Railway использует persistent volume;
5. в логах нет сообщения `нет известных админов`.

### `/list` показывает чужие тикеты сейлзу

Сейлз должен получать ключи из SQLite через `get_user_ticket_keys()`, а затем Jira должна загружать только эти ключи через `search_issues_by_keys()`.

### Telegram сообщает об ошибке Markdown

Динамический текст должен проходить через `_escape_md()` с `telegram.helpers.escape_markdown(..., version=1)`.

### Jira отвечает HTTP 410

Не используй старый:

```text
/rest/api/2/search
```

Используй:

```text
/rest/api/3/search/jql
```

### Статистика неполная

Проверь поддержку `nextPageToken` в `_search()` и лимит, передаваемый в `get_project_stats()` или `export_issues()`.

---

## Безопасность

- секреты хранятся только в переменных окружения;
- username нормализуются перед проверкой прав;
- действия администратора повторно проверяются при обработке callback;
- LLM-ключи дедупликации сверяются с реальными Jira-кандидатами;
- итоговый score не принимается от LLM без проверки;
- динамический Telegram Markdown экранируется;
- неподтверждённое превью не перезаписывается новым сообщением;
- постоянные ошибки API не отправляются в бесконечный retry.

---

## Лицензия

MIT
