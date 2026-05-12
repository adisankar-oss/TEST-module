import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

# ── Health endpoints ──────────────────────────────────────────────────────


def test_liveness_always_200():
    from backend.main import app

    client = TestClient(app)
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


def test_health_returns_provider_summary():
    from backend.main import app

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert "providers" in body
    assert "groq" in body["providers"]


def test_readiness_503_when_provider_unhealthy():
    from backend.services.llm.provider_health_monitor import ProviderHealthMonitor

    monitor = ProviderHealthMonitor()
    for _ in range(3):
        monitor.record_failure("groq", "timeout")
    with patch("backend.api.health.health_monitor", monitor):
        from backend.main import app

        client = TestClient(app)
        r = client.get("/health/ready")
        assert r.status_code == 503
        assert "groq" in r.json()["degraded"]


def test_readiness_200_when_all_healthy():
    from backend.services.llm.provider_health_monitor import ProviderHealthMonitor

    monitor = ProviderHealthMonitor()
    with patch("backend.api.health.health_monitor", monitor):
        from backend.main import app

        client = TestClient(app)
        r = client.get("/health/ready")
        assert r.status_code == 200


# ── RequestID middleware ──────────────────────────────────────────────────


def test_request_id_header_returned():
    from backend.main import app

    client = TestClient(app)
    r = client.get("/health/live")
    assert "x-request-id" in r.headers


def test_request_id_propagated_from_client():
    from backend.main import app

    client = TestClient(app)
    r = client.get("/health/live", headers={"X-Request-ID": "test-id-123"})
    assert r.headers["x-request-id"] == "test-id-123"


# ── FollowUpResolutionEngine FSM wiring ──────────────────────────────────


from backend.services.fsm.followup_resolution import (
    FollowUpResolutionEngine,
    FollowUpResolutionState,
)


def make_engine(session_id="test") -> FollowUpResolutionEngine:
    return FollowUpResolutionEngine(FollowUpResolutionState(session_id=session_id))


def test_followup_blocked_after_high_score():
    eng = make_engine()
    eng.record_answer("detailed answer " * 10, score=80, intent="depth_probe", topic="redis")
    should, reason = eng.should_followup("depth_probe", "redis")
    assert not should
    assert reason == "high_score"


def test_followup_blocked_after_max_repeats():
    eng = make_engine()
    for _ in range(2):
        eng.record_answer("short", score=30, intent="depth_probe", topic="redis")
    should, reason = eng.should_followup("depth_probe", "redis")
    assert not should
    assert reason == "exhausted"


def test_reset_clears_per_question_state():
    eng = make_engine()
    eng.record_answer("answer", score=40, intent="depth_probe", topic="caching")
    eng.reset_for_next_question()
    assert eng.state.total_followups_this_question == 0
    assert len(eng.state.probes) == 1  # probe memory persists


def test_serialization_preserves_state():
    eng = make_engine("sess-fsm")
    eng.record_answer("some detailed answer here", score=45, intent="depth_probe", topic="caching")
    restored = FollowUpResolutionEngine.from_dict(eng.to_dict())
    assert restored.state.session_id == "sess-fsm"
    assert restored.state.probes[0].asked_count == 1


def test_frustration_blocks_new_followups():
    eng = make_engine()
    for _ in range(3):
        eng.record_answer("ok", score=25, intent="depth_probe", topic="x")
    should, reason = eng.should_followup("new_intent", "new_topic")
    assert not should
    assert reason == "frustrated"


# ── Gemini model name ─────────────────────────────────────────────────────


def test_gemini_model_is_latest():
    from backend.config.settings import settings

    assert settings.GEMINI_MODEL.endswith("-latest") or settings.GEMINI_MODEL in {
        "gemini-1.0-pro"
    }


def test_no_bare_gemini_flash_in_configs():
    from backend.services.llm.model_configs import MODEL_CONFIGS

    gemini_cfg = MODEL_CONFIGS.get("gemini")
    assert gemini_cfg is not None
    assert (
        gemini_cfg.model_name != "gemini-1.5-flash"
    ), "Must use gemini-1.5-flash-latest not gemini-1.5-flash"