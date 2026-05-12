from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


VALID_REASONS = {
    "LOW_DEPTH",
    "NO_EXAMPLE",
    "VAGUE_RESPONSE",
    "WEAK_TECHNICAL_REASONING",
    "CONTRADICTION",
    "INCOMPLETE_EXPLANATION",
    "MISSING_TRADEOFFS",
    "LOW_CONFIDENCE",
    "GENERIC_RESPONSE",
    "UNSUPPORTED_CLAIM",
}
PROBE_TYPE_BY_REASON = {
    "LOW_DEPTH": "clarification_probe",
    "NO_EXAMPLE": "example_probe",
    "VAGUE_RESPONSE": "clarification_probe",
    "WEAK_TECHNICAL_REASONING": "technical_probe",
    "CONTRADICTION": "contradiction_probe",
    "INCOMPLETE_EXPLANATION": "clarification_probe",
    "MISSING_TRADEOFFS": "tradeoff_probe",
    "LOW_CONFIDENCE": "clarification_probe",
    "GENERIC_RESPONSE": "behavioral_probe",
    "UNSUPPORTED_CLAIM": "implementation_probe",
}
TECHNICAL_SIGNALS = {
    "api",
    "architecture",
    "async",
    "cache",
    "caching",
    "database",
    "design",
    "docker",
    "grpc",
    "index",
    "kafka",
    "latency",
    "load",
    "monitoring",
    "postgres",
    "queue",
    "redis",
    "retry",
    "scalability",
    "service",
    "shard",
    "system",
}
GENERIC_PHRASES = {
    "ok",
    "not sure",
    "kind of",
    "sort of",
    "something like",
    "it was good",
    "i guess",
    "maybe",
    "basically",
}
EXAMPLE_SIGNALS = (
    "for example",
    "for instance",
    "in one project",
    "there was a time",
    "i worked on",
    "we had",
    "when i",
)
TRADEOFF_SIGNALS = ("trade-off", "tradeoff", "versus", "balance", "alternative", "pros", "cons")
UNSUPPORTED_CLAIM_SIGNALS = ("improved", "reduced", "increased", "optimized", "faster", "better", "scaled")
HEDGING_SIGNALS = ("maybe", "probably", "i think", "i guess", "might have", "kind of", "sort of")
TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_+#.-]+")


@dataclass(slots=True)
class FollowUpAnalysis:
    followup_required: bool
    followup_reason: str = ""
    followup_priority: str = "LOW"
    missing_dimensions: list[str] = field(default_factory=list)
    followup_type: str = "clarification_probe"
    semantic_topic: str = ""
    contradiction_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "followup_required": self.followup_required,
            "followup_reason": self.followup_reason,
            "followup_priority": self.followup_priority,
            "missing_dimensions": list(self.missing_dimensions),
            "followup_type": self.followup_type,
            "semantic_topic": self.semantic_topic,
            "contradiction_text": self.contradiction_text,
        }


