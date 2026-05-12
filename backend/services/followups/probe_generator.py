from __future__ import annotations

import re
from dataclasses import dataclass

from backend.services.followups.followup_classifier import FollowUpAnalysis
from backend.services.followups.semantic_probe_tracker import SemanticProbeTracker


QUESTION_CLEANUP = re.compile(r"^(tell me about|describe|can you|could you|would you|walk me through)\s+", re.IGNORECASE)
STOPWORDS = {"the", "a", "an", "and", "or", "to", "of", "in", "that", "this", "your", "you", "about"}

FOLLOWUP_TEMPLATES = {
    "clarification_probe": [
        "Can you clarify what you mean by {anchor} and walk me through your reasoning?",
        "I'd like a more specific explanation there. What exactly happened, and why?",
    ],
    "technical_probe": [
        "What technical reasoning was behind {anchor}?",
        "How did the implementation actually work around {anchor}?",
    ],
    "example_probe": [
        "Could you walk me through a specific example from your experience related to {anchor}?",
        "What was one concrete situation where {anchor} came up, and what did you do?",
    ],
    "contradiction_probe": [
        "{challenge} Can you clarify?",
        "{challenge} Help me understand which approach was actually used.",
    ],
    "tradeoff_probe": [
        "What trade-offs did you consider when making that decision about {anchor}?",
        "What downside did you knowingly accept there, and why was it acceptable at the time?",
        "What alternatives were on the table, and what made you reject them?",
    ],
    "scalability_probe": [
        "How would that approach behave under higher scale or load?",
        "If traffic increased significantly, what would break first in that design?",
    ],
    "implementation_probe": [
        "What evidence or implementation detail supports that claim about {anchor}?",
        "How did you validate that result in practice?",
    ],
    "behavioral_probe": [
        "What was your role in that situation, and how did you communicate it?",
        "How did you handle that situation in practice, step by step?",
    ],
}


@dataclass(slots=True)
class GeneratedProbe:
    question: str
    probe_type: str
    probe_depth: int
    semantic_repeat_score: float
    repeat_count: int
    semantic_topic: str
    termination_reason: str = ""


class ProbeGenerator:
    def __init__(self, *, max_semantic_repeats: int = 2) -> None:
        self._max_semantic_repeats = max_semantic_repeats

    def generate(
        self,
        *,
        analysis: FollowUpAnalysis,
        original_question: str,
        candidate_answer: str,
        probe_depth: int,
        root_question_id: str,
        tracker: SemanticProbeTracker,
    ) -> GeneratedProbe:
        probe_type = analysis.followup_type or "clarification_probe"
        anchor = self._extract_anchor(original_question, candidate_answer, analysis.semantic_topic)
        challenge = analysis.contradiction_text or "You gave two different descriptions"
        templates = FOLLOWUP_TEMPLATES.get(probe_type, FOLLOWUP_TEMPLATES["clarification_probe"])

        best_question = ""
        best_similarity = 1.0
        best_repeats = self._max_semantic_repeats + 1

        for index, template in enumerate(templates):
            rendered = template.format(anchor=anchor, challenge=challenge).strip()
            similarity = tracker.max_similarity(
                question=rendered,
                probe_type=probe_type,
                root_question_id=root_question_id,
            )
            repeats = tracker.repeat_count(
                question=rendered,
                probe_type=probe_type,
                root_question_id=root_question_id,
            )
            if repeats < self._max_semantic_repeats and similarity < SemanticProbeTracker.THRESHOLD:
                return GeneratedProbe(
                    question=self._ensure_question(rendered),
                    probe_type=probe_type,
                    probe_depth=probe_depth,
                    semantic_repeat_score=similarity,
                    repeat_count=repeats,
                    semantic_topic=analysis.semantic_topic,
                )
            if index == 0 or similarity < best_similarity:
                best_question = rendered
                best_similarity = similarity
                best_repeats = repeats

        return GeneratedProbe(
            question=self._ensure_question(best_question or templates[0].format(anchor=anchor, challenge=challenge)),
            probe_type=probe_type,
            probe_depth=probe_depth,
            semantic_repeat_score=best_similarity,
            repeat_count=best_repeats,
            semantic_topic=analysis.semantic_topic,
            termination_reason="semantic_repetition_limit" if best_repeats >= self._max_semantic_repeats else "",
        )

    @staticmethod
    def _extract_anchor(question: str, answer: str, semantic_topic: str) -> str:
        normalized_answer = " ".join(answer.strip().split())
        if len(normalized_answer.split()) >= 6:
            sentence = normalized_answer.split(".")[0].strip()
            if sentence:
                return sentence[:90]

        stripped_question = QUESTION_CLEANUP.sub("", question.strip())
        stripped_question = stripped_question.rstrip("?")
        if stripped_question:
            return stripped_question[:90]

        cleaned_topic = " ".join(
            token for token in semantic_topic.split()
            if token.lower() not in STOPWORDS
        )
        return cleaned_topic or "that situation"

    @staticmethod
    def _ensure_question(text: str) -> str:
        cleaned = " ".join((text or "").strip().split())
        if cleaned and not cleaned.endswith("?"):
            cleaned = f"{cleaned.rstrip('.')}?"
        return cleaned
