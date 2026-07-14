"""Configuration and environment validation."""

import logging
import os

from dotenv import load_dotenv


load_dotenv()

logger = logging.getLogger("teamtrustgate")


class Config:
    # ── Telegram ──────────────────────────────────────────────────────────
    TELEGRAM_TOKEN: str = os.getenv(
        "TELEGRAM_TOKEN",
        "",
    )

    # ── LLM провайдеры ────────────────────────────────────────────────────
    LLM_PROVIDER: str = os.getenv(
        "LLM_PROVIDER",
        "gemini",
    )

    LLM_API_KEY: str = os.getenv(
        "LLM_API_KEY",
        "",
    )

    DEEPSEEK_API_KEY: str = os.getenv(
        "DEEPSEEK_API_KEY",
        "",
    )

    GEMINI_MODEL: str = os.getenv(
        "GEMINI_MODEL",
        "gemini-2.5-flash",
    )

    DEEPSEEK_MODEL: str = os.getenv(
        "DEEPSEEK_MODEL",
        "deepseek-chat",
    )

    # ── Jira ──────────────────────────────────────────────────────────────
    JIRA_URL: str = os.getenv(
        "JIRA_URL",
        "",
    ).rstrip("/")

    JIRA_EMAIL: str = os.getenv(
        "JIRA_EMAIL",
        "",
    )

    JIRA_API_TOKEN: str = os.getenv(
        "JIRA_API_TOKEN",
        "",
    )

    JIRA_PROJECT_KEY: str = os.getenv(
        "JIRA_PROJECT_KEY",
        "",
    )

    # ── Продуктовый контекст ──────────────────────────────────────────────
    PRODUCT_STRATEGY: str = os.getenv(
        "PRODUCT_STRATEGY",
        "",
    )

    # ── Доступ и роли ─────────────────────────────────────────────────────
    # Сейлзы могут создавать тикеты и работать только
    # со своими тикетами.
    ALLOWED_USERNAMES: list[str] = [
        username.strip().lstrip("@").lower()
        for username in os.getenv(
            "ALLOWED_USERNAMES",
            "",
        ).split(",")
        if username.strip()
    ]

    # Администраторы имеют полный доступ:
    # удаление, смена статуса, статистика и чужие тикеты.
    ADMIN_USERNAMES: list[str] = [
        username.strip().lstrip("@").lower()
        for username in os.getenv(
            "ADMIN_USERNAMES",
            "",
        ).split(",")
        if username.strip()
    ]

    # ── База данных ───────────────────────────────────────────────────────
    DB_PATH: str = os.getenv(
        "DB_PATH",
        "teamtrustgate.db",
    )

    # ── Логика агента ─────────────────────────────────────────────────────
    MAX_CLARIFICATION_ROUNDS: int = int(
        os.getenv(
            "MAX_CLARIFICATION_ROUNDS",
            "3",
        )
    )

    CONFIDENCE_THRESHOLD: float = float(
        os.getenv(
            "CONFIDENCE_THRESHOLD",
            "0.7",
        )
    )

    # За сколько последних дней искать похожие тикеты.
    DEDUP_DAYS: int = int(
        os.getenv(
            "DEDUP_DAYS",
            "90",
        )
    )

    # Максимальное количество Jira-тикетов,
    # проверяемых при дедупликации.
    DEDUP_MAX_RESULTS: int = int(
        os.getenv(
            "DEDUP_MAX_RESULTS",
            "200",
        )
    )

    # ── Таймауты ──────────────────────────────────────────────────────────
    JIRA_TIMEOUT: int = int(
        os.getenv(
            "JIRA_TIMEOUT",
            "10",
        )
    )

    LLM_TIMEOUT: int = int(
        os.getenv(
            "LLM_TIMEOUT",
            "30",
        )
    )

    # ── Логирование ───────────────────────────────────────────────────────
    LOG_LEVEL: str = os.getenv(
        "LOG_LEVEL",
        "INFO",
    ).upper()

    @classmethod
    def validate(cls) -> None:
        required = [
            (
                "TELEGRAM_TOKEN",
                cls.TELEGRAM_TOKEN,
            ),
            (
                "LLM_API_KEY",
                cls.LLM_API_KEY,
            ),
            (
                "JIRA_URL",
                cls.JIRA_URL,
            ),
            (
                "JIRA_EMAIL",
                cls.JIRA_EMAIL,
            ),
            (
                "JIRA_API_TOKEN",
                cls.JIRA_API_TOKEN,
            ),
            (
                "JIRA_PROJECT_KEY",
                cls.JIRA_PROJECT_KEY,
            ),
        ]

        missing = [
            name
            for name, value in required
            if not value
        ]

        if (
            cls.LLM_PROVIDER.lower() == "deepseek"
            and not cls.DEEPSEEK_API_KEY
            and not cls.LLM_API_KEY
        ):
            missing.append(
                "DEEPSEEK_API_KEY или LLM_API_KEY"
            )

        if missing:
            raise ValueError(
                "❌ Отсутствуют обязательные "
                "переменные окружения: "
                f"{', '.join(missing)}\n"
                "Заполни их в Railway → Variables."
            )

        # ── Проверка числовых настроек ────────────────────────────────────
        if cls.MAX_CLARIFICATION_ROUNDS < 1:
            raise ValueError(
                "MAX_CLARIFICATION_ROUNDS "
                "должен быть больше 0."
            )

        if not 0 <= cls.CONFIDENCE_THRESHOLD <= 1:
            raise ValueError(
                "CONFIDENCE_THRESHOLD "
                "должен находиться в диапазоне от 0 до 1."
            )

        if cls.DEDUP_DAYS < 1:
            raise ValueError(
                "DEDUP_DAYS должен быть больше 0."
            )

        if cls.DEDUP_MAX_RESULTS < 1:
            raise ValueError(
                "DEDUP_MAX_RESULTS должен быть больше 0."
            )

        if cls.DEDUP_MAX_RESULTS > 500:
            raise ValueError(
                "DEDUP_MAX_RESULTS не должен превышать 500."
            )

        if cls.JIRA_TIMEOUT < 1:
            raise ValueError(
                "JIRA_TIMEOUT должен быть больше 0."
            )

        if cls.LLM_TIMEOUT < 1:
            raise ValueError(
                "LLM_TIMEOUT должен быть больше 0."
            )

        # ── Предупреждения ────────────────────────────────────────────────
        if not cls.PRODUCT_STRATEGY:
            logger.warning(
                "⚠️ PRODUCT_STRATEGY не задана — "
                "скоринг стратегического соответствия "
                "будет неточным"
            )

        if not cls.ALLOWED_USERNAMES:
            logger.warning(
                "⚠️ ALLOWED_USERNAMES не задан — "
                "бот доступен всем пользователям Telegram"
            )

        if not cls.ADMIN_USERNAMES:
            logger.warning(
                "⚠️ ADMIN_USERNAMES не задан — "
                "никто не имеет админ-прав "
                "(/stats, управление чужими тикетами)"
            )

        # ── Информация при запуске ────────────────────────────────────────
        logger.info(
            "✅ Конфигурация прошла валидацию"
        )

        logger.info(
            "   LLM_PROVIDER     = %s",
            cls.LLM_PROVIDER,
        )

        logger.info(
            "   GEMINI_MODEL     = %s",
            cls.GEMINI_MODEL,
        )

        logger.info(
            "   DEEPSEEK_MODEL   = %s",
            cls.DEEPSEEK_MODEL,
        )

        logger.info(
            "   JIRA_PROJECT     = %s",
            cls.JIRA_PROJECT_KEY,
        )

        logger.info(
            "   DEDUP_DAYS       = %s",
            cls.DEDUP_DAYS,
        )

        logger.info(
            "   DEDUP_MAX_RESULTS = %s",
            cls.DEDUP_MAX_RESULTS,
        )

        logger.info(
            "   SALES_USERS      = %s",
            len(cls.ALLOWED_USERNAMES),
        )

        logger.info(
            "   ADMIN_USERS      = %s",
            len(cls.ADMIN_USERNAMES),
        )


CONFIG = Config()
