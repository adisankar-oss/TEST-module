from __future__ import annotations

import json
from pathlib import Path
from typing import Any


QUESTION_BANK_PATH = Path(__file__).resolve().parents[2] / "data" / "question_bank.json"


def get_fallback_question(
    *,
    job_id: str,
    topic: str,
    previous_questions: list[str],
) -> str:
    bank = _load_question_bank()
    role = _resolve_role(job_id)
    topic_questions = (
        bank.get(role, {}).get(topic)
        or bank.get("default", {}).get(topic)
        or bank.get("default", {}).get("behavioural")
        or ["Tell me about a challenge you handled and what you learned from it."]
    )

    normalized_previous = {
        _normalize(question)
        for question in previous_questions
        if _normalize(question)
    }
    for question in topic_questions:
        if _normalize(question) not in normalized_previous:
            return question
    return topic_questions[0]


def _load_question_bank() -> dict[str, Any]:
    with QUESTION_BANK_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _resolve_role(job_id: str) -> str:
    normalized = (job_id or "").strip().lower()
    if any(token in normalized for token in ("backend", "api", "python", "java", "service")):
        return "backend"
    if any(token in normalized for token in ("frontend", "react", "ui", "web")):
        return "frontend"
    if any(token in normalized for token in ("data", "etl", "analytics", "ml")):
        return "data"
    return "default"


def _normalize(value: str) -> str:
    return " ".join((value or "").strip().lower().split())
