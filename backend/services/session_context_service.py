"""Session context management for maintaining conversation history."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from utils.logger import get_logger


@dataclass(slots=True)
class QuestionAnswerContext:
    """A single Q&A pair with evaluation."""

    question: str
    answer: str
    score: int
    feedback: str
    question_number: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "score": self.score,
            "feedback": self.feedback,
            "question_number": self.question_number,
        }


class SessionContextService:
    """Manages session context including conversation history and question/answer tracking."""

    MAX_HISTORY_ITEMS = 3

    def __init__(self) -> None:
        self._logger = get_logger("services.session_context_service")
        self._history: list[QuestionAnswerContext] = []

    def add_qa_pair(
        self,
        question: str,
        answer: str,
        score: int,
        feedback: str,
        question_number: int,
    ) -> None:
        """Add a Q&A pair to context history.
        
        Maintains only the last MAX_HISTORY_ITEMS pairs.
        """
        pair = QuestionAnswerContext(
            question=question,
            answer=answer,
            score=score,
            feedback=feedback,
            question_number=question_number,
        )
        self._history.append(pair)

        # Keep only the last MAX_HISTORY_ITEMS
        if len(self._history) > self.MAX_HISTORY_ITEMS:
            self._history = self._history[-self.MAX_HISTORY_ITEMS :]

    def get_history(self) -> list[dict[str, Any]]:
        """Get conversation history as list of dicts."""
        return [pair.to_dict() for pair in self._history]

    def get_last_questions(self) -> list[str]:
        """Get last 3 questions asked."""
        return [pair.question for pair in self._history]

    def get_last_answers(self) -> list[str]:
        """Get last 3 answers given."""
        return [pair.answer for pair in self._history]

    def get_last_scores(self) -> list[int]:
        """Get last 3 scores."""
        return [pair.score for pair in self._history]

    def clear(self) -> None:
        """Clear all history."""
        self._history.clear()

    @staticmethod
    def from_json(data: dict[str, Any] | None) -> SessionContextService:
        """Reconstruct context from persisted JSON."""
        service = SessionContextService()
        if data and "history" in data:
            for item in data["history"]:
                service.add_qa_pair(
                    question=item.get("question", ""),
                    answer=item.get("answer", ""),
                    score=item.get("score", 5),
                    feedback=item.get("feedback", ""),
                    question_number=item.get("question_number", 0),
                )
        return service

    def to_json(self) -> dict[str, Any]:
        """Persist context as JSON."""
        return {"history": self.get_history()}
