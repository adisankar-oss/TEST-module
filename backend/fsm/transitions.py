from __future__ import annotations

from enum import Enum


class SessionState(str, Enum):
    WAITING = "WAITING"
    INTRO = "INTRO"
    ASKING = "ASKING"
    LISTENING = "LISTENING"
    EVALUATING = "EVALUATING"
    DECISION = "DECISION"
    FOLLOWUP = "FOLLOWUP"
    WRAPPING = "WRAPPING"
    ENDED = "ENDED"
    ERROR = "ERROR"


class RecruiterCommand(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    SKIP_QUESTION = "skip_question"
    END_INTERVIEW = "end_interview"
    EXTEND_5MIN = "extend_5min"


TERMINAL_STATES = {SessionState.ENDED, SessionState.ERROR}

VALID_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.WAITING: {SessionState.INTRO},
    SessionState.INTRO: {SessionState.ASKING},
    SessionState.ASKING: {SessionState.LISTENING},
    SessionState.LISTENING: {SessionState.EVALUATING},
    SessionState.EVALUATING: {SessionState.DECISION},
    SessionState.DECISION: {
        SessionState.ASKING,
        SessionState.FOLLOWUP,
        SessionState.WRAPPING,
    },
    SessionState.FOLLOWUP: {SessionState.ASKING},
    SessionState.WRAPPING: {SessionState.ENDED},
    SessionState.ENDED: set(),
    SessionState.ERROR: set(),
}


def can_transition(current: SessionState, target: SessionState) -> bool:
    return target in VALID_TRANSITIONS.get(current, set())


def validate_transition(current: SessionState, target: SessionState) -> None:
    if not can_transition(current, target):
        raise ValueError(f"Invalid transition: {current.value} -> {target.value}")
