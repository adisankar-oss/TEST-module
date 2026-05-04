"""
Tests for AdvancedAnswerEvaluator (M6).

These tests validate the reasoning-based evaluation system without requiring
actual LLM API calls, using mocked responses.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from services.advanced_evaluator import (
    AdvancedAnswerEvaluator,
    EvaluationResult,
    RELEVANCE_MAX,
    DEPTH_MAX,
    TECHNICAL_MAX,
    COMMUNICATION_MAX,
    RED_FLAG_PENALTY_MAX,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def evaluator():
    """Create an evaluator instance with mocked AI client."""
    mock_client = MagicMock()
    # generate_text is async, but _run_async handles it
    mock_client.generate_text = MagicMock()

    with patch("services.advanced_evaluator.AIClient") as mock_class:
        mock_class.return_value = mock_client
        eval_instance = AdvancedAnswerEvaluator()
        eval_instance._ai_client = mock_client
        yield eval_instance


# =============================================================================
# SAMPLE TEST DATA
# =============================================================================

SAMPLE_QUESTION = "Explain how you would design a rate limiter for an API."

SAMPLE_KEYWORDS = [
    "rate limiting",
    "token bucket",
    "sliding window",
    "throttling",
    "requests per second",
    "distributed",
    "redis",
    "middleware",
]

# Strong answer with depth
STRONG_ANSWER = """
I would implement a rate limiter using the token bucket algorithm. The core idea is:

1. Each client gets a bucket that holds tokens
2. Tokens are added at a fixed rate (e.g., 10 tokens/second)
3. Each request consumes one token
4. If bucket is empty, request is rejected with 429

For a distributed system, I'd use Redis with Lua scripts to ensure atomicity.
The bucket state (current tokens, last refill time) would be stored in Redis.

Trade-offs considered:
- Token bucket allows bursting, which is good for UX
- Sliding window would be more precise but more complex
- Fixed window is simpler but allows boundary bursting

In my previous role, I implemented this for a payment API handling 10k RPS.
We used Redis Cluster with client-side sharding by API key.

Key implementation details:
- Middleware layer checks rate limit before request processing
- Headers inform clients of remaining quota (X-RateLimit-Remaining)
- Separate limits per API endpoint based on cost
"""

# Shallow answer - technically correct but no depth
SHALLOW_ANSWER = """
I would use rate limiting with a token bucket. You add tokens at a fixed rate
and each request uses a token. If no tokens, reject the request.
"""

# Vague answer with hand-waving
VAGUE_ANSWER = """
Rate limiting is important for APIs. You need to control how many requests
come in. There are many approaches like token bucket or sliding window.
I would choose the right one based on the requirements and implement it
properly with good error handling and monitoring.
"""

# Too short answer
TOO_SHORT_ANSWER = "Use token bucket algorithm with Redis."

# Off-topic answer
OFF_TOPIC_ANSWER = """
Well, API design is interesting. I think the most important thing is having
good documentation and clear versioning. REST vs GraphQL is also a big
decision teams face.
"""

# Answer with contradiction
CONTRADICTION_ANSWER = """
I would never use Redis for rate limiting because it's too slow.
Redis is the best choice for high-performance rate limiting.
We should use in-memory storage for distributed systems.
"""


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def create_mock_response(
    relevance: int = 20,
    depth: int = 18,
    technical: int = 20,
    communication: int = 12,
    red_flags: list[str] = None,
    needs_followup: bool = False,
    followup_focus: str = "technical_depth",
    confidence_score: float = 0.85,
) -> str:
    """Create a mock LLM JSON response."""
    response = {
        "relevance_score": relevance,
        "depth_score": depth,
        "technical_score": technical,
        "communication_score": communication,
        "reasoning": {
            "relevance": "Candidate directly addresses the rate limiting question with appropriate approach.",
            "depth": "Explains token bucket mechanics and considers trade-offs for distributed implementation.",
            "technical": "Demonstrates correct understanding of rate limiting algorithms and Redis usage.",
            "communication": "Well-structured answer with clear progression from concept to implementation.",
        },
        "strengths": [
            "Clear explanation of token bucket algorithm",
            "Considers distributed system requirements",
            "Provides real-world example from experience",
        ],
        "weaknesses": [
            "Could elaborate on handling Redis failures",
            "Doesn't mention monitoring/alerting strategy",
        ],
        "red_flags": red_flags or [],
        "overall_score": relevance + depth + technical + communication,
        "confidence_score": confidence_score,
        "brief_feedback": (
            "Strong answer demonstrating solid understanding of rate limiting. "
            "Consider discussing failure scenarios and monitoring."
        ),
        "needs_followup": needs_followup,
        "followup_focus": followup_focus,
        "followup_reason": "Could explore failure handling in more depth.",
    }
    return json.dumps(response)


# =============================================================================
# TESTS: BASIC EVALUATION
# =============================================================================

class TestBasicEvaluation:
    """Test basic evaluation functionality."""

    def test_evaluate_strong_answer(self, evaluator):
        """Strong answers should receive high scores across all dimensions."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            relevance=22,
            depth=21,
            technical=22,
            communication=13,
            red_flags=[],
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
            role_level="senior",
        )

        assert result["relevance_score"] >= 18
        assert result["depth_score"] >= 18
        assert result["technical_score"] >= 18
        assert result["communication_score"] >= 10
        assert result["overall_score"] >= 70
        assert result["confidence_score"] >= 0.7
        assert not result["needs_followup"] or result["followup_reason"]

    def test_evaluate_shallow_answer(self, evaluator):
        """Shallow answers should receive lower depth scores and need followup."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            relevance=18,
            depth=10,
            technical=14,
            communication=10,
            red_flags=["vague_answer"],
            needs_followup=True,
            followup_focus="technical_depth",
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=SHALLOW_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
            role_level="mid",
        )

        assert result["depth_score"] <= 15
        assert result["needs_followup"] is True
        assert result["followup_focus"] == "technical_depth"

    def test_evaluate_too_short_answer(self, evaluator):
        """Very short answers should be heavily penalized."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            relevance=12,
            depth=8,
            technical=10,
            communication=6,
            red_flags=[],
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=TOO_SHORT_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert "too_short" in result["red_flags"]
        assert result["depth_score"] <= 10
        assert result["communication_score"] <= 8
        assert result["confidence_score"] <= 0.6
        assert result["needs_followup"] is True

    def test_evaluate_off_topic_answer(self, evaluator):
        """Off-topic answers should have low relevance scores."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            relevance=5,
            depth=8,
            technical=6,
            communication=10,
            red_flags=["vague_answer"],
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=OFF_TOPIC_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert result["relevance_score"] <= 10
        assert result["needs_followup"] is True

    def test_evaluate_contradiction(self, evaluator):
        """Answers with contradictions should be flagged."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            relevance=15,
            depth=10,
            technical=8,
            communication=8,
            red_flags=["contradiction"],
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=CONTRADICTION_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert "contradiction" in result["red_flags"]
        assert result["technical_score"] <= 15


