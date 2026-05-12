from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


STOPWORDS = {
    "a",
    "an",
    "and",
    "can",
    "could",
    "did",
    "do",
    "does",
    "explain",
    "further",
    "how",
    "in",
    "more",
    "please",
    "that",
    "the",
    "through",
    "walk",
    "what",
    "when",
    "why",
    "with",
    "would",
    "you",
    "your",
}
CLUSTER_TERMS = {
    "clarification_probe": {"clarify", "detail", "elaborate", "explain"},
    "technical_probe": {"technical", "system", "reasoning", "approach", "design"},
    "example_probe": {"example", "specific", "scenario", "situation", "experience"},
    "contradiction_probe": {"earlier", "clarify", "now", "contradiction", "inconsistent"},
    "tradeoff_probe": {"trade", "tradeoff", "trade-offs", "versus", "balance", "alternative"},
    "scalability_probe": {"scale", "scalability", "load", "traffic", "throughput"},
    "implementation_probe": {"implement", "implementation", "step", "steps", "build", "code"},
    "behavioral_probe": {"stakeholder", "team", "communicate", "role", "behavior"},
}
TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_+#.-]+")


@dataclass(slots=True)
class ProbeFingerprint:
    question: str
    probe_type: str
    semantic_topic: str
    root_question_id: str
    followup_chain_id: str

    @classmethod
    def from_dict(cls, payload: Any) -> ProbeFingerprint | None:
        if not isinstance(payload, dict):
            return None
        question = str(payload.get("question") or "")
        probe_type = str(payload.get("probe_type") or "")
        if not question or not probe_type:
            return None
        return cls(
            question=question,
            probe_type=probe_type,
            semantic_topic=str(payload.get("semantic_topic") or ""),
            root_question_id=str(payload.get("root_question_id") or ""),
            followup_chain_id=str(payload.get("followup_chain_id") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SemanticProbeTracker:
    THRESHOLD = 0.48

    def __init__(self, records: list[ProbeFingerprint] | None = None) -> None:
        self._records = records or []

    @classmethod
    def from_dicts(cls, payloads: list[dict[str, Any]]) -> SemanticProbeTracker:
        records: list[ProbeFingerprint] = []
        for payload in payloads:
            record = ProbeFingerprint.from_dict(payload)
            if record is not None:
                records.append(record)
        return cls(records=records)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self._records]

    def register(self, record: ProbeFingerprint) -> None:
        self._records.append(record)

    def max_similarity(
        self,
        *,
        question: str,
        probe_type: str,
        root_question_id: str,
    ) -> float:
        scores = [
            self.similarity(question, probe_type, record.question, record.probe_type)
            for record in self._records
            if record.root_question_id == root_question_id
        ]
        return max(scores) if scores else 0.0

    def repeat_count(
        self,
        *,
        question: str,
        probe_type: str,
        root_question_id: str,
    ) -> int:
        return sum(
            1
            for record in self._records
            if record.root_question_id == root_question_id
            and self.similarity(question, probe_type, record.question, record.probe_type) >= self.THRESHOLD
        )

    @classmethod
    def similarity(
        cls,
        left_question: str,
        left_probe_type: str,
        right_question: str,
        right_probe_type: str,
    ) -> float:
        left_tokens = cls._tokens(left_question) | CLUSTER_TERMS.get(left_probe_type, set())
        right_tokens = cls._tokens(right_question) | CLUSTER_TERMS.get(right_probe_type, set())
        if not left_tokens or not right_tokens:
            return 0.0

        overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        type_bonus = 0.25 if left_probe_type == right_probe_type else 0.0
        return round(min(1.0, overlap + type_bonus), 4)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {
            token.lower()
            for token in TOKEN_PATTERN.findall(text or "")
            if len(token) > 2 and token.lower() not in STOPWORDS
        }
