from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    # ── App ────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"  # "development" | "production"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ── Groq ─────────────────────────────────────────────────────────────
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # ── Gemini ─────────────────────────────────────────────────────────────
    # Accept both key names — GEMINI_API_KEY takes priority
    GEMINI_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-flash-latest"

    # ── LLM Shared ─────────────────────────────────────────────────────────
    LLM_TIMEOUT_SECONDS: int = 15
    LLM_MAX_RETRIES: int = 2

    # ── Interview ──────────────────────────────────────────────────────────
    MAX_FOLLOWUP_REPEATS: int = 2
    FOLLOWUP_SCORE_THRESHOLD: int = 65
    FRUSTRATION_THRESHOLD: int = 4
    SHORT_ANSWER_WORDS: int = 15

    # ── WebSocket ──────────────────────────────────────────────────────────
    HEARTBEAT_INTERVAL_SECONDS: int = 15
    STALE_CONNECTION_TIMEOUT_SECONDS: int = 30
    MAX_REPLAY_EVENTS: int = 20

    # ── Session ────────────────────────────────────────────────────────────
    SESSION_TTL_SECONDS: int = 7200

    # ── Logging ────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    def effective_gemini_key(self) -> str:
        """GEMINI_API_KEY takes priority over GOOGLE_API_KEY."""
        return self.GEMINI_API_KEY or self.GOOGLE_API_KEY

    @field_validator("GROQ_MODEL")
    @classmethod
    def validate_groq_model(cls, v: str) -> str:
        allowed = {
            "llama-3.3-70b-versatile",
            "llama-3.1-70b-versatile",
            "llama3-70b-8192",
        }
        if v not in allowed:
            raise ValueError(f"Unsupported GROQ_MODEL '{v}'. Allowed: {allowed}")
        return v

    @field_validator("DEBUG", mode="before")
    @classmethod
    def normalize_debug_flag(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "prod", "production"}:
                return False
            if normalized in {"debug", "dev", "development"}:
                return True
        return value

    @field_validator("GEMINI_MODEL")
    @classmethod
    def validate_gemini_model(cls, v: str) -> str:
        normalized = v.strip()
        if not normalized:
            raise ValueError("GEMINI_MODEL must not be empty")
        return normalized

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