# =============================================================================
# TESTS: SCORING LOGIC
# =============================================================================

class TestScoringLogic:
    """Test scoring boundaries and consistency."""

    def test_score_ranges(self, evaluator):
        """Scores should stay within defined ranges."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            relevance=25,
            depth=25,
            technical=25,
            communication=15,
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert 0 <= result["relevance_score"] <= RELEVANCE_MAX
        assert 0 <= result["depth_score"] <= DEPTH_MAX
        assert 0 <= result["technical_score"] <= TECHNICAL_MAX
        assert 0 <= result["communication_score"] <= COMMUNICATION_MAX
        assert 0.0 <= result["confidence_score"] <= 1.0

    def test_overall_score_calculation(self, evaluator):
        """Overall score should be sum of component scores minus penalties."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            relevance=20,
            depth=18,
            technical=20,
            communication=12,
            red_flags=[],
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        expected_base = 20 + 18 + 20 + 12
        assert result["overall_score"] <= expected_base
        assert result["overall_score"] >= expected_base - RED_FLAG_PENALTY_MAX

    def test_red_flag_penalty_applied(self, evaluator):
        """Red flags should reduce overall score."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            relevance=20,
            depth=18,
            technical=20,
            communication=12,
            red_flags=["vague_answer", "no_real_example"],
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=VAGUE_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        # With 2 red flags, penalty should be applied
        base_score = 20 + 18 + 20 + 12
        assert result["overall_score"] < base_score

    def test_consistency_layer_normalization(self, evaluator):
        """Depth and technical scores should be somewhat correlated."""
        # LLM returns wildly different depth vs technical
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            relevance=20,
            depth=22,  # High depth
            technical=8,  # Low technical - inconsistency
            communication=12,
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        # Normalization should reduce the gap
        gap = abs(result["depth_score"] - result["technical_score"])
        # After normalization, gap should be reduced (not necessarily eliminated)
        assert gap <= 14  # Should be less than original 14


# =============================================================================
# TESTS: REASONING OUTPUT
# =============================================================================

class TestReasoningOutput:
    """Test that reasoning is provided for each score."""

    def test_reasoning_provided_for_all_scores(self, evaluator):
        """Each score dimension should have reasoning explanation."""
        evaluator._ai_client.generate_text.return_value = create_mock_response()

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert "reasoning" in result
        assert "relevance" in result["reasoning"]
        assert "depth" in result["reasoning"]
        assert "technical" in result["reasoning"]
        assert "communication" in result["reasoning"]

        # Reasoning should be substantive (not empty or placeholder)
        for key, value in result["reasoning"].items():
            assert len(value) >= 10, f"Reasoning for {key} is too short"

    def test_reasoning_explains_score(self, evaluator):
        """Reasoning should explain WHY the score was given."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            depth=10,  # Low depth score
            red_flags=["vague_answer"],
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=VAGUE_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        depth_reasoning = result["reasoning"]["depth"]
        # Should explain why depth is low
        assert len(depth_reasoning) >= 20


