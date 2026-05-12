from __future__ import annotations

from typing import List, Optional, Tuple

from pydantic import BaseModel


class TopicEntry(BaseModel):
    topic: str
    question_intent: str
    depth_score: int
    times_seen: int = 1


class WeaknessEntry(BaseModel):
    dimension: str  # "depth" | "technical" | "clarity" | "confidence"
    description: str
    occurrences: int = 1
    followup_priority: int  # 1 (low) to 3 (urgent)


class StrengthEntry(BaseModel):
    dimension: str
    description: str
    confirmed_count: int = 1


class ContradictionEntry(BaseModel):
    earlier_claim: str
    later_claim: str
    question_indices: Tuple[int, int]  # which question numbers
    resolved: bool = False


class DepthTrend(BaseModel):
    question_index: int
    depth_score: int
    technical_score: int


class ConversationSummary(BaseModel):
    candidate_tendencies: List[str]  # max 3
    strongest_domain: str
    weakest_domain: str
    communication_quality: str  # "strong" | "adequate" | "weak"
    overall_trajectory: str  # "improving" | "declining" | "stable"


class CandidateMemoryState(BaseModel):
    session_id: str
    strengths: List[StrengthEntry] = []
    weaknesses: List[WeaknessEntry] = []
    topics_seen: List[TopicEntry] = []
    question_intents: List[str] = []
    depth_history: List[DepthTrend] = []
    contradictions: List[ContradictionEntry] = []
    summary: Optional[ConversationSummary] = None
    question_count: int = 0

    class Config:
        # Must remain fully JSON‑serializable for websocket transport
        json_encoders = {}
