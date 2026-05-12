from __future__ import annotations

import re
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from pydantic import BaseModel

from .memory_models import (
    CandidateMemoryState,
    ContradictionEntry,
    ConversationSummary,
    DepthTrend,
    StrengthEntry,
    WeaknessEntry,
    TopicEntry,
)
from ..evaluation.evaluation_models import EvaluationResult, DimensionScores


# Helper functions ------------------------------------------------------------

def extract_topic(question: str) -> str:
    """Return a simple heuristic topic – first four significant words.

    Stop‑words are filtered out; the remaining words are joined with spaces.
    """
    stopwords = {"the", "a", "an", "how", "what", "why", "would", "you", "do", "does"}
    tokens = [t for t in re.findall(r"[A-Za-z0-9_]+", question.lower()) if t not in stopwords]
    return " ".join(tokens[:4])


def extract_claim_sentence(text: str, keyword: str) -> str:
    """Return the first sentence from *text* containing *keyword*.

    Sentences are split on punctuation marks. If none found, truncate the text.
    """
    # Simple sentence split – include ., !, ?
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    for s in sentences:
        if keyword.lower() in s.lower():
            return s.strip()
    # Fallback – first 120 characters
    return text[:120].strip()


class CandidateMemory:
    """In‑memory candidate memory store.

    All updates are performed on a ``CandidateMemoryState`` instance. The class
    provides convenience methods to mutate the state and to retrieve a strategy
    context for the question generation engine.
    """

    # Contradiction phrase pairs – simple opposites used for detection
    _contradiction_pairs: List[Tuple[str, str]] = [
        ("simple", "complex"),
        ("avoid complexity", "prioritize scalability"),
        ("prefer", "avoid"),
        ("always", "never"),
        ("fast", "slow"),
        ("monolith", "microservice"),
        ("single", "distributed"),
    ]

    def __init__(self, session_id: str):
        self.state: CandidateMemoryState = CandidateMemoryState(session_id=session_id)
        # Rolling buffer of the last five raw answers for contradiction detection
        self._answer_buffer: Deque[str] = deque(maxlen=5)

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def update(
        self,
        question: str,
        question_intent: str,
        answer: str,
        evaluation: EvaluationResult,
    ) -> None:
        """Update the memory after an answer has been evaluated.

        The method follows the ordered steps described in the specification.
        """
        # 1. Increment question count
        self.state.question_count += 1

        # 2. Update topics/intents
        self._update_topics(question, question_intent, evaluation.dimension_scores)

        # 3. Update strengths and weaknesses
        self._update_strengths_weaknesses(evaluation)

        # 4. Detect contradictions with recent answers
        self._detect_contradiction(answer)

        # 5. Record depth/technical trend
        self._update_depth_history(evaluation.dimension_scores)

        # 6. Refresh the high‑level summary if enough data is available
        self._update_summary()

    # ---------------------------------------------------------------------
    # Private helpers
    # ---------------------------------------------------------------------
    def _update_topics(
        self, question: str, intent: str, dims: DimensionScores
    ) -> None:
        if intent in self.state.question_intents:
            # Increment times_seen for the matching topic entry
            for entry in self.state.topics_seen:
                if entry.question_intent == intent:
                    entry.times_seen += 1
                    break
        else:
            self.state.question_intents.append(intent)
            new_entry = TopicEntry(
                topic=extract_topic(question),
                question_intent=intent,
                depth_score=dims.depth,
            )
            self.state.topics_seen.append(new_entry)

    def _update_strengths_weaknesses(self, evaluation: EvaluationResult) -> None:
        # Strength thresholds
        strength_map = {
            "relevance": (75, "Strong question alignment"),
            "depth": (70, "Demonstrates multi-step reasoning"),
            "technical": (65, "Accurate technical vocabulary"),
            "clarity": (70, "Clear structured communication"),
            "confidence": (70, "Decisive and specific"),
        }
        # Weakness thresholds
        weakness_map = {
            "depth": (45, "Lacks reasoning depth or trade-off discussion"),
            "technical": (40, "Insufficient technical specificity"),
            "clarity": (45, "Fragmented or unclear communication"),
            "confidence": (40, "Hedging and vague assertions"),
            "relevance": (50, "Answer drifted from question focus"),
        }

        # Process strengths
        for dim, (threshold, description) in strength_map.items():
            score = getattr(evaluation.dimension_scores, dim)
            if score > threshold:
                # Find existing entry
                existing = next(
                    (s for s in self.state.strengths if s.dimension == dim), None
                )
                if existing:
                    existing.confirmed_count += 1
                else:
                    self.state.strengths.append(
                        StrengthEntry(dimension=dim, description=description)
                    )

        # Process weaknesses
        for dim, (threshold, description) in weakness_map.items():
            score = getattr(evaluation.dimension_scores, dim)
            if score < threshold:
                existing = next(
                    (w for w in self.state.weaknesses if w.dimension == dim), None
                )
                if existing:
                    existing.occurrences += 1
                    occ = existing.occurrences
                    if occ >= 3:
                        existing.followup_priority = 3
                    elif occ == 2:
                        existing.followup_priority = 2
                    else:
                        existing.followup_priority = 1
                else:
                    self.state.weaknesses.append(
                        WeaknessEntry(
                            dimension=dim,
                            description=description,
                            followup_priority=1,
                        )
                    )

    def _detect_contradiction(self, new_answer: str) -> None:
        # Compare against answers in the rolling buffer
        for idx, past_answer in enumerate(reversed(self._answer_buffer)):
            # ``idx`` is 0 for the most recent answer in the buffer
            for term_a, term_b in self._contradiction_pairs:
                # Check both directions
                if (
                    term_a in past_answer.lower()
                    and term_b in new_answer.lower()
                ) or (
                    term_b in past_answer.lower()
                    and term_a in new_answer.lower()
                ):
                    # Determine which term appeared in which answer for extraction
                    if term_a in past_answer.lower() and term_b in new_answer.lower():
                        earlier, later = past_answer, new_answer
                        kw_earlier, kw_later = term_a, term_b
                    else:
                        earlier, later = new_answer, past_answer
                        kw_earlier, kw_later = term_b, term_a
                    entry = ContradictionEntry(
                        earlier_claim=extract_claim_sentence(earlier, kw_earlier),
                        later_claim=extract_claim_sentence(later, kw_later),
                        question_indices=(
                            self.state.question_count - (idx + 1),
                            self.state.question_count,
                        ),
                    )
                    self.state.contradictions.append(entry)
                    # Once a contradiction is found for this new answer we stop further checks
                    break
            else:
                continue  # only executed if inner loop did NOT break
            break  # break outer loop if contradiction detected
        # Append the new answer to the buffer after detection
        self._answer_buffer.append(new_answer)

    def _update_depth_history(self, dims: DimensionScores) -> None:
        self.state.depth_history.append(
            DepthTrend(
                question_index=self.state.question_count,
                depth_score=dims.depth,
                technical_score=dims.technical,
            )
        )

    def _update_summary(self) -> None:
        if self.state.question_count < 2:
            return
        # strongest domain – highest confirmed_count among strengths
        if self.state.strengths:
            strongest = max(self.state.strengths, key=lambda s: s.confirmed_count)
            strongest_domain = strongest.dimension
        else:
            strongest_domain = "general"
        # weakest domain – highest occurrences among weaknesses
        if self.state.weaknesses:
            weakest = max(self.state.weaknesses, key=lambda w: w.occurrences)
            weakest_domain = weakest.dimension
        else:
            weakest_domain = "none identified"
        # overall trajectory based on last two depth scores
        if len(self.state.depth_history) >= 2:
            last = self.state.depth_history[-1]
            prev = self.state.depth_history[-2]
            diff = last.depth_score - prev.depth_score
            if diff > 10:
                overall_trajectory = "improving"
            elif diff < -10:
                overall_trajectory = "declining"
            else:
                overall_trajectory = "stable"
        else:
            overall_trajectory = "stable"
        # communication quality – average of last 3 clarity scores
        recent_clarity = [h.depth_score for h in self.state.depth_history[-3:]]
        if recent_clarity:
            avg_clarity = sum(recent_clarity) / len(recent_clarity)
            if avg_clarity > 65:
                communication_quality = "strong"
            elif avg_clarity > 45:
                communication_quality = "adequate"
            else:
                communication_quality = "weak"
        else:
            communication_quality = "weak"
        # candidate tendencies – up to three descriptors
        tendencies: List[str] = []
        for w in self.state.weaknesses:
            if w.occurrences >= 2:
                tendencies.append(f"Consistently weak in {w.dimension}")
                if len(tendencies) >= 3:
                    break
        if len(tendencies) < 3:
            for s in self.state.strengths:
                if s.confirmed_count >= 2:
                    tendencies.append(f"Reliably strong in {s.dimension}")
                    if len(tendencies) >= 3:
                        break
        if len(tendencies) < 3 and self.state.contradictions:
            tendencies.append("Shows inconsistency in stated preferences")
        # Trim to max three items
        tendencies = tendencies[:3]

        self.state.summary = ConversationSummary(
            candidate_tendencies=tendencies,
            strongest_domain=strongest_domain,
            weakest_domain=weakest_domain,
            communication_quality=communication_quality,
            overall_trajectory=overall_trajectory,
        )

    # ---------------------------------------------------------------------
    # Strategy context methods
    # ---------------------------------------------------------------------
    def get_strategy_context(self) -> Dict[str, Any]:
        # Unresolved contradictions – those not marked resolved
        unresolved = [c for c in self.state.contradictions if not c.resolved]
        has_unresolved = bool(unresolved)
        contradiction_challenge: Optional[str] = None
        if has_unresolved:
            entry = unresolved[0]
            contradiction_challenge = (
                f'Earlier you said "{entry.earlier_claim[:80]}" but later indicated "{entry.later_claim[:80]}". '
                "Can you clarify your position?"
            )
        # Urgent weaknesses – priority >= 2
        urgent_weak = [w.dimension for w in self.state.weaknesses if w.followup_priority >= 2]
        # Confirmed strengths – confirmed_count >= 2
        confirmed_str = [s.dimension for s in self.state.strengths if s.confirmed_count >= 2]
        # Topics to avoid – intents seen two or more times
        topics_to_avoid = [t.question_intent for t in self.state.topics_seen if t.times_seen >= 2]
        # Depth trend description from summary if present
        depth_trend = (
            self.state.summary.overall_trajectory if self.state.summary else "unknown"
        )
        # Recommended difficulty shift logic
        if urgent_weak:
            recommended_shift = "decrease"
        elif confirmed_str and depth_trend == "improving":
            recommended_shift = "increase"
        else:
            recommended_shift = "maintain"
        # Continuity reference – use last answer in buffer
        continuity_reference: Optional[str] = None
        if self._answer_buffer:
            last_answer = self._answer_buffer[-1]
            sentence = extract_claim_sentence(last_answer, "")  # first sentence
            continuity_reference = f'You mentioned "{sentence[:80]}..." — '
        return {
            "has_unresolved_contradictions": has_unresolved,
            "contradiction_challenge": contradiction_challenge,
            "urgent_weaknesses": urgent_weak,
            "confirmed_strengths": confirmed_str,
            "topics_to_avoid": topics_to_avoid,
            "depth_trend": depth_trend,
            "recommended_difficulty_shift": recommended_shift,
            "continuity_reference": continuity_reference,
        }

    # ---------------------------------------------------------------------
    # Serialization helpers
    # ---------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialize the internal state for websocket transport.

        ``pydantic``'s ``dict`` method already produces JSON‑compatible data.
        """
        return self.state.dict()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CandidateMemory":
        """Recreate a ``CandidateMemory`` from a previously serialized dict.

        The ``session_id`` is extracted from the top‑level field.
        """
        session_id = data.get("session_id")
        if not session_id:
            raise ValueError("Missing session_id in memory payload")
        memory = cls(session_id=session_id)
        # Directly construct the state model from the dict
        memory.state = CandidateMemoryState(**data)
        return memory