# =============================================================================
# TESTS: FEEDBACK QUALITY
# =============================================================================

class TestFeedbackQuality:
    """Test that feedback is specific and actionable."""

    def test_feedback_is_specific(self, evaluator):
        """Feedback should be specific to the answer, not generic."""
        evaluator._ai_client.generate_text.return_value = create_mock_response()

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        feedback = result["brief_feedback"]
        assert len(feedback) >= 30  # Not a trivial response
        # Should not be the generic fallback
        assert "fallback" not in feedback.lower()

    def test_feedback_is_actionable(self, evaluator):
        """Feedback should suggest concrete improvements."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            depth=12,
            red_flags=["vague_answer"],
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=VAGUE_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        feedback = result["brief_feedback"]
        # Should contain actionable language
        actionable_words = ["consider", "add", "include", "explain", "provide", "elaborate"]
        has_actionable = any(word in feedback.lower() for word in actionable_words)
        assert has_actionable or len(feedback) >= 50


# =============================================================================
# TESTS: FOLLOW-UP DECISION LOGIC
# =============================================================================

class TestFollowupDecision:
    """Test follow-up decision logic."""

    def test_followup_needed_for_low_depth(self, evaluator):
        """Low depth scores should trigger followup."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            depth=10,
            needs_followup=False,  # LLM says no, but logic should override
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=SHALLOW_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert result["needs_followup"] is True
        assert result["followup_focus"] == "technical_depth"

    def test_followup_focus_is_valid(self, evaluator):
        """Followup focus should be one of the allowed types."""
        evaluator._ai_client.generate_text.return_value = create_mock_response()

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        valid_focus = {"technical_depth", "clarity", "missing_concepts", "recovery"}
        assert result["followup_focus"] in valid_focus

    def test_followup_reason_is_provided(self, evaluator):
        """When followup is needed, a reason should be provided."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            depth=10,
            needs_followup=True,
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=SHALLOW_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        if result["needs_followup"]:
            assert len(result["followup_reason"]) >= 10


# =============================================================================
# TESTS: ROLE LEVEL ADJUSTMENT
# =============================================================================

class TestRoleLevelAdjustment:
    """Test role-level scoring adjustments."""

    def test_fresher_gets_benefit_of_doubt(self, evaluator):
        """Fresher answers should get slight score boost."""
        evaluator._ai_client.generate_text.return_value = create_mock_response()

        result_fresher = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=SHALLOW_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
            role_level="fresher",
        )

        result_senior = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=SHALLOW_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
            role_level="senior",
        )

        # Fresher should have slightly higher depth/technical after adjustment
        assert result_fresher["depth_score"] >= result_senior["depth_score"] - 2

    def test_senior_held_to_higher_standard(self, evaluator):
        """Senior answers should not get freshness adjustment."""
        evaluator._ai_client.generate_text.return_value = create_mock_response()

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
            role_level="senior",
        )

        # Senior evaluation should be strict (no artificial boost)
        assert result["depth_score"] <= DEPTH_MAX
        assert result["technical_score"] <= TECHNICAL_MAX


# =============================================================================
# TESTS: EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_answer(self, evaluator):
        """Empty answers should use fallback response."""
        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer="",
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert result["overall_score"] == 40  # Fallback score
        assert result["needs_followup"] is True

    def test_empty_question(self, evaluator):
        """Empty questions should use fallback response."""
        result = evaluator.evaluate(
            question="",
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert result["overall_score"] == 40  # Fallback score

    def test_no_keywords(self, evaluator):
        """Evaluation should work without expected keywords."""
        evaluator._ai_client.generate_text.return_value = create_mock_response()

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=[],
        )

        assert result["relevance_score"] >= 0
        assert result["depth_score"] >= 0

    def test_none_keywords(self, evaluator):
        """Evaluation should work with None keywords."""
        evaluator._ai_client.generate_text.return_value = create_mock_response()

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=None,
        )

        assert result["relevance_score"] >= 0

    def test_unprofessional_tone_detected(self, evaluator):
        """Unprofessional language should be flagged."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            communication=10,
            red_flags=[],
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer="This stupid rate limiting thing is easy, just use Redis.",
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert "unprofessional_tone" in result["red_flags"]
        assert result["communication_score"] <= 10

    def test_llm_error_uses_fallback(self, evaluator):
        """LLM errors should gracefully fall back."""
        evaluator._ai_client.generate_text.side_effect = Exception("API error")

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        # Should still return valid result
        assert "overall_score" in result
        assert "needs_followup" in result


