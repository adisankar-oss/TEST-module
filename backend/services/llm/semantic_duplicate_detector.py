from __future__ import annotations

import re
from difflib import SequenceMatcher


TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_+#.-]+")
STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "can",
    "could",
    "describe",
    "did",
    "do",
    "does",
    "explain",
    "for",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "tell",
    "that",
    "the",
    "this",
    "to",
    "walk",
    "what",
    "when",
    "why",
    "with",
    "would",
    "you",
    "your",
}


class SemanticDuplicateDetector:
    def similarity(self, left: str, right: str) -> float:
        normalized_left = self._normalize(left)
        normalized_right = self._normalize(right)
        if not normalized_left or not normalized_right:
            return 0.0
        token_overlap = self._token_overlap(normalized_left, normalized_right)
        sequence_overlap = SequenceMatcher(a=normalized_left, b=normalized_right).ratio()
        return round((token_overlap * 0.7) + (sequence_overlap * 0.3), 4)

    def is_duplicate(self, candidate: str, history: list[str], threshold: float) -> tuple[bool, float]:
        highest = 0.0
        for previous in history:
            score = self.similarity(candidate, previous)
            highest = max(highest, score)
            if score >= threshold:
                return True, score
        return False, highest

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(str(text or "").strip().lower().split())

    def _token_overlap(self, left: str, right: str) -> float:
        left_tokens = self._tokens(left)
        right_tokens = self._tokens(right)
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {
            token.lower()
            for token in TOKEN_PATTERN.findall(text)
            if len(token) > 2 and token.lower() not in STOPWORDS
        }
