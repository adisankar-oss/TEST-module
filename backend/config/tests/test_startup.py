import os
import pytest
from unittest.mock import patch
from backend.config.provider_validator import ProviderValidator, ProviderStatus


def make_settings_patch(**overrides):
    """Helper to patch settings fields."""
    return overrides


# ── ProviderValidator ────────────────────────────────────────────────────


def test_groq_missing_key_returns_unavailable():
    with patch("backend.config.provider_validator.settings") as mock:
        mock.GROQ_API_KEY = ""
        mock.effective_gemini_key = lambda: "valid-key-abcdefghijklmnop"
        mock.is_development = False
        result = ProviderValidator()._validate_groq()
        assert not result.available
        assert "missing" in result.reason.lower()


def test_gemini_missing_key_returns_unavailable():
    with patch("backend.config.provider_validator.settings") as mock:
        mock.effective_gemini_key = lambda: ""
        result = ProviderValidator()._validate_gemini()
        assert not result.available


def test_development_mode_raises_on_missing_provider():
    with patch("backend.config.provider_validator.settings") as mock:
        mock.GROQ_API_KEY = ""
        mock.effective_gemini_key = lambda: ""
        mock.is_development = True
        mock.is_production = False
        with pytest.raises(RuntimeError, match="DEVELOPMENT"):
            ProviderValidator().validate_all()


def test_production_mode_does_not_raise_on_missing_provider():
    with patch("backend.config.provider_validator.settings") as mock:
        mock.GROQ_API_KEY = ""
        mock.effective_gemini_key = lambda: ""
        mock.is_development = False
        mock.is_production = True
        # Should not raise
        results = ProviderValidator.__new__(ProviderValidator)
        statuses = [
            ProviderStatus("Groq", False, "missing"),
            ProviderStatus("Gemini", False, "missing"),
        ]
        ProviderValidator._enforce_policy(statuses)  # no raise


def test_development_mode_allows_partial_provider_availability():
    with patch("backend.config.provider_validator.settings") as mock:
        mock.is_development = True
        mock.is_production = False
        statuses = [
            ProviderStatus("Groq", False, "invalid"),
            ProviderStatus("Gemini", True),
        ]
        ProviderValidator._enforce_policy(statuses)  # no raise


def test_all_valid_keys_pass():
    with patch("backend.config.provider_validator.settings") as mock:
        mock.GROQ_API_KEY = "gsk_" + "x" * 40
        mock.effective_gemini_key = lambda: "AIza" + "x" * 35
        mock.is_development = True
        r_groq = ProviderValidator()._validate_groq()
        r_gemini = ProviderValidator()._validate_gemini()
        assert r_groq.available
        assert r_gemini.available


# ── Settings ─────────────────────────────────────────────────────────────


def test_effective_gemini_key_prefers_gemini_key():
    with patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY": "gemini-key-xxxxxxxxxxxxxxxxxxxx",
            "GOOGLE_API_KEY": "google-key-xxxxxxxxxxxxxxxxxxxx",
            "GROQ_API_KEY": "groq-key-xxxxxxxxxxxxxxxxxxxxx",
        },
    ):
        from importlib import reload

        import backend.config.settings as s

        reload(s)
        assert s.settings.effective_gemini_key() == "gemini-key-xxxxxxxxxxxxxxxxxxxx"


def test_effective_gemini_key_falls_back_to_google_key():
    with patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY": "",
            "GOOGLE_API_KEY": "google-key-xxxxxxxxxxxxxxxxxxxx",
            "GROQ_API_KEY": "groq-key-xxxxxxxxxxxxxxxxxxxxx",
        },
    ):
        from importlib import reload

        import backend.config.settings as s

        reload(s)
        assert s.settings.effective_gemini_key() == "google-key-xxxxxxxxxxxxxxxxxxxx"


def test_debug_flag_accepts_release_string():
    with patch.dict(
        os.environ,
        {
            "DEBUG": "release",
            "GROQ_API_KEY": "groq-key-xxxxxxxxxxxxxxxxxxxxx",
            "GEMINI_API_KEY": "gemini-key-xxxxxxxxxxxxxxxxxxxx",
        },
        clear=False,
    ):
        from importlib import reload

        import backend.config.settings as s

        reload(s)
        assert s.settings.DEBUG is False


def test_debug_flag_accepts_development_string():
    with patch.dict(
        os.environ,
        {
            "DEBUG": "development",
            "GROQ_API_KEY": "groq-key-xxxxxxxxxxxxxxxxxxxxx",
            "GEMINI_API_KEY": "gemini-key-xxxxxxxxxxxxxxxxxxxx",
        },
        clear=False,
    ):
        from importlib import reload

        import backend.config.settings as s

        reload(s)
        assert s.settings.DEBUG is True


# ── Health Monitor ────────────────────────────────────────────────────────


def test_health_monitor_marks_unhealthy_after_3_failures():
    from backend.services.llm.provider_health_monitor import ProviderHealthMonitor

    monitor = ProviderHealthMonitor()
    for _ in range(3):
        monitor.record_failure("gemini", "timeout")
    assert not monitor.is_healthy("gemini")


def test_health_monitor_recovers_on_success():
    from backend.services.llm.provider_health_monitor import ProviderHealthMonitor

    monitor = ProviderHealthMonitor()
    for _ in range(3):
        monitor.record_failure("gemini", "timeout")
    monitor.record_success("gemini")
    assert monitor.is_healthy("gemini")
