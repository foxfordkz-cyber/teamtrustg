"""Modified RICE scoring and priority mapping."""

import logging
from typing import Any, Dict

from llm_adapter import LLMProvider
from parse_utils import parse_llm_number

logger = logging.getLogger("teamtrustgate")


# Единая шкала проекта: 0–400
PRIORITY_THRESHOLDS = [
    (250, "Highest"),
    (120, "High"),
    (50, "Medium"),
    (0, "Low"),
]

REACH_MAP = {
    "one_client": 1,
    "segment": 5,
    "all_clients": 10,
}

VALID_REACH_VALUES = (1.0, 5.0, 10.0)

RAW_MAX_SCORE = 1000.0
MAX_SCORE = 400.0

ENTERPRISE_REVENUE_THRESHOLD = 9.0


def _load_prompt(name: str) -> str:
    """
    Загружает промпт из папки prompts/.

    Поддерживает форматы .md и .txt.
    """
    for ext in ("md", "txt"):
        path = f"prompts/{name}.{ext}"

        try:
            with open(path, "r", encoding="utf-8") as file:
                content = file.read()
                logger.info("Промпт загружен: %s", path)
                return content

        except FileNotFoundError:
            continue

    raise FileNotFoundError(
        f"Промпт '{name}' не найден. "
        f"Ожидается prompts/{name}.md или prompts/{name}.txt"
    )


def _clamp(value: float, minimum: float, maximum: float) -> float:
    """Ограничивает число указанным диапазоном."""
    return max(minimum, min(value, maximum))


def _normalize_reach(value: float) -> float:
    """
    Приводит Reach к одному из допустимых значений: 1, 5 или 10.

    Это защищает от ответов LLM вроде 3, 7 или 20.
    """
    value = _clamp(value, 1.0, 10.0)

    return min(
        VALID_REACH_VALUES,
        key=lambda allowed_value: abs(allowed_value - value),
    )


def _map_priority(total_score: float) -> str:
    """Преобразует итоговый score 0–400 в Jira-приоритет."""
    for threshold, priority in PRIORITY_THRESHOLDS:
        if total_score >= threshold:
            return priority

    return "Low"


def _parse_boolean(value: Any) -> bool:
    """
    Безопасно преобразует значение в bool.

    Важно: обычный bool("false") возвращает True,
    поэтому строки необходимо обрабатывать отдельно.
    """
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in {
            "true",
            "1",
            "yes",
            "да",
        }

    if isinstance(value, (int, float)):
        return value != 0

    return False


def _is_enterprise_risk(analysis: dict) -> bool:
    """Возвращает нормализованный Enterprise-флаг."""
    return _parse_boolean(
        analysis.get("is_enterprise_risk", False)
    )


def _calculate_total_score(
    reach_score: float,
    impact_score: float,
    confidence_score: float,
    strategy_fit_score: float,
) -> float:
    """
    Рассчитывает итоговый score в диапазоне 0–400.

    Максимальный сырой результат:
    10 × 10 × 1.0 × 10 = 1000.

    Нормализованный результат:
    raw_score / 1000 × 400.
    """
    raw_score = (
        reach_score
        * impact_score
        * confidence_score
        * strategy_fit_score
    )

    normalized_score = (
        raw_score / RAW_MAX_SCORE
    ) * MAX_SCORE

    return _clamp(
        normalized_score,
        0.0,
        MAX_SCORE,
    )