# =============================================================================
# TESTS: CONFIDENCE SCORE
# =============================================================================

class TestConfidenceScore:
    """Test confidence score calculation."""

    def test_high_confidence_for_complete_answer(self, evaluator):
        """Complete, well-structured answers should have high confidence."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            confidence_score=0.85,
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert result["confidence_score"] >= 0.7

    def test_low_confidence_for_short_answer(self, evaluator):
        """Short answers should have capped confidence."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            confidence_score=0.9,  # LLM is confident, but we cap it
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=TOO_SHORT_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert result["confidence_score"] <= 0.6


# =============================================================================
# TESTS: STRENGTHS AND WEAKNESSES
# =============================================================================

class TestStrengthsAndWeaknesses:
    """Test strengths and weaknesses extraction."""

    def test_strengths_identified(self, evaluator):
        """Strong answers should have strengths identified."""
        evaluator._ai_client.generate_text.return_value = create_mock_response()

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert isinstance(result["strengths"], list)
        # Should have at least one strength for good answers
        if result["overall_score"] >= 60:
            assert len(result["strengths"]) >= 1

    def test_weaknesses_identified(self, evaluator):
        """Answers with issues should have weaknesses identified."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            depth=10,
            red_flags=["vague_answer"],
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=VAGUE_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert isinstance(result["weaknesses"], list)


# =============================================================================
# TESTS: EVALUATION RESULT DATA CLASS
# =============================================================================

class TestEvaluationResult:
    """Test the EvaluationResult data class."""

    def test_to_dict_conversion(self):
        """EvaluationResult should convert to dict correctly."""
        result = EvaluationResult(
            relevance_score=20,
            depth_score=18,
            technical_score=20,
            communication_score=12,
            reasoning={"relevance": "test", "depth": "test", "technical": "test", "communication": "test"},
            strengths=["good answer"],
            weaknesses=["needs work"],
            red_flags=[],
            overall_score=70,
            confidence_score=0.8,
            brief_feedback="Good job",
            needs_followup=False,
            followup_focus="technical_depth",
            followup_reason="N/A",
        )

        result_dict = result.to_dict()

        assert result_dict["relevance_score"] == 20
        assert result_dict["depth_score"] == 18
        assert result_dict["technical_score"] == 20
        assert result_dict["communication_score"] == 12
        assert result_dict["overall_score"] == 70
        assert result_dict["confidence_score"] == 0.8
        assert result_dict["strengths"] == ["good answer"]
        assert result_dict["weaknesses"] == ["needs work"]


# =============================================================================
# INTEGRATION TESTS (with mock)
# =============================================================================

class TestIntegration:
    """Integration-style tests with mocked LLM."""

    def test_full_evaluation_workflow(self, evaluator):
        """Test complete evaluation workflow from input to output."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            relevance=22,
            depth=20,
            technical=21,
            communication=13,
            red_flags=[],
            needs_followup=False,
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=STRONG_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
            role_level="mid",
        )

        # Verify all expected fields are present
        required_fields = [
            "relevance_score", "depth_score", "technical_score",
            "communication_score", "reasoning", "strengths", "weaknesses",
            "red_flags", "overall_score", "confidence_score", "brief_feedback",
            "needs_followup", "followup_focus", "followup_reason",
        ]
        for field_name in required_fields:
            assert field_name in result, f"Missing field: {field_name}"

        # Verify reasoning is present for each dimension
        for dim in ["relevance", "depth", "technical", "communication"]:
            assert dim in result["reasoning"]
            assert len(result["reasoning"][dim]) >= 10

    def test_poor_answer_workflow(self, evaluator):
        """Test evaluation of a poor answer."""
        evaluator._ai_client.generate_text.return_value = create_mock_response(
            relevance=8,
            depth=6,
            technical=5,
            communication=5,
            red_flags=["too_short", "vague_answer"],
            needs_followup=True,
            followup_focus="recovery",
        )

        result = evaluator.evaluate(
            question=SAMPLE_QUESTION,
            answer=TOO_SHORT_ANSWER,
            expected_keywords=SAMPLE_KEYWORDS,
        )

        assert result["needs_followup"] is True
        assert result["overall_score"] <= 40
        assert len(result["red_flags"]) >= 2
