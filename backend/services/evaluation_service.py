from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from backend.services.llm.task_router import TaskRouter, get_task_router
from backend.utils.logger import get_logger


DEFAULT_SCORE = 5
DEFAULT_FEEDBACK = "The answer was relevant but needs more clarity and depth."


@dataclass(slots=True)
class EvaluationResult:
    score: int
    feedback: str
    overall_score: int | None = None
    relevance_score: int | None = None
    depth_score: int | None = None
    technical_score: int | None = None
    communication_score: int | None = None
    red_flags: list[str] = field(default_factory=list)
    needs_followup: bool = False
    followup_required: bool = False
    followup_reason: str = ""
    followup_priority: str = "LOW"
    missing_dimensions: list[str] = field(default_factory=list)
    followup_type: str = "clarification_probe"
    semantic_topic: str = ""


class EvaluationService:
    def __init__(self, task_router: TaskRouter | None = None) -> None:
        self._task_router = task_router or get_task_router()
        self._logger = get_logger("services.evaluation_service")

    async def evaluate_answer(
        self,
        *,
        question: str,
        answer: str,
        context: list[dict[str, Any]] | None = None,
    ) -> EvaluationResult:
        context = context or []
        keywords = self._extract_keywords(question=question, context=context)
        role_level = self._extract_role_level(context=context)

        try:
            payload = await self._task_router.evaluate_realtime(
                question=question,
                answer=answer,
                keywords=keywords,
                role_level=role_level,
                context=context,
            )
            result = self._normalize_result(payload)
            self._logger.info(
                json.dumps(
                    {
                        "event": "evaluation_completed",
                        "source": "hybrid_router",
                        "question": question,
                        "answer": answer,
                        "score": result.score,
                        "feedback": result.feedback,
                        "keywords": keywords,
                        "role_level": role_level,
                        "overall_score": result.overall_score,
                        "needs_followup": result.needs_followup,
                        "followup_required": result.followup_required,
                        "followup_reason": result.followup_reason,
                        "followup_priority": result.followup_priority,
                        "missing_dimensions": result.missing_dimensions,
                        "followup_type": result.followup_type,
                        "semantic_topic": result.semantic_topic,
                    }
                )
            )
            return result
        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "ai_error",
                        "component": "evaluation_service",
                        "error": str(exc),
                    }
                )
            )
            fallback = self._fallback_result()
            self._logger.info(
                json.dumps(
                    {
                        "event": "evaluation_completed",
                        "source": "fallback",
                        "question": question,
                        "answer": answer,
                        "score": fallback.score,
                        "feedback": fallback.feedback,
                        "overall_score": fallback.overall_score,
                        "needs_followup": fallback.needs_followup,
                        "followup_required": fallback.followup_required,
                        "followup_reason": fallback.followup_reason,
                        "followup_priority": fallback.followup_priority,
                        "missing_dimensions": fallback.missing_dimensions,
                        "followup_type": fallback.followup_type,
                        "semantic_topic": fallback.semantic_topic,
                    }
                )
            )
            return fallback

    def _normalize_result(self, payload: dict[str, Any]) -> EvaluationResult:
        score = self._clamp_score(payload.get("score", DEFAULT_SCORE))
        strengths = self._normalize_text_list(payload.get("strengths"))
        weaknesses = self._normalize_text_list(payload.get("weaknesses"))
        followup_required = bool(payload.get("followup_required", score <= 6 or bool(weaknesses)))
        confidence_score = self._clamp_confidence(payload.get("confidence_score", 0.0))
        overall_score = max(0, min(80, int(round(score * 8))))

        missing_dimensions = []
        if any("depth" in weakness for weakness in weaknesses):
            missing_dimensions.append("depth")
        if any("specific" in weakness for weakness in weaknesses):
            missing_dimensions.append("specificity")
        if not missing_dimensions and followup_required:
            missing_dimensions = ["depth", "specificity"]

        followup_type = "clarification_probe"
        if any("technical" in weakness for weakness in weaknesses):
            followup_type = "technical_probe"
        elif any("trade" in weakness for weakness in weaknesses):
            followup_type = "tradeoff_probe"
        elif any("example" in weakness for weakness in weaknesses):
            followup_type = "example_probe"

        feedback = " ".join(
            part
            for part in [
                str(payload.get("feedback") or "").strip(),
                f"Strengths: {', '.join(strengths)}." if strengths else "",
                f"Needs improvement: {', '.join(weaknesses)}." if weaknesses else "",
            ]
            if part
        ) or DEFAULT_FEEDBACK

        priority = "HIGH" if followup_required and (score <= 4 or confidence_score < 0.45) else "LOW"
        return EvaluationResult(
            score=score,
            feedback=feedback,
            overall_score=overall_score,
            relevance_score=max(0, min(25, int(round(score * 2.5)))),
            depth_score=max(0, min(25, int(round((score - 1) * 2.5)))),
            technical_score=max(0, min(25, int(round((score - 1) * 2.5)))),
            communication_score=max(0, min(15, int(round(score * 1.5)))),
            red_flags=[],
            needs_followup=followup_required,
            followup_required=followup_required,
            followup_reason=str(payload.get("followup_reason") or "INCOMPLETE_EXPLANATION").strip() or "INCOMPLETE_EXPLANATION",
            followup_priority=priority,
            missing_dimensions=missing_dimensions,
            followup_type=followup_type,
            semantic_topic=str(payload.get("semantic_topic") or "general").strip() or "general",
        )

    @staticmethod
    def _extract_keywords(*, question: str, context: list[dict[str, Any]]) -> list[str]:
        latest_keywords = []
        for item in reversed(context):
            value = item.get("expected_keywords")
            if isinstance(value, list) and value:
                latest_keywords = [str(keyword).strip() for keyword in value if str(keyword).strip()]
                break

        if latest_keywords:
            return latest_keywords[:8]

        tokens = []
        for raw in question.replace("?", " ").replace(",", " ").split():
            token = raw.strip().lower()
            if len(token) < 4:
                continue
            if token in {"what", "when", "where", "which", "would", "could", "should", "about", "their"}:
                continue
            tokens.append(token)

        deduped: list[str] = []
        for token in tokens:
            if token not in deduped:
                deduped.append(token)
        return deduped[:8]

    @staticmethod
    def _extract_role_level(context: list[dict[str, Any]]) -> str:
        for item in reversed(context):
            role_level = item.get("role_level")
            if isinstance(role_level, str) and role_level.strip():
                return role_level.strip().lower()
        return "fresher"

    @staticmethod
    def _fallback_result() -> EvaluationResult:
        return EvaluationResult(
            score=DEFAULT_SCORE,
            feedback=DEFAULT_FEEDBACK,
            overall_score=None,
            relevance_score=None,
            depth_score=None,
            technical_score=None,
            communication_score=None,
            red_flags=[],
            needs_followup=True,
            followup_required=True,
            followup_reason="INCOMPLETE_EXPLANATION",
            followup_priority="HIGH",
            missing_dimensions=["depth", "specificity"],
            followup_type="clarification_probe",
            semantic_topic="general",
        )

    @staticmethod
    def _normalize_text_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            normalized = " ".join(str(item or "").strip().split())
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    @staticmethod
    def _clamp_score(value: Any) -> int:
        try:
            score = int(value)
        except (TypeError, ValueError):
            score = DEFAULT_SCORE
        return max(0, min(10, score))

    @staticmethod
    def _clamp_confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            confidence = 0.0
        return max(0.0, min(1.0, confidence))