class Scorer:
    def __init__(self, llm: LLMProvider):
        self.llm = llm
        self.prompt_template = _load_prompt("scoring")

    async def score(
        self,
        analysis: dict,
    ) -> Dict[str, Any]:
        """
        Выполняет scoring через LLM.

        При недоступности LLM используется локальный fallback.
        """
        try:
            llm_result = await self.llm.score(
                analysis,
                self.prompt_template,
            )

            return self._normalize(
                llm_result,
                analysis,
            )

        except Exception as error:
            logger.warning(
                "scorer: LLM недоступен, используется fallback. "
                "Причина: %s",
                error,
            )

            return self._fallback_score(analysis)

    def _normalize(
        self,
        result: dict,
        analysis: dict,
    ) -> Dict[str, Any]:
        """
        Нормализует оценки LLM.

        LLM определяет компоненты, но итоговый score
        всегда рассчитывается Python-кодом.
        """
        reach_score = parse_llm_number(
            result.get("reach_score", 1),
            default=1.0,
        )

        impact_score = parse_llm_number(
            result.get("impact_score", 1),
            default=1.0,
        )

        confidence_score = parse_llm_number(
            result.get("confidence_score", 0),
            default=0.0,
        )

        strategy_fit_score = parse_llm_number(
            result.get("strategy_fit_score", 1),
            default=1.0,
        )

        # Нормализация всех компонентов
        reach_score = _normalize_reach(reach_score)
        impact_score = _clamp(impact_score, 1.0, 10.0)
        confidence_score = _clamp(
            confidence_score,
            0.0,
            1.0,
        )
        strategy_fit_score = _clamp(
            strategy_fit_score,
            1.0,
            10.0,
        )

        revenue_at_risk = parse_llm_number(
            analysis.get("revenue_at_risk", 0),
            default=0.0,
        )
        revenue_at_risk = _clamp(
            revenue_at_risk,
            0.0,
            10.0,
        )

        # Enterprise-множитель
        if (
            _is_enterprise_risk(analysis)
            and revenue_at_risk
            >= ENTERPRISE_REVENUE_THRESHOLD
        ):
            reach_score = max(reach_score, 5.0)
            strategy_fit_score = 10.0

            logger.info(
                "scorer: применён Enterprise-множитель"
            )

        # Итог всегда рассчитывает Python
        total_score = _calculate_total_score(
            reach_score=reach_score,
            impact_score=impact_score,
            confidence_score=confidence_score,
            strategy_fit_score=strategy_fit_score,
        )

        priority = _map_priority(total_score)

        # Только для диагностики сравниваем с ответом LLM
        llm_total = parse_llm_number(
            result.get("total_score", 0),
            default=0.0,
        )

        if abs(llm_total - total_score) > 1:
            logger.info(
                "scorer: total_score LLM=%s проигнорирован, "
                "Python total_score=%s",
                llm_total,
                round(total_score, 1),
            )

        logger.info(
            "scorer: R=%s I=%s C=%s S=%s "
            "→ total=%s/400 priority=%s",
            reach_score,
            impact_score,
            confidence_score,
            strategy_fit_score,
            round(total_score, 1),
            priority,
        )

        return {
            "reach_score": reach_score,
            "impact_score": round(impact_score, 1),
            "confidence_score": round(
                confidence_score,
                2,
            ),
            "strategy_fit_score": round(
                strategy_fit_score,
                1,
            ),
            "total_score": round(total_score, 1),
            "priority": priority,
            "justification": str(
                result.get("justification", "")
            ),
        }

    def _fallback_score(
        self,
        analysis: dict,
    ) -> Dict[str, Any]:
        """
        Локальный Modified RICE без LLM.

        Используется, если все LLM-провайдеры недоступны.
        """
        reach_score = REACH_MAP.get(
            analysis.get("reach", "one_client"),
            1,
        )

        impact_score = parse_llm_number(
            analysis.get("revenue_at_risk", 5),
            default=5.0,
        )

        confidence_score = parse_llm_number(
            analysis.get("confidence", 0.5),
            default=0.5,
        )

        # Нейтральная оценка без LLM
        strategy_fit_score = 5.0

        reach_score = _normalize_reach(
            float(reach_score)
        )
        impact_score = _clamp(
            impact_score,
            1.0,
            10.0,
        )
        confidence_score = _clamp(
            confidence_score,
            0.0,
            1.0,
        )

        revenue_at_risk = parse_llm_number(
            analysis.get("revenue_at_risk", 0),
            default=0.0,
        )
        revenue_at_risk = _clamp(
            revenue_at_risk,
            0.0,
            10.0,
        )

        if (
            _is_enterprise_risk(analysis)
            and revenue_at_risk
            >= ENTERPRISE_REVENUE_THRESHOLD
        ):
            reach_score = max(reach_score, 5.0)
            strategy_fit_score = 10.0

            logger.info(
                "scorer fallback: применён Enterprise-множитель"
            )

        total_score = _calculate_total_score(
            reach_score=reach_score,
            impact_score=impact_score,
            confidence_score=confidence_score,
            strategy_fit_score=strategy_fit_score,
        )

        priority = _map_priority(total_score)

        logger.info(
            "scorer fallback: R=%s I=%s C=%s S=%s "
            "→ total=%s/400 priority=%s",
            reach_score,
            impact_score,
            confidence_score,
            strategy_fit_score,
            round(total_score, 1),
            priority,
        )

        return {
            "reach_score": reach_score,
            "impact_score": round(impact_score, 1),
            "confidence_score": round(
                confidence_score,
                2,
            ),
            "strategy_fit_score": strategy_fit_score,
            "total_score": round(total_score, 1),
            "priority": priority,
            "justification": (
                "Fallback scoring: LLM недоступен. "
                "Приоритет рассчитан локально на основе "
                "данных из extraction."
            ),
        }