class FollowUpClassifier:
    def classify(
        self,
        *,
        question: str,
        answer: str,
        relevance_score: int,
        depth_score: int,
        technical_score: int,
        communication_score: int,
        red_flags: list[str] | None = None,
        contradiction: str | None = None,
    ) -> FollowUpAnalysis:
        normalized_question = self._normalize(question)
        normalized_answer = self._normalize(answer)
        answer_lower = normalized_answer.lower()
        question_lower = normalized_question.lower()
        red_flags = [str(flag).lower() for flag in red_flags or []]
        semantic_topic = self._extract_topic(normalized_question, normalized_answer)

        if contradiction or "contradiction" in red_flags:
            return self._analysis(
                reason="CONTRADICTION",
                priority="HIGH",
                missing_dimensions=["consistency", "clarification"],
                semantic_topic=semantic_topic,
                contradiction_text=contradiction or "",
            )

        word_count = len(normalized_answer.split())
        if word_count <= 2 or answer_lower in GENERIC_PHRASES:
            return self._analysis(
                reason="GENERIC_RESPONSE",
                priority="HIGH",
                missing_dimensions=["detail", "specific_example"],
                semantic_topic=semantic_topic,
                probe_type="example_probe" if self._is_behavioral_question(question_lower) else "clarification_probe",
            )

        if self._question_requests_example(question_lower) and not self._has_example(answer_lower):
            return self._analysis(
                reason="NO_EXAMPLE",
                priority="HIGH",
                missing_dimensions=["specific_example"],
                semantic_topic=semantic_topic,
                probe_type="example_probe",
            )

        if self._question_requests_tradeoffs(question_lower) and not self._has_tradeoffs(answer_lower):
            return self._analysis(
                reason="MISSING_TRADEOFFS",
                priority="HIGH",
                missing_dimensions=["tradeoffs", "decision_criteria"],
                semantic_topic=semantic_topic,
                probe_type="tradeoff_probe",
            )

        if self._unsupported_claim(answer_lower):
            return self._analysis(
                reason="UNSUPPORTED_CLAIM",
                priority="MEDIUM",
                missing_dimensions=["evidence", "validation"],
                semantic_topic=semantic_topic,
                probe_type="implementation_probe",
            )

        if self._is_technical_question(question_lower) and technical_score < 12:
            probe_type = "scalability_probe" if "scale" in question_lower or "load" in question_lower else "technical_probe"
            return self._analysis(
                reason="WEAK_TECHNICAL_REASONING",
                priority="HIGH",
                missing_dimensions=["technical_reasoning", "implementation_detail"],
                semantic_topic=semantic_topic,
                probe_type=probe_type,
            )

        if self._is_vague(answer_lower):
            return self._analysis(
                reason="VAGUE_RESPONSE",
                priority="HIGH",
                missing_dimensions=["specificity", "clarity"],
                semantic_topic=semantic_topic,
            )

        if self._low_confidence(answer_lower, communication_score):
            return self._analysis(
                reason="LOW_CONFIDENCE",
                priority="MEDIUM",
                missing_dimensions=["confidence", "specificity"],
                semantic_topic=semantic_topic,
            )

        if depth_score < 10:
            return self._analysis(
                reason="LOW_DEPTH",
                priority="HIGH",
                missing_dimensions=["depth", "reasoning"],
                semantic_topic=semantic_topic,
                probe_type="behavioral_probe" if self._is_behavioral_question(question_lower) else "clarification_probe",
            )

        if word_count < 12 or relevance_score < 12:
            return self._analysis(
                reason="INCOMPLETE_EXPLANATION",
                priority="MEDIUM",
                missing_dimensions=["completeness"],
                semantic_topic=semantic_topic,
            )

        return FollowUpAnalysis(
            followup_required=False,
            followup_reason="",
            followup_priority="LOW",
            missing_dimensions=[],
            followup_type="clarification_probe",
            semantic_topic=semantic_topic,
        )

    @staticmethod
    def _analysis(
        *,
        reason: str,
        priority: str,
        missing_dimensions: list[str],
        semantic_topic: str,
        probe_type: str | None = None,
        contradiction_text: str = "",
    ) -> FollowUpAnalysis:
        return FollowUpAnalysis(
            followup_required=True,
            followup_reason=reason,
            followup_priority=priority,
            missing_dimensions=missing_dimensions,
            followup_type=probe_type or PROBE_TYPE_BY_REASON[reason],
            semantic_topic=semantic_topic,
            contradiction_text=contradiction_text,
        )

    @staticmethod
    def _normalize(value: Any) -> str:
        return " ".join(str(value or "").strip().split())

    @staticmethod
    def _question_requests_example(question: str) -> bool:
        return any(signal in question for signal in ("example", "specific example", "describe a time", "walk me through"))

    @staticmethod
    def _question_requests_tradeoffs(question: str) -> bool:
        return any(signal in question for signal in ("trade-off", "tradeoff", "trade off", "alternatives", "pros and cons"))

    @staticmethod
    def _is_behavioral_question(question: str) -> bool:
        return any(
            signal in question
            for signal in ("describe a time", "tell me about", "communicat", "stakeholder", "team", "conflict", "risk", "challenge")
        )

    @staticmethod
    def _is_technical_question(question: str) -> bool:
        tokens = set(TOKEN_PATTERN.findall(question.lower()))
        return bool(tokens & TECHNICAL_SIGNALS)

    @staticmethod
    def _has_example(answer: str) -> bool:
        return any(signal in answer for signal in EXAMPLE_SIGNALS) or len(answer.split()) >= 25

    @staticmethod
    def _has_tradeoffs(answer: str) -> bool:
        return any(signal in answer for signal in TRADEOFF_SIGNALS)

    @staticmethod
    def _unsupported_claim(answer: str) -> bool:
        has_claim = any(signal in answer for signal in UNSUPPORTED_CLAIM_SIGNALS)
        has_support = any(char.isdigit() for char in answer) or "because" in answer or "measured" in answer
        return has_claim and not has_support

    @staticmethod
    def _low_confidence(answer: str, communication_score: int) -> bool:
        return communication_score < 8 or any(signal in answer for signal in HEDGING_SIGNALS)

    @staticmethod
    def _is_vague(answer: str) -> bool:
        if len(answer.split()) < 8:
            return True
        return any(phrase in answer for phrase in GENERIC_PHRASES)

    @staticmethod
    def _extract_topic(question: str, answer: str) -> str:
        tokens: list[str] = []
        for token in TOKEN_PATTERN.findall(f"{question} {answer}".lower()):
            if len(token) < 4:
                continue
            if token in {"about", "their", "there", "which", "would", "could", "should", "using", "with"}:
                continue
            if token not in tokens:
                tokens.append(token)
            if len(tokens) == 3:
                break
        return " ".join(tokens) if tokens else "general"
