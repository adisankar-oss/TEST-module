from __future__ import annotations

import json
from typing import Any

from backend.services.llm.providers.gemini_fallbacks import question_generation_fallback


def parse_question_generation(content: str, metadata: dict[str, Any] | None = None) -> dict[str, str]:
    metadata = metadata or {}
    payload = _extract_json_object(content)
    fallback = question_generation_fallback(metadata)
    if payload is None:
        return fallback

    question = " ".join(str(payload.get("question") or "").strip().split()) or fallback["question"]
    if not question.endswith("?"):
        question = f"{question.rstrip('.')}?"

    return {
        "question": question.replace(" ?", "?"),
        "type": str(payload.get("type") or fallback["type"]).strip().lower() or fallback["type"],
        "topic": str(payload.get("topic") or fallback["topic"]).strip() or fallback["topic"],
        "reasoning": str(payload.get("reasoning") or fallback["reasoning"]).strip() or fallback["reasoning"],
    }


def parse_summary_text(content: str) -> str:
    return " ".join(str(content or "").strip().split())


def _extract_json_object(content: str) -> dict[str, Any] | None:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        payload = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
