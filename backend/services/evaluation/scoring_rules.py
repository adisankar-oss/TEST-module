import re
from typing import List, Optional

from .evaluation_models import AnswerLength, DimensionScores

# --- LENGTH CLASSIFIER ---

def classify_length(answer: str) -> AnswerLength:
    """Classify answer length into AnswerLength enum.
    Word count thresholds:
      <20 -> VERY_SHORT
      20-50 -> SHORT
      51-350 -> GOOD
      >350 -> TOO_LONG
    """
    words = re.findall(r"\b\w+\b", answer)
    count = len(words)
    if count < 20:
        return AnswerLength.VERY_SHORT
    if 20 <= count <= 50:
        return AnswerLength.SHORT
    if count > 350:
        return AnswerLength.TOO_LONG
    return AnswerLength.GOOD

# --- GENERIC PHRASE DETECTOR ---
GENERIC_PHRASES = [
    "something like",
    "basically",
    "I just",
    "kind of",
    "somehow",
    "I think maybe",
    "I guess",
    "sort of",
    "you know",
    "stuff like that",
    "and so on",
    "etc",
    "things like that",
    "generally speaking",
]

def detect_generic_phrases(answer: str) -> List[str]:
    """Return list of generic phrases found in *answer* (case‑insensitive)."""
    lower = answer.lower()
    matches: List[str] = []
    for phrase in GENERIC_PHRASES:
        if phrase.lower() in lower:
            matches.append(phrase)
    return matches

def generic_phrase_penalty(matched: List[str]) -> int:
    """Penalty based on number of generic phrases – 5 points each, capped at 25."""
    return min(len(matched) * 5, 25)

# --- DIMENSION SCORERS ---

def _token_set(text: str) -> set:
    return set(re.findall(r"\b\w+\b", text.lower()))

def score_relevance(answer: str, question: str) -> int:
    """Simple token overlap based relevance score (0‑100)."""
    q_tokens = _token_set(question)
    a_tokens = _token_set(answer)
    if not q_tokens:
        return 0
    overlap = len(q_tokens & a_tokens)
    score = int((overlap / len(q_tokens)) * 100)
    return max(0, min(score, 100))

def score_depth(answer: str) -> int:
    """Depth scoring based on presence of reasoning cues and penalties.
    Returns 0‑100.
    """
    depth_cues = [
        "however",
        "on the other hand",
        "but",
        "because",
        "therefore",
        "which means",
        "at scale",
        "in production",
        "failure mode",
        "for example",
        "in our case",
        "when I worked on",
        "first",
        "then",
        "finally",
    ]
    lower = answer.lower()
    score = 0
    for cue in depth_cues:
        if cue in lower:
            score += 20
    score = min(score, 100)
    # Penalize very short answers
    if classify_length(answer) == AnswerLength.VERY_SHORT:
        score = max(score - 30, 0)
    # Penalize many generic phrases
    generic_matches = detect_generic_phrases(answer)
    if len(generic_matches) > 3:
        score = max(score - 20, 0)
    return score

def score_clarity(answer: str) -> int:
    """Clarity based on sentence length distribution and connectors.
    Returns 0‑100.
    """
    connectors = [
        "however",
        "therefore",
        "moreover",
        "consequently",
        "in addition",
        "additionally",
        "for example",
        "first",
        "then",
        "finally",
        "next",
        "subsequently",
    ]
    sentences = re.split(r"[.!?]+", answer)
    short_cnt = 0
    long_cnt = 0
    for s in sentences:
        words = re.findall(r"\b\w+\b", s)
        wlen = len(words)
        if wlen == 0:
            continue
        if wlen < 5:
            short_cnt += 1
        if wlen > 60:
            long_cnt += 1
    penalty = (short_cnt * 2) + (long_cnt * 2)
    connector_bonus = sum(1 for c in connectors if c in answer.lower()) * 2
    score = 100 - penalty + connector_bonus
    return max(0, min(score, 100))

