from __future__ import annotations

from typing import Any


def question_generation_fallback(metadata: dict[str, Any] | None = None) -> dict[str, str]:
    metadata = metadata or {}
    question = (
        str(metadata.get("fallback_question") or "").strip()
        or "Tell me about a challenging system you worked on and the trade-offs you had to make?"
    )
    return {
        "question": question,
        "type": str(metadata.get("question_type") or "new"),
        "topic": str(metadata.get("topic") or "technical_skills"),
        "reasoning": "deterministic_fallback",
    }


def final_summary_fallback(metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata or {}
    candidate_name = str(metadata.get("candidate_name") or "The candidate")
    return (
        f"{candidate_name} completed the interview, but the model-generated recruiter summary was unavailable. "
        "Use the recorded question history, evaluator scores, and follow-up trace for the final hiring review."
    )


def greeting_fallback(metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata or {}
    candidate_name = str(metadata.get("candidate_name") or "there").strip() or "there"
    role = str(metadata.get("role") or "the role").strip() or "the role"
    return (
        f"Hi {candidate_name}, I’m Alex and I’ll be your interviewer today for {role}. "
        "We’ll cover technical and behavioural topics, and I’d like this to feel like a real discussion. "
        "To start, could you briefly introduce yourself?"
    )
