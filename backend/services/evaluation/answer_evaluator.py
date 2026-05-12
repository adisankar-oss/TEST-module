from typing import List, Optional

from .evaluation_models import AnswerLength, DimensionScores, FollowUpSignal, FollowUpReason
from .scoring_rules import (
    classify_length,
    compute_dimension_scores,
    compute_final_score,
)
from .feedback_generator import determine_followup


class AnswerEvaluator:
    """Deterministic, pure answer evaluator used for unit tests.

    It does **not** call any LLM services and relies entirely on the
    deterministic scoring functions defined in :pymod:`scoring_rules`.
    """

    def __init__(self) -> None:
        # No state needed.
        pass

    def evaluate(
        self,
        question: str,
        answer: str,
        keywords: Optional[List[str]] = None,
        role_level: str = "fresher",
    ) -> dict:
        """Evaluate *answer* against *question*.

        Returns a dictionary compatible with the expectations of the tests:
        ``{"score": int, "followup": FollowUpSignal, "dimension_scores": DimensionScores}``.
        """
        # Length classification
        length = classify_length(answer)

        # Compute dimension scores (technical uses keywords as domain hints)
        dimensions = compute_dimension_scores(
            answer=answer,
            question=question,
            domain_keywords=keywords or [],
        )

        # Final overall score
        final_score = compute_final_score(dimensions, length)

        # Determine follow‑up requirement
        followup_signal = determine_followup(dimensions, length)

        # Return serializable dict (convert dataclasses to plain dicts)
        return {
            "score": final_score,
            "dimension_scores": dimensions,
            "followup": {
                "required": followup_signal.required,
                "reason": followup_signal.reason.value if followup_signal.reason else None,
            },
        }