# Technical vocabulary (approx. 60+ terms)
TECH_VOCAB = {
    "latency",
    "throughput",
    "sharding",
    "indexing",
    "cache",
    "queue",
    "async",
    "asynchronous",
    "idempotent",
    "race condition",
    "deadlock",
    "horizontal scaling",
    "load balancer",
    "cap theorem",
    "eventual consistency",
    "microservice",
    "circuit breaker",
    "docker",
    "kubernetes",
    "cloud",
    "aws",
    "gcp",
    "azure",
    "api",
    "rest",
    "graphql",
    "authentication",
    "authorization",
    "jwt",
    "oauth",
    "session",
    "database",
    "sql",
    "nosql",
    "postgresql",
    "mysql",
    "redis",
    "mongodb",
    "elasticsearch",
    "monitoring",
    "logging",
    "tracing",
    "ci/cd",
    "helm",
    "terraform",
    "infra",
    "typescript",
    "python",
    "java",
    "go",
    "rust",
    "nodejs",
    "react",
    "frontend",
    "backend",
    "service mesh",
    "istio",
    "prometheus",
    "grafana",
    "slo",
    "sla",
    "rate limiting",
    "throttling",
    "load testing",
    "stress testing",
    "benchmark",
    "profiling",
    "gc",
    "memory leak",
    "thread",
    "process",
    "container",
    "orchestration",
    "deployment",
    "blue‑green",
    "canary",
    "rollback",
    "feature flag",
    "ab testing",
    "observability",
    "metric",
    "alert",
    "incident",
    "on‑call",
    "sRE",
    "devops",
}

def score_technical(answer: str, domain_keywords: List[str] = []) -> int:
    """Technical depth based on overlap with a technical vocabulary and optional domain keywords.
    Returns 0‑100.
    """
    tokens = _token_set(answer)
    overlap = len(tokens & TECH_VOCAB)
    base = int((overlap / max(1, len(TECH_VOCAB))) * 100)
    # domain keyword bonus – up to 10 points
    bonus = 0
    domain_set = set(k.lower() for k in domain_keywords)
    for kw in domain_set:
        if kw in tokens:
            bonus += 3
    bonus = min(bonus, 10)
    score = min(base + bonus, 100)
    return score

def score_confidence(answer: str) -> int:
    """Confidence scoring based on decisive language, specificity, and lack of hedging.
    Returns 0‑100.
    """
    decisive = [
        "i chose",
        "we decided",
        "the approach was",
        "i implemented",
        "i built",
        "i designed",
    ]
    hedges = [
        "but i'm not sure",
        "maybe",
        "i think",
        "i guess",
        "possibly",
        "perhaps",
    ]
    lower = answer.lower()
    score = 0
    # decisive cues
    for d in decisive:
        if d in lower:
            score += 15
    # specificity – numbers or percentages or tool names
    if re.search(r"\b\d+%?\b", answer):
        score += 10
    if re.search(r"\bpython\b|\bjava\b|\bgo\b|\bnodejs\b|\bdocker\b|\bkubernetes\b", lower):
        score += 5
    # penalize hedges
    for h in hedges:
        if h in lower:
            score -= 10
    score = max(0, min(score, 100))
    return score

# --- COMPOSITE SCORER ---

def compute_dimension_scores(answer: str, question: str, domain_keywords: List[str] = []) -> DimensionScores:
    """Compute all dimension scores and return a DimensionScores instance."""
    relevance = score_relevance(answer, question)
    depth = score_depth(answer)
    clarity = score_clarity(answer)
    technical = score_technical(answer, domain_keywords)
    confidence = score_confidence(answer)
    return DimensionScores(
        relevance=relevance,
        depth=depth,
        clarity=clarity,
        technical=technical,
        confidence=confidence,
    )

def compute_final_score(dimensions: DimensionScores, length: AnswerLength) -> int:
    """Weighted average of dimension scores with length‑based modifiers.
    Returns integer 0‑100.
    """
    weighted = (
        dimensions.relevance * 0.30
        + dimensions.depth * 0.25
        + dimensions.clarity * 0.20
        + dimensions.technical * 0.15
        + dimensions.confidence * 0.10
    )
    score = int(round(weighted))
    # Length modifiers
    if length == AnswerLength.VERY_SHORT:
        score = min(score, 40)
    elif length == AnswerLength.SHORT:
        score = min(score, 65)
    elif length == AnswerLength.TOO_LONG:
        score = max(score - 5, 0)
    # GOOD length has no change
    return max(0, min(score, 100))
