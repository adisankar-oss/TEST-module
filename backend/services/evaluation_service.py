from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from services.ai_client import AIClient
from utils.logger import get_logger


DEFAULT_SCORE = 5
DEFAULT_FEEDBACK = "The answer was relevant but needs more clarity and depth."
SCORE_PATTERN = re.compile(r"score\s*[:=-]?\s*(10|[1-9])", re.IGNORECASE)
FEEDBACK_PATTERN = re.compile(r"feedback\s*[:=-]?\s*(.+)", re.IGNORECASE)


@dataclass(slots=True)
class EvaluationResult:
    score: int
    feedback: str


class EvaluationService:
    def __init__(self, ai_client: AIClient | None = None) -> None:
        self._ai_client = ai_client or AIClient()
        self._logger = get_logger("services.evaluation_service")

    async def evaluate_answer(
        self,
        *,
        question: str,
        answer: str,
        context: list[dict[str, Any]] | None = None,
    ) -> EvaluationResult:
        try:
            response = await self._ai_client.generate_text(
                system_prompt=self._evaluation_system_prompt(),
                user_prompt=self._evaluation_user_prompt(
                    question=question,
                    answer=answer,
                    context=context or [],
                ),
                temperature=0.2,
                max_tokens=180,
                fallback_text=f"Score: {DEFAULT_SCORE}\nFeedback: {DEFAULT_FEEDBACK}",
            )
            parsed = self._parse_result(response)
            self._logger.info(
                json.dumps(
                    {
                        "event": "evaluation_completed",
                        "source": "ai",
                        "question": question,
                        "answer": answer,
                        "score": parsed.score,
                        "feedback": parsed.feedback,
                    }
                )
            )
            return parsed
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
            fallback = EvaluationResult(score=DEFAULT_SCORE, feedback=DEFAULT_FEEDBACK)
            self._logger.info(
                json.dumps(
                    {
                        "event": "evaluation_completed",
                        "source": "fallback",
                        "question": question,
                        "answer": answer,
                        "score": fallback.score,
                        "feedback": fallback.feedback,
                    }
                )
            )
            return fallback

    def _parse_result(self, raw_text: str) -> EvaluationResult:
        score_match = SCORE_PATTERN.search(raw_text or "")
        feedback_match = FEEDBACK_PATTERN.search(raw_text or "")

        score = int(score_match.group(1)) if score_match else DEFAULT_SCORE
        feedback = feedback_match.group(1).strip() if feedback_match else self._extract_feedback(raw_text)
        if not feedback:
            feedback = DEFAULT_FEEDBACK

        return EvaluationResult(score=max(1, min(10, score)), feedback=feedback)

    @staticmethod
    def _extract_feedback(raw_text: str) -> str:
        lines = [line.strip(" -") for line in (raw_text or "").splitlines() if line.strip()]
        for line in lines:
            if "score" not in line.lower():
                return line
        return ""

    @staticmethod
    def _evaluation_system_prompt() -> str:
        return (
            "You are an expert interviewer. "
            "Evaluate the candidate answer for clarity, depth, and relevance to the question. "
            "Respond exactly in this format:\n"
            "Score: <1-10>\n"
            "Feedback: <one short sentence>"
        )

    def _evaluation_user_prompt(
        self,
        *,
        question: str,
        answer: str,
        context: list[dict[str, Any]],
    ) -> str:
        return (
            f"Question:\n{question}\n\n"
            f"Candidate Answer:\n{answer}\n\n"
            f"Recent Context:\n{json.dumps(context, ensure_ascii=True)}\n\n"
            "Evaluate the answer based on clarity, depth, and relevance. "
            "Return a score from 1 to 10 and one short feedback sentence."
        )
