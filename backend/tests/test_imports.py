def test_core_imports_resolve():
    from backend.config.settings import settings

    assert settings is not None


def test_env_loader_importable():
    from backend.config.env_loader import load_env

    assert callable(load_env)


def test_task_router_importable():
    from backend.services.llm.task_router import TaskRouter

    assert TaskRouter is not None


def test_event_bus_importable():
    from backend.services.realtime.event_bus import event_bus

    assert event_bus is not None


def test_followup_engine_importable():
    from backend.services.fsm.followup_resolution import (
        FollowUpResolutionEngine,
        FollowUpResolutionState,
    )

    eng = FollowUpResolutionEngine(FollowUpResolutionState(session_id="import-test"))
    assert eng is not None


def test_health_router_importable():
    from backend.api.health import router

    assert router is not None


def test_no_import_uses_bare_config():
    import ast
    import pathlib

    backend_root = pathlib.Path("backend")
    bad_patterns = [
        "from config import",
        "from services import",
        "from fsm import",
        "from core import",
    ]
    violations = []
    for f in backend_root.rglob("*.py"):
        # Skip test files and __pycache__
        if "tests" in f.parts or "__pycache__" in str(f):
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for pat in bad_patterns:
            if pat in text:
                violations.append(f"{f}: {pat}")
    assert not violations, (
        "Bare imports found — fix with 'backend.' prefix:\n" + "\n".join(violations)
    )