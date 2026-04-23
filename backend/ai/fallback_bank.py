from __future__ import annotations

from services.question_bank_service import AdaptiveQuestionService

_service = AdaptiveQuestionService()


def get_fallback_question(
    *,
    job_id: str,
    topic: str,
    previous_questions: list[str],
) -> str:
    """Legacy adapter: delegates to AdaptiveQuestionService."""
    role_level = _resolve_role_level(job_id)
    return _service.get_question(
        topic,
        role_level,
        session_id=f"legacy_{job_id}",
        asked_questions=previous_questions,
    )


def _resolve_role_level(job_id: str) -> str:
    normalized = (job_id or "").strip().lower()
    if any(token in normalized for token in ("junior", "intern", "entry", "fresher", "fresh_grad")):
        return "fresher"
    if any(token in normalized for token in ("senior", "lead", "staff", "principal")):
        return "senior"
    return "mid"
