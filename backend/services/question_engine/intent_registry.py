'''Intent registry for question intents.

This module defines the taxonomy of interview question intents, provides a static registry
of intents, and offers helper functions for lookup and simple classification.

It has **no external dependencies** beyond the standard library and *pydantic* for the
BaseModel definitions.
'''  # noqa: D400

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Set, Dict

from pydantic import BaseModel


class IntentCategory(str, Enum):
    SYSTEM_DESIGN = "system_design"
    DEBUGGING = "debugging"
    BEHAVIORAL = "behavioral"
    ARCHITECTURE = "architecture"
    COMMUNICATION = "communication"
    SCALABILITY = "scalability"
    TRADEOFFS = "tradeoffs"
    LEADERSHIP = "leadership"


class QuestionIntent(BaseModel):
    id: str
    category: IntentCategory
    description: str
    semantic_signals: List[str]
    mutually_exclusive_with: List[str] = []


class QuestionRecord(BaseModel):
    question: str
    topic: str
    intent: str  # refers to QuestionIntent.id
    difficulty: str  # "easy" | "medium" | "hard"
    followup_to: Optional[str] = None


# ---------------------------------------------------------------------------
# Intent registry – single source of truth for all intents used by the platform.
# ---------------------------------------------------------------------------
INTENT_REGISTRY: Dict[str, QuestionIntent] = {
    "system_tradeoffs": QuestionIntent(
        id="system_tradeoffs",
        category=IntentCategory.TRADEOFFS,
        description="Tests ability to reason about competing system constraints",
        semantic_signals=[
            "trade-off",
            "tradeoff",
            "cost",
            "vs",
            "versus",
            "chose",
            "decision",
            "compromise",
            "balance",
        ],
        mutually_exclusive_with=["architecture_decision"],
    ),
    "debugging_strategy": QuestionIntent(
        id="debugging_strategy",
        category=IntentCategory.DEBUGGING,
        description="Tests systematic approach to finding and fixing problems",
        semantic_signals=[
            "debug",
            "diagnose",
            "root cause",
            "investigate",
            "trace",
            "reproduce",
            "isolate",
            "fix",
        ],
        mutually_exclusive_with=[],
    ),
    "architecture_decision": QuestionIntent(
        id="architecture_decision",
        category=IntentCategory.ARCHITECTURE,
        description="Tests reasoning behind structural system choices",
        semantic_signals=[
            "design",
            "architecture",
            "structure",
            "pattern",
            "chose",
            "selected",
            "approach",
            "framework",
        ],
        mutually_exclusive_with=["system_tradeoffs"],
    ),
    "scalability_reasoning": QuestionIntent(
        id="scalability_reasoning",
        category=IntentCategory.SCALABILITY,
        description="Tests understanding of scale challenges and solutions",
        semantic_signals=[
            "scale",
            "scalability",
            "load",
            "throughput",
            "horizontal",
            "vertical",
            "distributed",
            "performance",
        ],
        mutually_exclusive_with=[],
    ),
    "team_conflict": QuestionIntent(
        id="team_conflict",
        category=IntentCategory.BEHAVIORAL,
        description="Tests interpersonal conflict navigation",
        semantic_signals=[
            "disagree",
            "conflict",
            "tension",
            "team",
            "colleague",
            "pushback",
            "convince",
            "difficult person",
        ],
        mutually_exclusive_with=["communication_style"],
    ),
    "communication_style": QuestionIntent(
        id="communication_style",
        category=IntentCategory.COMMUNICATION,
        description="Tests clarity of communication with different audiences",
        semantic_signals=[
            "explain",
            "communicate",
            "present",
            "stakeholder",
            "non-technical",
            "audience",
            "simplify",
        ],
        mutually_exclusive_with=["team_conflict"],
    ),
    "failure_handling": QuestionIntent(
        id="failure_handling",
        category=IntentCategory.BEHAVIORAL,
        description="Tests response to failure and learning from mistakes",
        semantic_signals=[
            "fail",
            "mistake",
            "wrong",
            "incident",
            "outage",
            "postmortem",
            "lesson",
            "recover",
        ],
        mutually_exclusive_with=[],
    ),
    "ownership_initiative": QuestionIntent(
        id="ownership_initiative",
        category=IntentCategory.LEADERSHIP,
        description="Tests proactive ownership and independent action",
        semantic_signals=[
            "initiative",
            "owned",
            "drove",
            "led",
            "without being asked",
            "proactive",
            "ownership",
        ],
        mutually_exclusive_with=[],
    ),
    "system_complexity": QuestionIntent(
        id="system_complexity",
        category=IntentCategory.SYSTEM_DESIGN,
        description="Tests ability to manage and reason about complex systems",
        semantic_signals=[
            "complex",
            "complexity",
            "manage",
            "maintain",
            "legacy",
            "dependency",
            "coupling",
            "abstraction",
        ],
        mutually_exclusive_with=[],
    ),
    "prioritization": QuestionIntent(
        id="prioritization",
        category=IntentCategory.LEADERSHIP,
        description="Tests decision-making under constraints and competing priorities",
        semantic_signals=[
            "prioritize",
            "prioritization",
            "deadline",
            "constraint",
            "cut scope",
            "focus",
            "important",
            "urgent",
        ],
        mutually_exclusive_with=[],
    ),
}


def get_intent(intent_id: str) -> Optional[QuestionIntent]:
    """Return the intent object for *intent_id* if it exists, otherwise ``None``."""
    return INTENT_REGISTRY.get(intent_id)


def classify_question_intent(question: str) -> str:
    """Return the most likely intent identifier for *question*.

    The algorithm scans the ``semantic_signals`` of each intent and counts how many
    signals appear as substrings in the lower‑cased question. The intent with the highest
    count wins. If no signals match, the function falls back to ``"system_tradeoffs"``
    as a safe default.
    """
    q = question.lower()
    scores = {
        iid: sum(1 for s in intent.semantic_signals if s in q)
        for iid, intent in INTENT_REGISTRY.items()
    }
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "system_tradeoffs"


def get_covered_categories(intent_ids: list[str]) -> set[IntentCategory]:
    return {
        INTENT_REGISTRY[i].category
        for i in intent_ids if i in INTENT_REGISTRY
    }


def get_uncovered_categories(intent_ids: list[str]) -> list[IntentCategory]:
    covered = get_covered_categories(intent_ids)
    return [c for c in IntentCategory if c not in covered]


def are_intents_exclusive(a: str, b: str) -> bool:
    intent = INTENT_REGISTRY.get(a)
    return bool(intent and b in intent.mutually_exclusive_with)


__all__ = [
    "IntentCategory",
    "QuestionIntent",
    "QuestionRecord",
    "INTENT_REGISTRY",
    "get_intent",
    "classify_question_intent",
    "get_covered_categories",
    "get_uncovered_categories",
    "are_intents_exclusive",
]
