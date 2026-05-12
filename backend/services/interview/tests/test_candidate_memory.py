import pytest
from backend.services.interview.candidate_memory import CandidateMemory
from backend.services.evaluation.evaluation_models import (
    EvaluationResult,
    DimensionScores,
    FollowUpSignal,
)

# Helper to build an EvaluationResult
def build_eval(*, depth, technical, clarity, confidence, relevance):
    dims = DimensionScores(
        relevance=relevance,
        depth=depth,
        clarity=clarity,
        technical=technical,
        confidence=confidence,
    )
    # Compute weighted score (same weights as final scorer)
    score = int(
        round(
            relevance * 0.30
            + depth * 0.25
            + clarity * 0.20
            + technical * 0.15
            + confidence * 0.10
        )
    )
    return EvaluationResult(
        score=score,
        dimension_scores=dims,
        strengths=[],
        weaknesses=[],
        feedback="test feedback",
        followup=FollowUpSignal(required=False, reason=None),
        difficulty_recommendation="same",
        evaluation_confidence=0.8,
    )

# Shared question and intent
q = "How would you design a rate limiter for a distributed system?"
intent = "system_design_rate_limiting"

def test_weak_candidate_accumulates_weaknesses():
    memory = CandidateMemory(session_id="test-001")
    weak_eval = build_eval(depth=30, technical=25, clarity=35, confidence=30, relevance=40)
    answer = "I would just limit requests somehow."
    for _ in range(3):
        memory.update(q, intent, answer, weak_eval)
    # Weakness with dimension "depth" should have occurrences >= 3
    depth_weak = next((w for w in memory.state.weaknesses if w.dimension == "depth"), None)
    assert depth_weak is not None
    assert depth_weak.occurrences >= 3
    # Priority should be escalated to 3
    assert depth_weak.followup_priority == 3
    # Summary weakest domain should not be the placeholder
    assert memory.state.summary is not None
    assert memory.state.summary.weakest_domain != "none identified"

def test_strong_candidate_accumulates_strengths_and_shifts_difficulty():
    memory = CandidateMemory(session_id="test-002")
    strong_eval = build_eval(depth=80, technical=75, clarity=78, confidence=72, relevance=82)
    answer = "I'd use a token bucket with Redis atomic Lua scripts..."
    for _ in range(3):
        memory.update(q, intent, answer, strong_eval)
    ctx = memory.get_strategy_context()
    assert ctx["recommended_difficulty_shift"] == "increase"
    assert len(ctx["confirmed_strengths"]) >= 1
    # All depth scores are equal, so trajectory should be stable
    assert ctx["depth_trend"] == "stable"

def test_contradiction_detected():
    memory = CandidateMemory(session_id="test-003")
    eval_a = build_eval(depth=55, technical=50, clarity=55, confidence=50, relevance=55)
    memory.update(q, intent, "I always prefer simple architectures over complex ones.", eval_a)
    # Second answer contains the opposite term "complex"
    memory.update(q, intent, "I prioritize scalability regardless of complex needs.", eval_a)
    assert len(memory.state.contradictions) >= 1
    assert memory.state.contradictions[0].resolved is False
    ctx = memory.get_strategy_context()
    assert ctx["has_unresolved_contradictions"] is True
    assert ctx["contradiction_challenge"] is not None

def test_topic_repetition_avoided():
    memory = CandidateMemory(session_id="test-004")
    eval_a = build_eval(depth=60, technical=55, clarity=60, confidence=55, relevance=60)
    memory.update(q, intent, "answer one", eval_a)
    memory.update(q, intent, "answer two", eval_a)  # same intent repeats
    ctx = memory.get_strategy_context()
    assert intent in ctx["topics_to_avoid"]

def test_serialization_round_trip():
    memory = CandidateMemory(session_id="test-005")
    eval_a = build_eval(depth=60, technical=55, clarity=60, confidence=55, relevance=60)
    memory.update(q, intent, "some answer", eval_a)
    serialized = memory.to_dict()
    restored = CandidateMemory.from_dict(serialized)
    assert restored.state.session_id == "test-005"
    assert restored.state.question_count == memory.state.question_count
    assert len(restored.state.topics_seen) == len(memory.state.topics_seen)

def test_improving_trajectory_detection():
    memory = CandidateMemory(session_id="test-006")
    evals = [
        build_eval(depth=30, technical=30, clarity=35, confidence=30, relevance=35),
        build_eval(depth=50, technical=48, clarity=52, confidence=45, relevance=50),
        build_eval(depth=72, technical=68, clarity=70, confidence=65, relevance=72),
    ]
    answers = ["weak answer", "better answer with some reasoning", "strong answer with trade-offs and specific tools like Redis"]
    for i, (e, a) in enumerate(zip(evals, answers)):
        memory.update(q, f"{intent}_{i}", a, e)
    assert memory.state.summary is not None
    assert memory.state.summary.overall_trajectory == "improving"
    # Communication quality should be adequate or strong based on recent clarity scores
    assert memory.state.summary.communication_quality in ("adequate", "strong")
