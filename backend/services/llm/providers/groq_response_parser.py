from __future__ import annotations

import json
from typing import Any

from backend.services.llm.providers.groq_fallbacks import realtime_evaluation_fallback


def parse_realtime_evaluation(content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = metadata or {}
    payload = _extract_json_object(content)
    if payload is None:
        return realtime_evaluation_fallback(metadata)

    fallback = realtime_evaluation_fallback(metadata)
    strengths = payload.get("strengths")
    weaknesses = payload.get("weaknesses")
    return {
        "score": _coerce_int(payload.get("score"), fallback["score"], minimum=0, maximum=10),
        "strengths": strengths if isinstance(strengths, list) else fallback["strengths"],
        "weaknesses": weaknesses if isinstance(weaknesses, list) else fallback["weaknesses"],
        "followup_required": bool(payload.get("followup_required", fallback["followup_required"])),
        "followup_reason": str(payload.get("followup_reason") or fallback["followup_reason"]).strip(),
        "confidence_score": _coerce_float(payload.get("confidence_score"), fallback["confidence_score"], minimum=0.0, maximum=1.0),
        "decision": str(payload.get("decision") or fallback["decision"]).strip().lower() or fallback["decision"],
        "feedback": str(payload.get("feedback") or fallback["feedback"]).strip() or fallback["feedback"],
        "semantic_topic": str(payload.get("semantic_topic") or fallback["semantic_topic"]).strip() or fallback["semantic_topic"],
    }


def parse_followup_text(content: str) -> str:
    cleaned = " ".join(str(content or "").strip().split())
    if not cleaned:
        return ""
    if not cleaned.endswith("?"):
        cleaned = f"{cleaned.rstrip('.')}?"
    return cleaned.replace(" ?", "?")


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


def _coerce_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _coerce_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))
