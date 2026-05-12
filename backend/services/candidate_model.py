from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


TOKEN_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9_+#.-]{2,}")
STOPWORDS = {
    "about",
    "after",
    "also",
    "been",
    "being",
    "built",
    "could",
    "from",
    "have",
    "into",
    "just",
    "like",
    "made",
    "over",
    "really",
    "that",
    "their",
    "there",
    "they",
    "this",
    "through",
    "using",
    "very",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
    "your",
}
TECHNICAL_TOPICS = {"technical_skills", "problem_solving"}
TOPIC_ORDER = (
    "background",
    "behavioural",
    "technical_skills",
    "problem_solving",
    "culture_fit",
)
CLAIM_PATTERNS = {
    "caching": re.compile(r"\b(cache|caching|cached)\b", re.IGNORECASE),
    "synchronous flow": re.compile(r"\b(sync|synchronous|blocking)\b", re.IGNORECASE),
    "asynchronous flow": re.compile(
        r"\b(async|asynchronous|non-blocking|queue|event[- ]driven)\b",
        re.IGNORECASE,
    ),
    "microservices": re.compile(r"\b(microservice|microservices|service mesh)\b", re.IGNORECASE),
    "monolith": re.compile(r"\b(monolith|monolithic)\b", re.IGNORECASE),
    "sql database": re.compile(r"\b(sql|postgres|mysql|relational)\b", re.IGNORECASE),
    "nosql database": re.compile(r"\b(nosql|dynamodb|mongodb|document db)\b", re.IGNORECASE),
}
CONTRADICTION_PAIRS = (
    ("caching", "synchronous flow"),
    ("synchronous flow", "asynchronous flow"),
    ("microservices", "monolith"),
    ("sql database", "nosql database"),
)


