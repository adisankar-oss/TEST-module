from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SessionConfig(BaseModel):
    max_duration_minutes: int = Field(default=45, ge=1, le=240)
    max_questions: int = Field(default=10, ge=1, le=20)
    fsm_start_delay_seconds: int = Field(default=1, ge=0, le=60)
    intro_timeout_seconds: int = Field(default=15, ge=1, le=300)
    question_delivery_timeout_seconds: int = Field(default=15, ge=1, le=300)
    answer_timeout_seconds: int = Field(default=30, ge=1, le=900)
    intro_delay_seconds: int = Field(default=1, ge=0, le=30)
    question_delivery_delay_seconds: int = Field(default=1, ge=0, le=30)
    answer_capture_delay_seconds: int = Field(default=1, ge=0, le=30)
    followup_score_max: int = Field(default=4, ge=0, le=10)
    next_score_min: int = Field(default=8, ge=0, le=10)
    topics: list[str] = Field(
        default_factory=lambda: [
            "technical_skills",
            "problem_solving",
            "behavioural",
            "culture_fit",
        ]
    )
    language: str = Field(default="en", min_length=2, max_length=10)
    avatar_persona: str = Field(default="alex", min_length=1, max_length=50)
    force_followup_test: bool = False


class SessionCreateRequest(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=64)
    job_id: str = Field(min_length=1, max_length=64)
    meeting_url: str = Field(min_length=8)
    meeting_type: Literal["google_meet", "zoom", "daily"]
    schedule_time: datetime
    config: SessionConfig = Field(default_factory=SessionConfig)

    @field_validator("schedule_time")
    @classmethod
    def normalize_schedule_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class SessionCreateResponse(BaseModel):
    session_id: str
    status: str
    join_url: str


class SessionStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str
    state: str
    current_question_number: int
    duration_seconds: int
    max_questions: int
    max_duration_minutes: int
    ended_reason: str | None = None


class SessionCommandRequest(BaseModel):
    command: str


class SessionCommandResponse(BaseModel):
    session_id: str
    command: str
    state: str
    max_duration_minutes: int | None = None


class SessionAnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)

    @field_validator("answer")
    @classmethod
    def validate_answer(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("answer must not be empty")
        return normalized


class SessionAnswerResponse(BaseModel):
    question: str
    answer: str
    score: int
    feedback: str
    next_state: str


class SessionEventResponse(BaseModel):
    session_id: str
    status: str


class LiveEventEnvelope(BaseModel):
    event: str
    payload: dict[str, Any]
