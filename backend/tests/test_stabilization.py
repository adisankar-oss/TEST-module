import pytest
from backend.services.llm.safe_prompt_renderer import SafePromptRenderer
from backend.services.fsm.followup_resolution import (
    FollowUpResolutionEngine, FollowUpResolutionState, FollowUpStatus,
)

# ── SafePromptRenderer tests ─────────────────────────────────────────────

def test_renderer_success():
    r = SafePromptRenderer()
    result = r.render("Hello {name}, role is {role}",
                     {"name": "Alice", "role": "engineer"}, "t1")
    assert result.success
    assert result.prompt == "Hello Alice, role is engineer"
    assert not result.fallback_used


def test_renderer_missing_var_no_crash():
    r = SafePromptRenderer()
    result = r.render("Hello {name}, difficulty {difficulty}",
                     {"name": "Alice"}, "t2")
    assert not result.success
    assert "difficulty" in result.missing_vars
    assert result.fallback_used
    assert "{difficulty}" not in result.prompt  # placeholder stripped


def test_renderer_all_missing_returns_stripped_template():
    r = SafePromptRenderer()
    result = r.render("{topic} question for {role}", {}, "t3")
    assert result.fallback_used
    assert len(result.missing_vars) == 2


def test_renderer_optional_vars_not_flagged():
    r = SafePromptRenderer()
    result = r.render("Q about {topic} {optional_context}",
                     {"topic": "databases"},
                     optional_vars={"optional_context"})
    # optional_context missing but not flagged as error
    assert "optional_context" not in result.missing_vars

# ── FollowUpResolutionEngine tests ───────────────────────────────────────

def make_engine(session_id="test") -> FollowUpResolutionEngine:
    return FollowUpResolutionEngine(
        FollowUpResolutionState(session_id=session_id)
    )

def test_first_followup_always_proceeds():
    eng = make_engine()
    should, reason = eng.should_followup("depth_probe", "rate_limiter")
    assert should
    assert reason == "proceed"

def test_high_score_resolves_probe():
    eng = make_engine()
    eng.record_answer("detailed answer " * 10, score=80,
                        intent="depth_probe", topic="rate_limiter")
    should, reason = eng.should_followup("depth_probe", "rate_limiter")
    assert not should
    assert reason == "high_score"

def test_repeat_limit_exhausts_probe():
    eng = make_engine()
    for _ in range(2):
        eng.record_answer("short", score=30,
                          intent="depth_probe", topic="rate_limiter")
    should, reason = eng.should_followup("depth_probe", "rate_limiter")
    assert not should
    assert reason == "exhausted"

def test_candidate_frustration_suppresses_followup():
    eng = make_engine()
    # 3 consecutive short answers triggers frustration
    for _ in range(3):
        eng.record_answer("ok", score=25,
                          intent="depth_probe", topic="topic_a")
    should, reason = eng.should_followup("new_intent", "new_topic")
    assert not should
    assert reason == "frustrated"

def test_force_resolve_prevents_reentry():
    eng = make_engine()
    eng.force_resolve("contradiction", "architecture")
    should, reason = eng.should_followup("contradiction", "architecture")
    assert not should
    assert reason == "resolved"

def test_reset_clears_per_question_counter():
    eng = make_engine()
    eng.record_answer("answer", score=40,
                     intent="depth_probe", topic="caching")
    assert eng.state.total_followups_this_question == 1
    eng.reset_for_next_question()
    assert eng.state.total_followups_this_question == 0
    # probes still exist after reset
    assert len(eng.state.probes) == 1

def test_serialization_round_trip():
    eng = make_engine("sess-99")
    eng.record_answer(
        "some answer text here and more words",
        score=45,
        intent="depth_probe",
        topic="caching",
    )
    restored = FollowUpResolutionEngine.from_dict(eng.to_dict())
    assert restored.state.session_id == "sess-99"
    assert len(restored.state.probes) == 1
    assert restored.state.probes[0].asked_count == 1
