from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from services.ai_client import AIClient
from services.answer_evaluator import AnswerEvaluator
from utils.logger import get_logger


DEFAULT_SCORE = 5
DEFAULT_FEEDBACK = "The answer was relevant but needs more clarity and depth."
MAX_EVALUATOR_SCORE = 80


@dataclass(slots=True)
class EvaluationResult:
    score: int
    feedback: str
    overall_score: int | None = None
    red_flags: list[str] = field(default_factory=list)
    needs_followup: bool = False
    followup_reason: str = ""


class EvaluationService:
    def __init__(self, ai_client: AIClient | None = None) -> None:
        self._answer_evaluator = AnswerEvaluator()
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
            result = await asyncio.to_thread(
                self._answer_evaluator.evaluate,
                question,
                answer,
                keywords,
                role_level,
            )
            score = self._map_overall_score_to_ten_point_scale(result.get("overall_score"))
            feedback = self._normalize_feedback(result.get("brief_feedback"))

            self._logger.info(
                json.dumps(
                    {
                        "event": "evaluation_completed",
                        "source": "m6_answer_evaluator",
                        "question": question,
                        "answer": answer,
                        "score": score,
                        "feedback": feedback,
                        "keywords": keywords,
                        "role_level": role_level,
                        "red_flags": result.get("red_flags", []),
                        "overall_score": result.get("overall_score"),
                        "needs_followup": result.get("needs_followup", False),
                        "followup_reason": result.get("followup_reason", ""),
                    }
                )
            )
            return EvaluationResult(
                score=score,
                feedback=feedback,
                overall_score=self._normalize_optional_int(result.get("overall_score")),
                red_flags=self._normalize_red_flags(result.get("red_flags")),
                needs_followup=bool(result.get("needs_followup", False)),
                followup_reason=self._normalize_followup_reason(result.get("followup_reason")),
            )
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
            fallback = EvaluationResult(
                score=DEFAULT_SCORE,
                feedback=DEFAULT_FEEDBACK,
                overall_score=None,
                red_flags=[],
                needs_followup=True,
                followup_reason="Fallback evaluation was used after an evaluator failure.",
            )
            self._logger.info(
                json.dumps(
                    {
                        "event": "evaluation_completed",
                        "source": "fallback",
                        "question": question,
                        "answer": answer,
                        "score": fallback.score,
                        "feedback": fallback.feedback,
                        "red_flags": fallback.red_flags,
                        "overall_score": fallback.overall_score,
                        "needs_followup": fallback.needs_followup,
                        "followup_reason": fallback.followup_reason,
                    }
                )
            )
            return fallback

    @staticmethod
    def _extract_keywords(
        *,
        question: str,
        context: list[dict[str, Any]],
    ) -> list[str]:
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
    def _map_overall_score_to_ten_point_scale(value: Any) -> int:
        try:
            overall = int(value)
        except (TypeError, ValueError):
            return DEFAULT_SCORE

        overall = max(0, min(MAX_EVALUATOR_SCORE, overall))
        return max(1, min(10, round((overall / MAX_EVALUATOR_SCORE) * 10)))

    @staticmethod
    def _normalize_feedback(value: Any) -> str:
        feedback = " ".join(str(value or "").strip().split())
        return feedback or DEFAULT_FEEDBACK

    @staticmethod
    def _normalize_optional_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_red_flags(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        red_flags: list[str] = []
        for item in value:
            normalized = "_".join(str(item or "").strip().lower().split())
            if normalized and normalized not in red_flags:
                red_flags.append(normalized)
        return red_flags

    @staticmethod
    def _normalize_followup_reason(value: Any) -> str:
        return " ".join(str(value or "").strip().split())
