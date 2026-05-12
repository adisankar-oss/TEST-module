"""
Guarantees .env is loaded before any module reads os.environ.
Safe for uvicorn --reload, CMD, PowerShell, and Linux.
Call load_env() as the FIRST line of backend/main.py.
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_env(env_path: Path | None = None) -> None:
    """
    Explicitly load .env into os.environ before pydantic-settings reads it.
    Idempotent — safe to call multiple times (uvicorn reload).
    """
    try:
        from dotenv import load_dotenv, find_dotenv
    except ImportError:
        logger.warning(
            "python-dotenv not installed — skipping .env load. "
            "Run: pip install python-dotenv"
        )
        return

    if env_path is None:
        # Walk up from this file to find .env at project root
        env_path = (
            Path(find_dotenv(usecwd=True))
            if find_dotenv(usecwd=True)
            else Path(__file__).parent.parent.parent / ".env"
        )

    if not env_path.exists():
        logger.warning(
            "EnvFileNotFound path=%s — relying on system env vars", env_path
        )
        return

    # override=False: system env vars take priority over .env
    # This is correct production behavior
    load_dotenv(dotenv_path=env_path, override=False)
    logger.info("EnvLoaded path=%s", env_path)

    _verify_critical_vars()


def _verify_critical_vars() -> None:
    """Log which critical vars are present (never log values)."""
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if groq_key:
        logger.info("EnvVarPresent var=GROQ_API_KEY length=%d", len(groq_key))
    else:
        logger.warning("EnvVarMissing var=GROQ_API_KEY")

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    google_key = os.environ.get("GOOGLE_API_KEY", "")
    if gemini_key:
        logger.info("EnvVarPresent var=GEMINI_API_KEY length=%d", len(gemini_key))
    elif google_key:
        logger.info("EnvVarPresent var=GOOGLE_API_KEY length=%d", len(google_key))
    else:
        logger.warning("EnvVarMissing var=GEMINI_API_KEY|GOOGLE_API_KEY")
