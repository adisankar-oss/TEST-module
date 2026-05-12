from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_ENV: str = "production"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-flash-latest"
    LLM_TIMEOUT_SECONDS: int = 3
    LLM_MAX_RETRIES: int = 2

    MAX_FOLLOWUP_REPEATS: int = 2
    FOLLOWUP_SCORE_THRESHOLD: int = 65
    FRUSTRATION_THRESHOLD: int = 4
    SHORT_ANSWER_WORDS: int = 15

    HEARTBEAT_INTERVAL_SECONDS: int = 15
    STALE_CONNECTION_TIMEOUT_SECONDS: int = 30
    MAX_REPLAY_EVENTS: int = 20

    SESSION_TTL_SECONDS: int = 7200

    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
