from __future__ import annotations

from enum import Enum
from typing import Any


class Decision(str, Enum):
    FOLLOWUP = "FOLLOWUP"
    NEXT = "NEXT"
    HARDER = "HARDER"
    WRAPPING = "WRAPPING"


CONFIG_KEY_MAX_QUESTIONS = "max_questions"
CONFIG_KEY_FOLLOWUP_SCORE_MAX = "followup_score_max"
CONFIG_KEY_NEXT_SCORE_MIN = "next_score_min"

DEFAULT_FOLLOWUP_SCORE_MAX = 4
DEFAULT_NEXT_SCORE_MIN = 8


def _get_config_value(config: dict[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    if not isinstance(value, int):
        raise ValueError(f"Config key '{key}' must be an integer")
    return value


def decide_next_action(score: int, question_number: int, config: dict[str, Any]) -> str:
    max_questions = _get_config_value(config, CONFIG_KEY_MAX_QUESTIONS, 1)
    followup_score_max = _get_config_value(
        config,
        CONFIG_KEY_FOLLOWUP_SCORE_MAX,
        DEFAULT_FOLLOWUP_SCORE_MAX,
    )
    next_score_min = _get_config_value(
        config,
        CONFIG_KEY_NEXT_SCORE_MIN,
        DEFAULT_NEXT_SCORE_MIN,
    )

    if max_questions < 1:
        raise ValueError("Config key 'max_questions' must be greater than 0")
    if followup_score_max >= next_score_min:
        raise ValueError(
            "Config threshold 'followup_score_max' must be lower than 'next_score_min'"
        )

    if score <= followup_score_max:
        return Decision.FOLLOWUP.value
    if question_number >= max_questions:
        return Decision.WRAPPING.value
    if score >= next_score_min:
        return Decision.HARDER.value
    return Decision.NEXT.value
