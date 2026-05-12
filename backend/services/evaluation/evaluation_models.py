from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class AnswerLength(str, Enum):
    VERY_SHORT = "very_short"  # <20 words
    SHORT = "short"            # 20–50 words
    GOOD = "good"              # 50–180 words
    TOO_LONG = "too_long"      # >350 words


class FollowUpReason(str, Enum):
    LOW_DEPTH = "low_depth"
    VAGUE_ANSWER = "vague_answer"
    CONTRADICTION = "contradiction"
    WEAK_TECHNICAL_REASONING = "weak_technical_reasoning"
    INSUFFICIENT_EXAMPLE = "insufficient_example"


class DimensionScores(BaseModel):
    relevance: int  # 0–100
    depth: int      # 0–100
    clarity: int    # 0–100
    technical: int  # 0–100
    confidence: int # 0–100


class FollowUpSignal(BaseModel):
    required: bool
    reason: Optional[FollowUpReason] = None


class EvaluationResult(BaseModel):
    score: int  # 0–100 final
    dimension_scores: DimensionScores
    strengths: List[str]
    weaknesses: List[str]
    feedback: str
    followup: FollowUpSignal
    difficulty_recommendation: str  # "easier" | "same" | "harder"
    evaluation_confidence: float     # 0.0–1.0