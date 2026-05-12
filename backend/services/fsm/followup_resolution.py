from enum import Enum
from pydantic import BaseModel
from typing import Optional

class FollowUpStatus(str, Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    EXHAUSTED = "exhausted"   # hit repeat limit
    SUPPRESSED = "suppressed" # candidate fatigue

class FollowUpProbe(BaseModel):
    intent: str
    topic: str
    asked_count: int = 0
    status: FollowUpStatus = FollowUpStatus.ACTIVE
    last_score: Optional[int] = None
    resolved_at_question: Optional[int] = None

class FollowUpResolutionState(BaseModel):
    session_id: str
    probes: list[FollowUpProbe] = []
    total_followups_this_question: int = 0
    candidate_frustration_score: int = 0  # 0–10
    consecutive_short_answers: int = 0

    # Thresholds
    MAX_REPEATS: int = 2
    SCORE_RESOLUTION_THRESHOLD: int = 65
    FRUSTRATION_THRESHOLD: int = 4
    SHORT_ANSWER_WORDS: int = 15

    class Config:
        # Allow threshold fields alongside data fields
        extra = "allow"

import logging
logger = logging.getLogger(__name__)

class FollowUpResolutionEngine:
  """
  Single authority for deciding whether to continue, resolve,
  or suppress a follow-up probe chain.
  """

  def __init__(self, state: FollowUpResolutionState):
    self.state = state

  # ── called after each answer is evaluated ──────────────────────────────

  def record_answer(
    self,
    answer: str,
    score: int,
    intent: str,
    topic: str
  ) -> None:
    words = len(answer.strip().split())
    if words < self.state.SHORT_ANSWER_WORDS:
      self.state.consecutive_short_answers += 1
      self.state.candidate_frustration_score = min(
        10, self.state.candidate_frustration_score + 1
      )
    else:
      self.state.consecutive_short_answers = 0
      self.state.candidate_frustration_score = max(
        0, self.state.candidate_frustration_score - 1
      )

    probe = self._get_or_create_probe(intent, topic)
    probe.asked_count += 1
    probe.last_score   = score
    self.state.total_followups_this_question += 1

    self._auto_resolve(probe, score)

  # ── main decision: should we follow up? ────────────────────────────────

  def should_followup(self, intent: str, topic: str) -> tuple[bool, str]:
    """
    Returns (should_followup: bool, reason: str).
    reason is one of: "proceed", "resolved", "exhausted",
                      "high_score", "frustrated", "duplicate"
    """
    if self._is_frustrated():
      logger.info("FollowUpSuppressed:frustration session=%s", self.state.session_id)
      return False, "frustrated"

    probe = self._find_probe(intent, topic)

    if probe is None:
      return True, "proceed"

    if probe.status in (FollowUpStatus.RESOLVED, FollowUpStatus.EXHAUSTED,
                        FollowUpStatus.SUPPRESSED):
      return False, probe.status.value

    if probe.asked_count >= self.state.MAX_REPEATS:
      probe.status = FollowUpStatus.EXHAUSTED
      logger.info("FollowUpExhausted intent=%s session=%s",
                  intent, self.state.session_id)
      return False, "exhausted"

    if probe.last_score and probe.last_score >= self.state.SCORE_RESOLUTION_THRESHOLD:
      probe.status = FollowUpStatus.RESOLVED
      return False, "high_score"

    return True, "proceed"

  def force_resolve(self, intent: str, topic: str) -> None:
    probe = self._find_probe(intent, topic)
    if probe:
      probe.status = FollowUpStatus.RESOLVED
      logger.info("FollowUpForceResolved intent=%s session=%s",
                  intent, self.state.session_id)

  def reset_for_next_question(self) -> None:
    self.state.total_followups_this_question = 0
    # probes persist across questions (prevents re-probing resolved topics)

  # ── helpers ─────────────────────────────────────────────────────────────

  def _is_frustrated(self) -> bool:
    return (
      self.state.candidate_frustration_score >= self.state.FRUSTRATION_THRESHOLD
      or self.state.consecutive_short_answers >= 3
    )

  def _find_probe(self, intent: str, topic: str) -> Optional[FollowUpProbe]:
    for p in self.state.probes:
      if p.intent == intent or p.topic == topic:
        return p
    return None

  def _get_or_create_probe(self, intent: str, topic: str) -> FollowUpProbe:
    probe = self._find_probe(intent, topic)
    if not probe:
      probe = FollowUpProbe(intent=intent, topic=topic)
      self.state.probes.append(probe)
    return probe

  def _auto_resolve(self, probe: FollowUpProbe, score: int) -> None:
    if score >= self.state.SCORE_RESOLUTION_THRESHOLD:
      probe.status = FollowUpStatus.RESOLVED
    elif probe.asked_count >= self.state.MAX_REPEATS:
      probe.status = FollowUpStatus.EXHAUSTED

  def to_dict(self) -> dict:
    return self.state.dict()

  @classmethod
  def from_dict(cls, data: dict) -> "FollowUpResolutionEngine":
    return cls(FollowUpResolutionState(**data))