@dataclass(slots=True)
class CandidateModel:
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    mentioned_topics: list[str] = field(default_factory=list)
    depth_map: dict[str, float] = field(default_factory=dict)
    contradictions: list[str] = field(default_factory=list)
    communication_score_trend: list[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Any) -> CandidateModel:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            strengths=cls._normalize_list(payload.get("strengths")),
            weaknesses=cls._normalize_list(payload.get("weaknesses")),
            mentioned_topics=cls._normalize_list(payload.get("mentioned_topics")),
            depth_map=cls._normalize_depth_map(payload.get("depth_map")),
            contradictions=cls._normalize_list(payload.get("contradictions")),
            communication_score_trend=cls._normalize_float_list(
                payload.get("communication_score_trend")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def update_from_evaluation(
        self,
        *,
        question: str,
        answer: str,
        topic: str,
        technical_score: int | None,
        depth_score: int | None,
        communication_score: int | None,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        normalized_topic = self._normalize_topic(topic)
        keywords = self._extract_keywords(answer)
        for keyword in keywords:
            self._append_unique(self.mentioned_topics, keyword, limit=40)

        if technical_score is not None and technical_score > 18:
            self._append_unique(self.strengths, normalized_topic or "technical_skills")
        if communication_score is not None and communication_score >= 12:
            self._append_unique(self.strengths, "communication")

        if depth_score is not None:
            if normalized_topic:
                self.depth_map[normalized_topic] = float(depth_score)
            if depth_score < 12:
                weakness = "technical_depth" if normalized_topic in TECHNICAL_TOPICS else normalized_topic
                self._append_unique(self.weaknesses, weakness or "depth")

        if communication_score is not None:
            self.communication_score_trend = (
                self.communication_score_trend + [float(communication_score)]
            )[-12:]

        contradiction = self._detect_contradiction(
            answer=answer,
            history=history or [],
        )
        if contradiction:
            self._append_unique(self.contradictions, contradiction, limit=12)

        return {
            "topic": normalized_topic,
            "keywords": keywords,
            "contradiction": contradiction,
            "recommended_difficulty": self.recommended_difficulty(),
        }

    def choose_topic(self, requested_topic: str, covered_topics: list[str]) -> str:
        normalized_topic = self._normalize_topic(requested_topic)
        normalized_covered = [self._normalize_topic(topic) for topic in covered_topics if topic]

        if "technical_depth" in self.weaknesses:
            return "technical_skills"

        avoid_topics = set(self.strong_topics_to_avoid())
        if normalized_topic not in avoid_topics:
            return normalized_topic

        for topic in TOPIC_ORDER:
            if topic not in normalized_covered and topic not in avoid_topics:
                return topic

        for topic in TOPIC_ORDER:
            if topic != normalized_topic and topic not in avoid_topics:
                return topic

        return normalized_topic

    def strong_topics_to_avoid(self) -> list[str]:
        strong_topics: list[str] = []
        for strength in self.strengths:
            normalized = self._normalize_topic(strength)
            if normalized and normalized != "communication" and self.depth_map.get(normalized, 0.0) >= 18.0:
                self._append_unique(strong_topics, normalized)
        return strong_topics

    def recommended_difficulty(self) -> str:
        recent = self.communication_score_trend[-3:]
        if "communication" in self.strengths and recent and sum(recent) / len(recent) >= 11.0:
            return "harder"
        if len(recent) >= 2 and recent[-1] >= recent[0] and sum(recent) / len(recent) >= 11.0:
            return "harder"
        if "technical_depth" in self.weaknesses:
            return "probe"
        if len(recent) >= 2 and recent[-1] <= recent[0] and sum(recent) / len(recent) <= 7.0:
            return "easier"
        return "normal"

    def latest_contradiction(self) -> str | None:
        return self.contradictions[-1] if self.contradictions else None

    def _detect_contradiction(
        self,
        *,
        answer: str,
        history: list[dict[str, Any]],
    ) -> str | None:
        current_claims = self._extract_claims(answer)
        if not current_claims:
            return None

        for entry in reversed(history[-6:]):
            previous_answer = self._normalize_text(entry.get("answer", ""))
            if not previous_answer:
                continue
            previous_claims = self._extract_claims(previous_answer)
            for left, right in CONTRADICTION_PAIRS:
                if left in previous_claims and right in current_claims:
                    return f"You mentioned earlier that {left} was used, but now you're describing {right}."
                if right in previous_claims and left in current_claims:
                    return f"You mentioned earlier that {right} was used, but now you're describing {left}."
        return None

    @staticmethod
    def _extract_claims(text: str) -> set[str]:
        claims: set[str] = set()
        for label, pattern in CLAIM_PATTERNS.items():
            if pattern.search(text or ""):
                claims.add(label)
        return claims

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        keywords: list[str] = []
        for token in TOKEN_PATTERN.findall(text or ""):
            normalized = token.lower()
            if normalized in STOPWORDS:
                continue
            if normalized not in keywords:
                keywords.append(normalized)
            if len(keywords) >= 12:
                break
        return keywords

    @staticmethod
    def _normalize_topic(value: Any) -> str:
        topic = CandidateModel._normalize_text(value).lower().replace("behavioral", "behavioural")
        if topic == "wrapup":
            return "culture_fit"
        return topic

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return " ".join(str(value or "").strip().split())

    @staticmethod
    def _normalize_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in (CandidateModel._normalize_text(entry) for entry in value) if item]

    @staticmethod
    def _normalize_depth_map(value: Any) -> dict[str, float]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, float] = {}
        for key, raw in value.items():
            topic = CandidateModel._normalize_topic(key)
            try:
                result[topic] = float(raw)
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _normalize_float_list(value: Any) -> list[float]:
        if not isinstance(value, list):
            return []
        normalized: list[float] = []
        for entry in value:
            try:
                normalized.append(float(entry))
            except (TypeError, ValueError):
                continue
        return normalized[-12:]

    @staticmethod
    def _append_unique(items: list[str], value: str, *, limit: int = 10) -> None:
        normalized = CandidateModel._normalize_text(value)
        if not normalized or normalized in items:
            return
        items.append(normalized)
        if len(items) > limit:
            del items[:-limit]
