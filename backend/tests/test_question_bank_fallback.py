"""
Test suite for the Adaptive Question Bank Fallback System.

Run:
    cd c:\\ai-interview-avatar\\backend
    python tests/test_question_bank_fallback.py
"""
from __future__ import annotations

import io
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.question_bank_service import (
    AdaptiveQuestionResult,
    AdaptiveQuestionService,
    EvaluationScores,
)
from services.question_service import QuestionResult, QuestionService


# -----------------------------------------------
# Helpers
# -----------------------------------------------

def make_session(**kwargs):
    defaults = {
        "id": "test-session-001",
        "session_id": "test-session-001",
        "job_id": "backend_engineer",
        "current_question_number": 1,
        "max_questions": 10,
        "config": {},
        "memory": None,
        "role_level": "mid",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


PASS = "[PASS]"
FAIL = "[FAIL]"
results = []


def check(name, condition):
    status = PASS if condition else FAIL
    results.append((name, condition))
    print(f"  {status} {name}")


# -----------------------------------------------
# 1. Question Bank Loading (difficulty-based)
# -----------------------------------------------

def test_load_questions():
    print("\n[1] Question Bank Loading (difficulty-based)")
    svc = AdaptiveQuestionService(seed=42)
    bank = svc.load_questions()

    check("Bank loads without error", bank is not None)
    check("Has 5 topics", len(bank) == 5)

    total = sum(len(q) for diffs in bank.values() for q in diffs.values())
    check(f"At least 50 questions (got {total})", total >= 50)

    for topic, diffs in bank.items():
        for level in ["easy", "medium", "hard"]:
            check(f"{topic}.{level} exists", level in diffs)


# -----------------------------------------------
# 2. No Duplicate Questions
# -----------------------------------------------

def test_no_duplicates():
    print("\n[2] No Duplicate Questions")
    svc = AdaptiveQuestionService(seed=42)
    bank = svc.load_questions()

    all_q = []
    for diffs in bank.values():
        for questions in diffs.values():
            all_q.extend(questions)

    normalized = [" ".join(q.lower().split()) for q in all_q]
    unique = set(normalized)
    check(f"No duplicates ({len(all_q)} total, {len(unique)} unique)", len(normalized) == len(unique))


# -----------------------------------------------
# 3. Adaptive Difficulty -- Score >= 75 increases
# -----------------------------------------------

def test_difficulty_increase():
    print("\n[3] Adaptive Difficulty -- High score increases difficulty")
    svc = AdaptiveQuestionService(seed=42)

    scores = EvaluationScores(overall_score=80, technical_score=85, communication_score=70)
    new_diff, reason = svc.adjust_difficulty(
        current_difficulty="medium",
        evaluation_scores=scores,
        base_role_level="mid",
    )

    check("Difficulty increased to hard", new_diff == "hard")
    check("Reason is increased_difficulty", reason == "increased_difficulty")


# -----------------------------------------------
# 4. Adaptive Difficulty -- Score < 50 decreases
# -----------------------------------------------

def test_difficulty_decrease():
    print("\n[4] Adaptive Difficulty -- Low score decreases difficulty")
    svc = AdaptiveQuestionService(seed=42)

    scores = EvaluationScores(overall_score=35, technical_score=30, communication_score=40)
    new_diff, reason = svc.adjust_difficulty(
        current_difficulty="medium",
        evaluation_scores=scores,
        base_role_level="mid",
    )

    check("Difficulty decreased to easy", new_diff == "easy")
    check("Reason is decreased_difficulty", reason == "decreased_difficulty")


# -----------------------------------------------
# 5. Adaptive Difficulty -- Score 50-74 maintains
# -----------------------------------------------

def test_difficulty_maintain():
    print("\n[5] Adaptive Difficulty -- Mid score maintains difficulty")
    svc = AdaptiveQuestionService(seed=42)

    scores = EvaluationScores(overall_score=65, technical_score=60, communication_score=70)
    new_diff, reason = svc.adjust_difficulty(
        current_difficulty="medium",
        evaluation_scores=scores,
        base_role_level="mid",
    )

    check("Difficulty stays medium", new_diff == "medium")
    check("Reason is maintained_difficulty", reason == "maintained_difficulty")


# -----------------------------------------------
# 6. Difficulty clamped at boundaries
# -----------------------------------------------

def test_difficulty_clamp():
    print("\n[6] Difficulty clamped at boundaries")
    svc = AdaptiveQuestionService(seed=42)

    high_scores = EvaluationScores(overall_score=90)
    new_diff, reason = svc.adjust_difficulty(
        current_difficulty="hard",
        evaluation_scores=high_scores,
    )
    check("Cannot go above hard", new_diff == "hard")
    check("Reason is already_at_max", reason == "already_at_max")

    low_scores = EvaluationScores(overall_score=20)
    new_diff2, reason2 = svc.adjust_difficulty(
        current_difficulty="easy",
        evaluation_scores=low_scores,
    )
    check("Cannot go below easy", new_diff2 == "easy")
    check("Reason is already_at_min", reason2 == "already_at_min")


# -----------------------------------------------
# 7. Topic switching on weak technical
# -----------------------------------------------

def test_topic_switch_technical():
    print("\n[7] Topic switching -- weak technical")
    svc = AdaptiveQuestionService(seed=42)

    scores = EvaluationScores(overall_score=60, technical_score=30, communication_score=80)
    result = svc.select_topic_based_on_weakness(
        requested_topic="behavioral",
        evaluation_scores=scores,
    )

    check("Topic switched to technical_skills", result == "technical_skills")


# -----------------------------------------------
# 8. Topic switching on weak communication
# -----------------------------------------------

def test_topic_switch_communication():
    print("\n[8] Topic switching -- weak communication")
    svc = AdaptiveQuestionService(seed=42)

    scores = EvaluationScores(overall_score=60, technical_score=80, communication_score=30)
    result = svc.select_topic_based_on_weakness(
        requested_topic="technical_skills",
        evaluation_scores=scores,
    )

    check("Topic switched to behavioral", result == "behavioral")


# -----------------------------------------------
# 9. Session deduplication
# -----------------------------------------------

def test_session_deduplication():
    print("\n[9] Session deduplication")
    svc = AdaptiveQuestionService(seed=42)

    asked = []
    for _ in range(9):
        q = svc.get_question("technical_skills", "senior", session_id="dedup-test")
        asked.append(q)

    check("9 questions returned", len(asked) == 9)
    check("All 9 are unique", len(set(asked)) == 9)


# -----------------------------------------------
# 10. Full adaptive flow
# -----------------------------------------------

def test_full_adaptive_flow():
    print("\n[10] Full adaptive flow -- get_adaptive_question()")
    svc = AdaptiveQuestionService(seed=42)

    scores = EvaluationScores(overall_score=80, technical_score=85, communication_score=75)
    result = svc.get_adaptive_question(
        session_id="adaptive-test",
        topic="technical_skills",
        base_role_level="mid",
        evaluation_scores=scores,
    )

    check("Returns AdaptiveQuestionResult", isinstance(result, AdaptiveQuestionResult))
    check("Source is adaptive_fallback", result.source == "adaptive_fallback")
    check("Difficulty is hard (increased)", result.difficulty == "hard")
    check("Reason is increased_difficulty", result.reason == "increased_difficulty")
    check("Question is non-empty", len(result.question) > 10)

    d = result.to_dict()
    check("Serializable to dict", isinstance(d, dict))
    check("Dict has difficulty key", "difficulty" in d)
    check("Dict has reason key", "reason" in d)


# -----------------------------------------------
# 11. Adaptive flow -- low score path
# -----------------------------------------------

def test_adaptive_low_score():
    print("\n[11] Adaptive flow -- low score decreases difficulty")
    svc = AdaptiveQuestionService(seed=42)

    scores = EvaluationScores(overall_score=30, technical_score=55, communication_score=55)
    result = svc.get_adaptive_question(
        session_id="low-score-test",
        topic="problem_solving",
        base_role_level="mid",
        evaluation_scores=scores,
    )

    check("Difficulty is easy (decreased)", result.difficulty == "easy")
    check("Reason is decreased_difficulty", result.reason == "decreased_difficulty")


# -----------------------------------------------
# 12. Adaptive flow -- weakness targeting
# -----------------------------------------------

def test_adaptive_weakness_targeting():
    print("\n[12] Adaptive flow -- weakness-targeted topic switch")
    svc = AdaptiveQuestionService(seed=42)

    scores = EvaluationScores(overall_score=60, technical_score=25, communication_score=80)
    result = svc.get_adaptive_question(
        session_id="weakness-test",
        topic="culture_fit",
        base_role_level="mid",
        evaluation_scores=scores,
    )

    check("Topic switched to technical_skills", result.topic == "technical_skills")
    check("Reason is weakness_targeted", result.reason == "weakness_targeted")


# -----------------------------------------------
# 13. EvaluationScores dataclass
# -----------------------------------------------

def test_evaluation_scores():
    print("\n[13] EvaluationScores dataclass")
    scores = EvaluationScores(
        relevance_score=70,
        depth_score=60,
        technical_score=80,
        communication_score=55,
        overall_score=65,
    )

    d = scores.to_dict()
    check("to_dict() has all fields", len(d) == 5)
    check("overall_score correct", d["overall_score"] == 65)

    reconstructed = EvaluationScores.from_dict(d)
    check("from_dict() round-trips", reconstructed == scores)

    partial = EvaluationScores.from_dict({"overall_score": 42, "unknown_field": 99})
    check("from_dict() ignores unknown keys", partial.overall_score == 42)
    check("from_dict() defaults missing fields", partial.technical_score == 0.0)


# -----------------------------------------------
# 14. Deterministic seed
# -----------------------------------------------

def test_deterministic_seed():
    print("\n[14] Deterministic seed")
    svc1 = AdaptiveQuestionService(seed=99)
    svc2 = AdaptiveQuestionService(seed=99)

    q1 = [svc1.get_question("behavioral", "mid", session_id="s1") for _ in range(5)]
    q2 = [svc2.get_question("behavioral", "mid", session_id="s2") for _ in range(5)]

    check("Same seed produces same sequence", q1 == q2)

    svc3 = AdaptiveQuestionService(seed=123)
    q3 = [svc3.get_question("behavioral", "mid", session_id="s3") for _ in range(5)]
    check("Different seed produces different sequence", q1 != q3)


# -----------------------------------------------
# 15. Weighted scoring by role
# -----------------------------------------------

def test_weighted_scoring():
    print("\n[15] Weighted scoring by role level")
    svc = AdaptiveQuestionService(seed=42)

    scores = EvaluationScores(
        relevance_score=80,
        depth_score=40,
        technical_score=90,
        communication_score=50,
    )

    weighted_senior = svc._compute_weighted_score(scores, "senior")
    weighted_fresher = svc._compute_weighted_score(scores, "fresher")

    check("Senior weights technical more heavily", weighted_senior != weighted_fresher)
    check("Weighted scores are positive", weighted_senior > 0 and weighted_fresher > 0)


# -----------------------------------------------
# 16. Integration -- LLM fails, adaptive fallback
# -----------------------------------------------

def test_integration_adaptive_fallback():
    print("\n[16] Integration -- LLM fails, adaptive fallback used")
    bank_svc = AdaptiveQuestionService(seed=42)
    svc = QuestionService(question_bank_service=bank_svc)
    session = make_session()

    eval_scores = {"overall_score": 80, "technical_score": 85, "communication_score": 70}

    with patch("services.question_service.ask_llm", side_effect=RuntimeError("timeout")):
        result = asyncio.run(svc.generate_adaptive_question(session, eval_scores))

    check("Returns QuestionResult", isinstance(result, QuestionResult))
    check("Source is adaptive_fallback", result.source == "adaptive_fallback")
    check("Has difficulty field", len(result.difficulty) > 0)
    check("Has reason field", len(result.reason) > 0)
    check("Question is non-empty", len(result.question) > 10)


# -----------------------------------------------
# 17. Integration -- LLM succeeds (no adaptation)
# -----------------------------------------------

def test_integration_llm_success():
    print("\n[17] Integration -- LLM succeeds")
    bank_svc = AdaptiveQuestionService(seed=42)
    svc = QuestionService(question_bank_service=bank_svc)
    session = make_session()

    fake_response = json.dumps({
        "question": "How do you handle concurrency in distributed systems?",
        "type": "new",
        "topic": "technical_skills",
        "reasoning": "testing",
    })

    with patch("services.question_service.ask_llm", return_value=fake_response):
        result = asyncio.run(svc.generate_adaptive_question(session))

    check("Returns QuestionResult", isinstance(result, QuestionResult))
    check("Source is llm", result.source == "llm")
    check("Question matches", "concurrency" in result.question.lower())


# -----------------------------------------------
# 18. Session difficulty persistence
# -----------------------------------------------

def test_session_difficulty_persistence():
    print("\n[18] Session difficulty persists across calls")
    svc = AdaptiveQuestionService(seed=42)

    high = EvaluationScores(overall_score=90)
    svc.get_adaptive_question(
        session_id="persist-test",
        topic="technical_skills",
        base_role_level="mid",
        evaluation_scores=high,
    )

    check("Session difficulty is hard", svc.get_session_difficulty("persist-test") == "hard")

    low = EvaluationScores(overall_score=20)
    svc.get_adaptive_question(
        session_id="persist-test",
        topic="technical_skills",
        base_role_level="mid",
        evaluation_scores=low,
    )

    check("Session difficulty decreased to medium", svc.get_session_difficulty("persist-test") == "medium")


# -----------------------------------------------
# 19. Backward compatibility -- get_question()
# -----------------------------------------------

def test_backward_compat():
    print("\n[19] Backward compatibility -- get_question(topic, role_level)")
    svc = AdaptiveQuestionService(seed=42)

    q = svc.get_question("technical_skills", "senior", session_id="compat-test")
    check("Returns a string", isinstance(q, str))
    check("Non-empty question", len(q) > 10)

    q2 = svc.get_question("behavioural", "junior", session_id="compat-alias")
    check("Alias resolution works", len(q2) > 10)


# -----------------------------------------------
# 20. Session stats & clear
# -----------------------------------------------

def test_session_stats():
    print("\n[20] Session stats & clear")
    svc = AdaptiveQuestionService(seed=42)

    for _ in range(3):
        svc.get_question("behavioral", "mid", session_id="stats-test")

    stats = svc.get_session_stats("stats-test")
    check("questions_used is 3", stats["questions_used"] == 3)
    check("Has current_difficulty", "current_difficulty" in stats)

    svc.clear_session("stats-test")
    stats_after = svc.get_session_stats("stats-test")
    check("After clear, questions_used is 0", stats_after["questions_used"] == 0)


# -----------------------------------------------
# Runner
# -----------------------------------------------

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print("=" * 60)
    print("  Adaptive Question Bank Fallback System -- Test Suite")
    print("=" * 60)

    test_load_questions()
    test_no_duplicates()
    test_difficulty_increase()
    test_difficulty_decrease()
    test_difficulty_maintain()
    test_difficulty_clamp()
    test_topic_switch_technical()
    test_topic_switch_communication()
    test_session_deduplication()
    test_full_adaptive_flow()
    test_adaptive_low_score()
    test_adaptive_weakness_targeting()
    test_evaluation_scores()
    test_deterministic_seed()
    test_weighted_scoring()
    test_integration_adaptive_fallback()
    test_integration_llm_success()
    test_session_difficulty_persistence()
    test_backward_compat()
    test_session_stats()

    passed = sum(1 for _, ok in results if ok)
    failed = sum(1 for _, ok in results if not ok)

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed} passed, {failed} failed, {len(results)} total")
    print(f"{'=' * 60}")

    if failed:
        print("\n  Failed tests:")
        for name, ok in results:
            if not ok:
                print(f"    [FAIL] {name}")
        sys.exit(1)
    else:
        print("\n  All tests passed!")
        sys.exit(0)
