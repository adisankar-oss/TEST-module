import pytest
from backend.services.evaluation import AnswerEvaluator, EvaluationResult
from backend.services.evaluation.evaluation_models import AnswerLength

# Shared question
QUESTION = "Describe how you would design a rate limiter for a distributed API gateway."

# Answers
weak_answer = "I would just limit the requests somehow."
medium_answer = "I would track requests per user and block them if they exceed a limit. Maybe use Redis to store counts."
strong_answer = (
    "I'd implement a token bucket algorithm using Redis with atomic Lua scripts to avoid race conditions. "
    "Each service node checks a shared counter with a TTL equal to the window size. "
    "For distributed consistency, I'd use sliding window logs for accuracy or fixed windows with a small over‑count tolerance for performance. "
    "The trade‑off is precision vs latency — sliding window is more accurate but heavier under high QPS."
)
vague_answer = (
    "It depends on the situation. There are many ways to do it. You kind of need to think about what works best basically."
)
technical_deep_answer = (
    "I'd use a Redis‑backed sliding window counter with Lua atomicity, expose rate‑limit headers (X‑RateLimit‑Remaining, Retry‑After), "
    "implement per‑tenant burst allowances using token bucket layered over the sliding window, and back‑pressure via circuit breakers at the API gateway layer. "
    "I'd shard the Redis keyspace by user_id hash to avoid hotspots at high throughput."
)
behavioral_answer = (
    "In my last job, we had issues with a vendor API getting hammered. I worked with the team to add request throttling. "
    "We used a simple counter in our database and it worked well enough for our scale at the time."
)

@pytest.fixture
def evaluator():
    return AnswerEvaluator()

def test_weak_answer(evaluator):
    result = evaluator.evaluate(QUESTION, weak_answer)
    assert result["score"] < 35
    assert result["followup"]["required"] is True

def test_medium_answer(evaluator):
    result = evaluator.evaluate(QUESTION, medium_answer)
    assert 35 <= result["score"] <= 60

def test_strong_answer(evaluator):
    result = evaluator.evaluate(QUESTION, strong_answer)
    assert result["score"] >= 70
    assert result["followup"]["required"] is False

def test_vague_answer(evaluator):
    result = evaluator.evaluate(QUESTION, vague_answer)
    assert result["score"] < 40
    assert result["followup"]["required"] is True

def test_technical_deep_answer(evaluator):
    result = evaluator.evaluate(QUESTION, technical_deep_answer)
    assert result["score"] >= 75
    dims = result["dimension_scores"]
    assert dims.technical >= 65

def test_behavioral_answer(evaluator):
    result = evaluator.evaluate(QUESTION, behavioral_answer)
    assert 40 <= result["score"] <= 65
    dims = result["dimension_scores"]
    assert dims.technical < 55

def test_score_ordering(evaluator):
    weak = evaluator.evaluate(QUESTION, weak_answer)["score"]
    medium = evaluator.evaluate(QUESTION, medium_answer)["score"]
    strong = evaluator.evaluate(QUESTION, strong_answer)["score"]
    assert strong - medium >= 15
    assert medium - weak >= 15
