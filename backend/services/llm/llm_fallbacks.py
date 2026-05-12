from __future__ import annotations

import json
from typing import Any


def fallback_provider_sequence(task_type: str, primary_provider: str) -> list[str]:
    primary = (primary_provider or "").strip().lower()
    if primary == "gemini":
        return ["groq"]
    if primary == "groq":
        return ["gemini"]
    if task_type == "question_generation":
        return ["groq"]
    return ["groq", "gemini"]


def deterministic_fallback(
    *,
    task_type: str,
    fallback_text: str = "",
    metadata: dict[str, Any] | None = None,
    response_format: str = "text",
) -> str:
    metadata = metadata or {}
    if fallback_text.strip():
        return fallback_text.strip()

    if task_type == "question_generation":
        question = (
            str(metadata.get("fallback_question") or "").strip()
            or "Tell me about a challenging system you worked on and the trade-offs you had to make?"
        )
        payload = {
            "question": question,
            "type": str(metadata.get("question_type") or "new"),
            "topic": str(metadata.get("topic") or "technical_skills"),
            "reasoning": "deterministic_fallback",
        }
        return json.dumps(payload) if response_format == "json" else question

    if task_type == "followup_generation":
        return (
            str(metadata.get("fallback_question") or "").strip()
            or "Could you walk me through a specific example from your experience?"
        )

    if task_type == "realtime_evaluation":
        payload = {
            "relevance_score": 10,
            "depth_score": 8,
            "technical_score": 8,
            "communication_score": 8,
            "red_flags": [],
            "brief_feedback": "The answer needs more specificity, technical depth, and clearer reasoning.",
            "needs_followup": True,
            "followup_reason": "INCOMPLETE_EXPLANATION",
            "followup_required": True,
            "followup_priority": "HIGH",
            "missing_dimensions": ["depth", "specificity"],
            "followup_type": "clarification_probe",
            "semantic_topic": str(metadata.get("semantic_topic") or "general"),
        }
        return json.dumps(payload)

    if task_type == "final_summary":
        return "Interview summary is unavailable from the model layer. Use the deterministic session record for recruiter review."

    return " ".join(str(metadata.get("fallback_text") or "").strip().split())
