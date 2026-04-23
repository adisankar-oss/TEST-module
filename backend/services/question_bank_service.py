from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from utils.logger import get_logger

DEFAULT_QUESTION_BANK_PATH = Path(__file__).resolve().parents[2] / "data" / "question_bank.json"

VALID_TOPICS = frozenset({
    "technical_skills",
    "problem_solving",
    "behavioral",
    "culture_fit",
    "background",
})

VALID_DIFFICULTIES = frozenset({"easy", "medium", "hard"})

TOPIC_ALIASES: dict[str, str] = {
    "behavioural": "behavioral",
    "behaviour": "behavioral",
    "behavior": "behavioral",
    "tech": "technical_skills",
    "technical": "technical_skills",
    "coding": "technical_skills",
    "problem": "problem_solving",
    "problems": "problem_solving",
    "culture": "culture_fit",
    "fit": "culture_fit",
    "intro": "background",
    "introduction": "background",
}

ROLE_TO_DIFFICULTY: dict[str, list[str]] = {
    "fresher": ["easy", "medium"],
    "mid": ["medium", "hard"],
    "senior": ["hard"],
}

ROLE_LEVEL_ALIASES: dict[str, str] = {
    "fresh_grad": "fresher",
    "junior": "fresher",
    "entry": "fresher",
    "intern": "fresher",
    "middle": "mid",
    "intermediate": "mid",
    "lead": "senior",
    "staff": "senior",
    "principal": "senior",
    "manager": "senior",
}

DEFAULT_TOPIC = "behavioral"
DEFAULT_DIFFICULTY = "medium"

DIFFICULTY_ORDER = ("easy", "medium", "hard")


@dataclass(frozen=True, slots=True)
class EvaluationScores:
    """Candidate evaluation scores from M6."""

    relevance_score: float = 0.0
    depth_score: float = 0.0
    technical_score: float = 0.0
    communication_score: float = 0.0
    overall_score: float = 0.0

    def to_dict(self) -> dict[str, float]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationScores:
        """Construct from a dictionary, ignoring unknown keys."""
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: float(v) for k, v in data.items() if k in valid_fields and v is not None}
        return cls(**filtered)


@dataclass(frozen=True, slots=True)
class AdaptiveQuestionResult:
    """Structured output for adaptive fallback question selection."""

    question: str
    source: str
    topic: str
    difficulty: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        """Serialize to a plain dictionary."""
        return asdict(self)


SCORE_WEIGHTS_BY_ROLE: dict[str, dict[str, float]] = {
    "fresher": {
        "relevance_score": 0.30,
        "depth_score": 0.15,
        "technical_score": 0.25,
        "communication_score": 0.30,
    },
    "mid": {
        "relevance_score": 0.25,
        "depth_score": 0.25,
        "technical_score": 0.30,
        "communication_score": 0.20,
    },
    "senior": {
        "relevance_score": 0.20,
        "depth_score": 0.30,
        "technical_score": 0.35,
        "communication_score": 0.15,
    },
}

INCREASE_THRESHOLD = 75.0
MAINTAIN_UPPER = 74.9
MAINTAIN_LOWER = 50.0
DECREASE_THRESHOLD = 50.0

WEAK_TECHNICAL_THRESHOLD = 50.0
WEAK_COMMUNICATION_THRESHOLD = 50.0


