from __future__ import annotations

from typing import Any


def realtime_evaluation_fallback(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = metadata or {}
    return {
        "score": 5,
        "strengths": [],
        "weaknesses": ["needs_more_specificity", "needs_more_depth"],
        "followup_required": True,
        "followup_reason": "INCOMPLETE_EXPLANATION",
        "confidence_score": 0.25,
        "decision": "followup",
        "feedback": "The answer was relevant but did not provide enough concrete depth.",
        "semantic_topic": str(metadata.get("semantic_topic") or "general"),
    }


def followup_generation_fallback(metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata or {}
    contradiction = str(metadata.get("contradiction") or "").strip()
    if contradiction:
        return f"{contradiction.rstrip('.')} Can you clarify?"

    reason = str(metadata.get("followup_reason") or "").upper()
    anchor = str(metadata.get("anchor") or metadata.get("semantic_topic") or "that approach").strip()
    if reason == "MISSING_TRADEOFFS":
        return f"You mentioned {anchor}. What trade-offs did you weigh before choosing that approach?"
    if reason == "NO_EXAMPLE":
        return f"You mentioned {anchor}. Can you walk me through a concrete example from your experience?"
    if reason == "CONTRADICTION":
        return f"You described {anchor}, but parts of the explanation do not line up. Can you clarify what actually happened?"
    return f"You mentioned {anchor}. What specific decision or outcome best shows how you handled it?"
