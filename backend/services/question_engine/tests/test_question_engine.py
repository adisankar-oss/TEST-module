import pytest
from semantic_dedup import QuestionDeduplicator
from intent_registry import QuestionRecord, INTENT_REGISTRY, IntentCategory
from followup_generator import FollowUpGenerator, extract_answer_anchor
from backend.services.interview.candidate_memory import CandidateMemory
from backend.services.evaluation.evaluation_models import (
    EvaluationResult, DimensionScores, FollowUpSignal
)

def build_eval(depth, technical, clarity, confidence, relevance) -> EvaluationResult:
    return EvaluationResult(
        score=int(relevance * .30 + depth * .25 + clarity * .20 + technical * .15 + confidence * .10),
        dimension_scores=DimensionScores(
            relevance=relevance,
            depth=depth,
            clarity=clarity,
            technical=technical,
            confidence=confidence,
        ),
        strengths=[],
        weaknesses=[],
        feedback="test",
        followup=FollowUpSignal(required=False, reason=None),
        difficulty_recommendation="same",
        evaluation_confidence=0.8,
    )

def make_record(question, intent) -> QuestionRecord:
    return QuestionRecord(question=question, topic="test", intent=intent, difficulty="medium")

# Test 1
def test_same_intent_flagged_as_duplicate():
    d = QuestionDeduplicator()
    d.register(make_record("How did you handle trade-offs in your last system design?", "system_tradeoffs"))
    is_dup, sim = d.is_duplicate(
        "Describe a trade-off you faced when designing a system.", "system_tradeoffs"
    )
    assert is_dup and sim >= 0.38

# Test 2
def test_different_intent_not_duplicate():
    d = QuestionDeduplicator()
    d.register(make_record("Tell me about a production incident.", "debugging_strategy"))
    is_dup, _ = d.is_duplicate(
        "How do you prioritize work when deadlines conflict?", "prioritization"
    )
    assert not is_dup

# Test 3
def test_intent_gap_suggests_uncovered_category():
    d = QuestionDeduplicator()
    for intent in ["team_conflict", "failure_handling", "ownership_initiative"]:
        d.register(make_record(f"question {intent}", intent))
    gap = d.suggest_intent_gap()
    assert gap is not None
    gap_cat = INTENT_REGISTRY[gap].category
    assert gap_cat not in {IntentCategory.BEHAVIORAL, IntentCategory.LEADERSHIP}

# Test 4
def test_anchor_prefers_technical_content():
    answer = (
        "We basically tried a few things. "
        "We used Redis with a Lua script to atomically decrement the counter, "
        "which reduced race conditions by 95%. It worked okay."
    )
    anchor = extract_answer_anchor(answer)
    assert any(w in anchor.lower() for w in ["redis", "lua", "counter", "atomic"])
    assert len(anchor) <= 100

# Test 5
def test_followup_references_answer_content():
    memory = CandidateMemory(session_id="fu-001")
    ev = build_eval(depth=35, technical=30, clarity=40, confidence=35, relevance=45)
    answer = "I used Redis with a Lua script to manage the token bucket atomically."
    memory.update("How would you design a rate limiter?", "system_tradeoffs", answer, ev)
    record = FollowUpGenerator(llm_client=None).generate(
        answer=answer, evaluation=ev, memory=memory,
        previous_question="How would you design a rate limiter?",
    )
    assert len(record.question) > 20
    assert any(w in record.question.lower() for w in ["redis", "lua", "token", "counter", "mentioned", "rate"])

# Test 6
def test_contradiction_generates_challenge():
    memory = CandidateMemory(session_id="fu-002")
    ev = build_eval(55, 50, 55, 50, 55)
    memory.update("Q1", "architecture_decision",
        "I always prefer simple architectures over complex distributed systems.", ev)
    memory.update("Q2", "scalability_reasoning",
        "I prioritize scalability regardless of complexity.", ev)
    record = FollowUpGenerator(llm_client=None).generate(
        answer="I prioritize scalability regardless of complexity.",
        evaluation=ev, memory=memory,
        previous_question="How do you approach scalability?",
    )
    assert any(w in record.question.lower() for w in ["earlier", "said", "mentioned", "simple", "clarify"])

# Test 7
def test_deduplicator_serialization():
    d = QuestionDeduplicator()
    d.register(make_record("Tell me about a trade-off.", "system_tradeoffs"))
    d.register(make_record("How did you debug a production issue?", "debugging_strategy"))
    restored = QuestionDeduplicator.from_dict(d.to_dict())
    assert len(restored.get_asked_records()) == 2
    is_dup, _ = restored.is_duplicate("Describe a trade-off in design.", "system_tradeoffs")
    assert is_dup