class AdaptiveQuestionService:
    """Adaptive fallback question selection based on candidate performance.

    When LLM-based question generation fails, this service selects
    questions from the local bank using real-time evaluation scores
    to adjust difficulty, switch topics to target weaknesses, and
    simulate a real interviewer adapting mid-interview.
    """

    def __init__(
        self,
        *,
        question_bank_path: Path | str | None = None,
        seed: int | None = None,
    ) -> None:
        self._logger = get_logger("services.adaptive_question_service")
        self._bank_path = Path(question_bank_path) if question_bank_path else DEFAULT_QUESTION_BANK_PATH
        self._rng = random.Random(seed)
        self._bank: dict[str, dict[str, list[str]]] | None = None
        self._session_used: dict[str, set[str]] = {}
        self._session_difficulty: dict[str, str] = {}
        self._flat_pool: list[str] | None = None

    def load_questions(self) -> dict[str, dict[str, list[str]]]:
        """Load and cache the difficulty-based question bank from disk."""
        if self._bank is not None:
            return self._bank

        self._logger.info(json.dumps({
            "event": "adaptive_bank_loading",
            "path": str(self._bank_path),
        }))

        with self._bank_path.open("r", encoding="utf-8") as fh:
            raw: dict[str, Any] = json.load(fh)

        bank: dict[str, dict[str, list[str]]] = {}
        total = 0

        for topic_key, difficulties in raw.items():
            if not isinstance(difficulties, dict):
                continue
            norm_topic = self._normalize_topic(topic_key)
            bank.setdefault(norm_topic, {})
            for diff_key, questions in difficulties.items():
                if not isinstance(questions, list):
                    continue
                norm_diff = self._normalize_difficulty(diff_key)
                unique = list(dict.fromkeys(q for q in questions if isinstance(q, str) and q.strip()))
                bank[norm_topic][norm_diff] = unique
                total += len(unique)

        self._bank = bank
        self._flat_pool = None

        self._logger.info(json.dumps({
            "event": "adaptive_bank_loaded",
            "topics": list(bank.keys()),
            "total_questions": total,
        }))

        return self._bank

    def get_adaptive_question(
        self,
        *,
        session_id: str,
        topic: str,
        base_role_level: str,
        previous_questions: list[str] | None = None,
        evaluation_scores: EvaluationScores | None = None,
    ) -> AdaptiveQuestionResult:
        """Select a question adaptively based on candidate performance.

        Args:
            session_id: Unique session identifier.
            topic: Requested interview topic.
            base_role_level: Candidate's base seniority level.
            previous_questions: Questions already asked in this session.
            evaluation_scores: Most recent M6 evaluation scores.

        Returns:
            An AdaptiveQuestionResult with the selected question and metadata.
        """
        bank = self.load_questions()
        norm_topic = self._normalize_topic(topic)
        norm_role = self._normalize_role_level(base_role_level)

        used = self._get_session_used(session_id)
        if previous_questions:
            used.update(self._normalize_text(q) for q in previous_questions if q)

        scores = evaluation_scores or EvaluationScores()
        base_difficulty = self._role_to_base_difficulty(norm_role)
        current_difficulty = self._session_difficulty.get(session_id, base_difficulty)

        new_difficulty, reason = self.adjust_difficulty(
            current_difficulty=current_difficulty,
            evaluation_scores=scores,
            base_role_level=norm_role,
        )

        adjusted_topic = self.select_topic_based_on_weakness(
            requested_topic=norm_topic,
            evaluation_scores=scores,
        )

        if adjusted_topic != norm_topic:
            reason = "weakness_targeted"

        self._session_difficulty[session_id] = new_difficulty

        self._logger.info(json.dumps({
            "event": "adaptive_difficulty_resolved",
            "session_id": session_id,
            "base_role_level": norm_role,
            "base_difficulty": base_difficulty,
            "previous_difficulty": current_difficulty,
            "new_difficulty": new_difficulty,
            "requested_topic": norm_topic,
            "adjusted_topic": adjusted_topic,
            "reason": reason,
            "overall_score": scores.overall_score,
            "technical_score": scores.technical_score,
            "communication_score": scores.communication_score,
        }))

        question = self._select_from_pool(
            bank=bank,
            topic=adjusted_topic,
            difficulty=new_difficulty,
            session_id=session_id,
            used=used,
        )

        self._mark_used(question, session_id)

        return AdaptiveQuestionResult(
            question=question,
            source="adaptive_fallback",
            topic=adjusted_topic,
            difficulty=new_difficulty,
            reason=reason,
        )

    def adjust_difficulty(
        self,
        *,
        current_difficulty: str,
        evaluation_scores: EvaluationScores,
        base_role_level: str = "mid",
    ) -> tuple[str, str]:
        """Compute the next difficulty level based on evaluation scores.

        Args:
            current_difficulty: The difficulty level of the last question.
            evaluation_scores: Most recent candidate evaluation scores.
            base_role_level: The candidate's base role level for weight selection.

        Returns:
            A tuple of (new_difficulty, reason).
        """
        weighted_score = self._compute_weighted_score(evaluation_scores, base_role_level)

        if weighted_score == 0.0 and evaluation_scores.overall_score == 0.0:
            return current_difficulty, "no_scores_available"

        effective_score = evaluation_scores.overall_score if evaluation_scores.overall_score > 0 else weighted_score

        if effective_score >= INCREASE_THRESHOLD:
            new_diff = self._shift_difficulty(current_difficulty, direction=1)
            reason = "increased_difficulty" if new_diff != current_difficulty else "already_at_max"
        elif effective_score < DECREASE_THRESHOLD:
            new_diff = self._shift_difficulty(current_difficulty, direction=-1)
            reason = "decreased_difficulty" if new_diff != current_difficulty else "already_at_min"
        else:
            new_diff = current_difficulty
            reason = "maintained_difficulty"

        return new_diff, reason

    def select_topic_based_on_weakness(
        self,
        *,
        requested_topic: str,
        evaluation_scores: EvaluationScores,
    ) -> str:
        """Override the topic if evaluation scores reveal a specific weakness.

        Args:
            requested_topic: The originally requested topic.
            evaluation_scores: Most recent candidate evaluation scores.

        Returns:
            The topic to use, potentially switched to target a weakness.
        """
        if evaluation_scores.overall_score == 0.0:
            return requested_topic

        if (
            evaluation_scores.technical_score > 0
            and evaluation_scores.technical_score < WEAK_TECHNICAL_THRESHOLD
            and requested_topic != "technical_skills"
        ):
            self._logger.info(json.dumps({
                "event": "topic_override_weakness",
                "from_topic": requested_topic,
                "to_topic": "technical_skills",
                "trigger": "low_technical_score",
                "technical_score": evaluation_scores.technical_score,
            }))
            return "technical_skills"

        if (
            evaluation_scores.communication_score > 0
            and evaluation_scores.communication_score < WEAK_COMMUNICATION_THRESHOLD
            and requested_topic not in ("behavioral", "culture_fit")
        ):
            self._logger.info(json.dumps({
                "event": "topic_override_weakness",
                "from_topic": requested_topic,
                "to_topic": "behavioral",
                "trigger": "low_communication_score",
                "communication_score": evaluation_scores.communication_score,
            }))
            return "behavioral"

        return requested_topic

    def get_session_difficulty(self, session_id: str) -> str:
        """Return the current difficulty for a session."""
        return self._session_difficulty.get(session_id, DEFAULT_DIFFICULTY)

    def get_session_stats(self, session_id: str) -> dict[str, Any]:
        """Return usage statistics for a session."""
        bank = self.load_questions()
        used = self._get_session_used(session_id)
        total = sum(len(q) for diffs in bank.values() for q in diffs.values())
        return {
            "session_id": session_id,
            "current_difficulty": self._session_difficulty.get(session_id, DEFAULT_DIFFICULTY),
            "questions_used": len(used),
            "total_available": total,
            "exhaustion_ratio": round(len(used) / max(total, 1), 3),
        }

    def clear_session(self, session_id: str) -> None:
        """Clear all session state."""
        self._session_used.pop(session_id, None)
        self._session_difficulty.pop(session_id, None)

    def mark_used(self, question: str, *, session_id: str = "default") -> None:
        """Public interface to mark a question as used."""
        self._mark_used(question, session_id)

    # ─── Backward-Compatible Adapter ──────────────────────────

    def get_question(
        self,
        topic: str,
        role_level: str,
        *,
        session_id: str = "default",
        asked_questions: list[str] | None = None,
    ) -> str:
        """Backward-compatible interface mapping role_level to difficulty.

        This allows existing code that calls get_question(topic, role_level)
        to work seamlessly with the new difficulty-based bank.
        """
        bank = self.load_questions()
        norm_topic = self._normalize_topic(topic)
        norm_role = self._normalize_role_level(role_level)
        difficulty = self._role_to_base_difficulty(norm_role)

        used = self._get_session_used(session_id)
        if asked_questions:
            used.update(self._normalize_text(q) for q in asked_questions if q)

        question = self._select_from_pool(
            bank=bank,
            topic=norm_topic,
            difficulty=difficulty,
            session_id=session_id,
            used=used,
        )

        self._mark_used(question, session_id)

        self._logger.info(json.dumps({
            "event": "question_selected",
            "session_id": session_id,
            "topic": norm_topic,
            "role_level": norm_role,
            "difficulty": difficulty,
            "source": "fallback",
            "pool": "primary",
        }))

        return question

    def get_random_question(self, topic: str, role_level: str) -> str:
        """Get a random question without session tracking."""
        bank = self.load_questions()
        norm_topic = self._normalize_topic(topic)
        difficulty = self._role_to_base_difficulty(self._normalize_role_level(role_level))

        pool = (
            bank.get(norm_topic, {}).get(difficulty)
            or self._collect_topic_questions(bank, norm_topic)
            or self._get_flat_pool(bank)
        )

        if not pool:
            return "Tell me about a challenging project you worked on and the trade-offs you had to make."

        return self._rng.choice(pool)

    # ─── Internal Helpers ─────────────────────────────────────

    def _select_from_pool(
        self,
        *,
        bank: dict[str, dict[str, list[str]]],
        topic: str,
        difficulty: str,
        session_id: str,
        used: set[str],
    ) -> str:
        """Select an unused question with cascading fallback through pools."""
        candidate_pools: list[list[str]] = []

        exact = bank.get(topic, {}).get(difficulty, [])
        if exact:
            candidate_pools.append(exact)

        adjacent = self._get_adjacent_difficulties(difficulty)
        for adj_diff in adjacent:
            adj_pool = bank.get(topic, {}).get(adj_diff, [])
            if adj_pool:
                candidate_pools.append(adj_pool)

        topic_all = self._collect_topic_questions(bank, topic)
        if topic_all:
            candidate_pools.append(topic_all)

        diff_all = self._collect_difficulty_questions(bank, difficulty)
        if diff_all:
            candidate_pools.append(diff_all)

        flat = self._get_flat_pool(bank)
        if flat:
            candidate_pools.append(flat)

        for pool in candidate_pools:
            unused = [q for q in pool if self._normalize_text(q) not in used]
            if unused:
                return self._rng.choice(unused)

        self._logger.warning(json.dumps({
            "event": "adaptive_pool_exhausted",
            "session_id": session_id,
            "topic": topic,
            "difficulty": difficulty,
            "action": "resetting_session_pool",
        }))
        self._session_used[session_id] = set()

        reset_pool = exact or flat
        shuffled = list(reset_pool)
        self._rng.shuffle(shuffled)
        return shuffled[0] if shuffled else "Tell me about a challenging project you worked on and the trade-offs you had to make."

    def _mark_used(self, question: str, session_id: str) -> None:
        """Mark a question as used within a session."""
        used = self._get_session_used(session_id)
        used.add(self._normalize_text(question))

    def _get_session_used(self, session_id: str) -> set[str]:
        """Get or create the set of used questions for a session."""
        if session_id not in self._session_used:
            self._session_used[session_id] = set()
        return self._session_used[session_id]

    def _get_flat_pool(self, bank: dict[str, dict[str, list[str]]]) -> list[str]:
        """Build and cache a flat list of all questions."""
        if self._flat_pool is not None:
            return self._flat_pool
        pool: list[str] = []
        seen: set[str] = set()
        for diffs in bank.values():
            for questions in diffs.values():
                for q in questions:
                    norm = self._normalize_text(q)
                    if norm not in seen:
                        seen.add(norm)
                        pool.append(q)
        self._flat_pool = pool
        return pool

    @staticmethod
    def _collect_topic_questions(bank: dict[str, dict[str, list[str]]], topic: str) -> list[str]:
        """Collect all questions for a topic across all difficulties."""
        topic_data = bank.get(topic, {})
        result: list[str] = []
        seen: set[str] = set()
        for questions in topic_data.values():
            for q in questions:
                norm = " ".join(q.lower().split())
                if norm not in seen:
                    seen.add(norm)
                    result.append(q)
        return result

    @staticmethod
    def _collect_difficulty_questions(bank: dict[str, dict[str, list[str]]], difficulty: str) -> list[str]:
        """Collect all questions for a difficulty across all topics."""
        result: list[str] = []
        seen: set[str] = set()
        for topic_data in bank.values():
            for q in topic_data.get(difficulty, []):
                norm = " ".join(q.lower().split())
                if norm not in seen:
                    seen.add(norm)
                    result.append(q)
        return result

    @staticmethod
    def _get_adjacent_difficulties(difficulty: str) -> list[str]:
        """Return adjacent difficulty levels for fallback cascading."""
        idx = DIFFICULTY_ORDER.index(difficulty) if difficulty in DIFFICULTY_ORDER else 1
        adjacent: list[str] = []
        if idx > 0:
            adjacent.append(DIFFICULTY_ORDER[idx - 1])
        if idx < len(DIFFICULTY_ORDER) - 1:
            adjacent.append(DIFFICULTY_ORDER[idx + 1])
        return adjacent

    @staticmethod
    def _shift_difficulty(current: str, *, direction: int) -> str:
        """Shift difficulty up (+1) or down (-1), clamped to valid range."""
        idx = DIFFICULTY_ORDER.index(current) if current in DIFFICULTY_ORDER else 1
        new_idx = max(0, min(len(DIFFICULTY_ORDER) - 1, idx + direction))
        return DIFFICULTY_ORDER[new_idx]

    @staticmethod
    def _role_to_base_difficulty(role_level: str) -> str:
        """Map a role level to its base difficulty."""
        mapping = {"fresher": "easy", "mid": "medium", "senior": "hard"}
        return mapping.get(role_level, DEFAULT_DIFFICULTY)

    @staticmethod
    def _compute_weighted_score(scores: EvaluationScores, role_level: str) -> float:
        """Compute a weighted overall score using role-specific weights."""
        weights = SCORE_WEIGHTS_BY_ROLE.get(role_level, SCORE_WEIGHTS_BY_ROLE["mid"])
        total = 0.0
        for field_name, weight in weights.items():
            total += getattr(scores, field_name, 0.0) * weight
        return round(total, 2)

    @staticmethod
    def _normalize_topic(topic: str) -> str:
        """Normalize a topic string, resolving aliases."""
        cleaned = (topic or "").strip().lower().replace(" ", "_").replace("-", "_")
        if cleaned in VALID_TOPICS:
            return cleaned
        resolved = TOPIC_ALIASES.get(cleaned)
        if resolved:
            return resolved
        return DEFAULT_TOPIC

    @staticmethod
    def _normalize_difficulty(difficulty: str) -> str:
        """Normalize a difficulty string."""
        cleaned = (difficulty or "").strip().lower()
        if cleaned in VALID_DIFFICULTIES:
            return cleaned
        role_map = {"fresher": "easy", "mid": "medium", "senior": "hard"}
        return role_map.get(cleaned, DEFAULT_DIFFICULTY)

    @staticmethod
    def _normalize_role_level(role_level: str) -> str:
        """Normalize a role level string, resolving aliases."""
        cleaned = (role_level or "").strip().lower().replace(" ", "_").replace("-", "_")
        valid = {"fresher", "mid", "senior"}
        if cleaned in valid:
            return cleaned
        resolved = ROLE_LEVEL_ALIASES.get(cleaned)
        if resolved:
            return resolved
        return "mid"

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize text for deduplication comparison."""
        return " ".join((text or "").strip().lower().split())
