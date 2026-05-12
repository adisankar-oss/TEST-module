"""Semantic deduplication utilities for question generation.

This module provides tokenisation, Jaccard similarity, intent overlap scoring, and a combined similarity metric that can be used to decide whether a newly generated question is too similar to an existing one.
"""

from intent_registry import (
    QuestionRecord,
    are_intents_exclusive,
    INTENT_REGISTRY,
    get_uncovered_categories,
    IntentCategory,
)

STOPWORDS = {
    "a",
    "an",
    "the",
    "you",
    "your",
    "how",
    "what",
    "why",
    "when",
    "did",
    "do",
    "does",
    "have",
    "has",
    "been",
    "was",
    "were",
    "tell",
    "me",
    "about",
    "can",
    "could",
    "would",
    "please",
    "describe",
    "explain",
    "give",
    "in",
    "of",
    "to",
    "and",
    "or",
    "with",
    "for",
    "that",
    "this",
    "at",
    "on",
}

def tokenize(text: str) -> set[str]:
    return {w for w in text.lower().split() if w not in STOPWORDS and len(w) > 2}

def jaccard_similarity(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def intent_overlap_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if are_intents_exclusive(a, b):
        return 0.0
    ia, ib = INTENT_REGISTRY.get(a), INTENT_REGISTRY.get(b)
    if ia and ib and ia.category == ib.category:
        return 0.4
    return 0.1

def combined_similarity(q: str, intent: str, existing: QuestionRecord) -> float:
    return round(
        0.5 * jaccard_similarity(q, existing.question) +
        0.5 * intent_overlap_score(intent, existing.intent),
        4,
    )

# ---------------------------------------------------------------------------
# Question deduplication class
# ---------------------------------------------------------------------------
from typing import Optional

class QuestionDeduplicator:
    THRESHOLD = 0.38

    def __init__(self):
        self._asked: list[QuestionRecord] = []
        self._asked_intents: list[str] = []

    def is_duplicate(self, question: str, intent: str) -> tuple[bool, float]:
        if not self._asked:
            return False, 0.0
        sims = [combined_similarity(question, intent, r) for r in self._asked]
        mx = max(sims)
        return mx >= self.THRESHOLD, mx

    def register(self, record: QuestionRecord) -> None:
        self._asked.append(record)
        self._asked_intents.append(record.intent)

    def get_asked_intents(self) -> list[str]:
        return list(self._asked_intents)

    def get_asked_records(self) -> list[QuestionRecord]:
        return list(self._asked)

    def suggest_intent_gap(self) -> Optional[str]:
        uncovered = get_uncovered_categories(self._asked_intents)
        if not uncovered:
            return None
        for intent_id, intent in INTENT_REGISTRY.items():
            if intent.category == uncovered[0] and intent_id not in self._asked_intents:
                return intent_id
        return None

    def to_dict(self) -> dict:
        return {
            "asked": [r.dict() for r in self._asked],
            "asked_intents": self._asked_intents,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QuestionDeduplicator":
        inst = cls()
        inst._asked = [QuestionRecord(**r) for r in data.get("asked", [])]
        inst._asked_intents = data.get("asked_intents", [])
        return inst
