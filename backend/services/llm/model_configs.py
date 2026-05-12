from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_GENERIC_TASK = "generic_text"


# Task Routing Policy: Realtime conversational tasks use Groq for recruiter-quality consistency
REALTIME_TASKS = frozenset({
    "greeting_generation",
    "question_generation",
    "followup_generation",
    "behavioral_probing",
    "clarification_generation",
    "acknowledgement_generation",
    "contradiction_resolution",
    "adaptive_questioning",
    "realtime_evaluation",
})

# Analytical/backoffice tasks use Gemini for synthesis quality
ANALYTICAL_TASKS = frozenset({
    "final_summary",
    "report_generation",
    "candidate_analysis",
    "post_interview_synthesis",
})


def get_recommended_provider(task_type: str) -> str:
    """Get the recommended provider based on task category."""
    normalized = task_type.strip().lower()
    if normalized in REALTIME_TASKS:
        return "groq"
    if normalized in ANALYTICAL_TASKS:
        return "gemini"
    return "groq"  # Default to Groq for unknown tasks


class LLMError(RuntimeError):
    """Base error for routed LLM failures."""


class LLMTimeoutError(LLMError):
    """Raised when a provider request times out."""


class LLMRateLimitError(LLMError):
    """Raised when a provider rate limit is hit."""


class LLMAuthError(LLMError):
    """Raised when provider authentication fails."""


class LLMValidationError(LLMError):
    """Raised when a response fails validation."""


@dataclass(frozen=True, slots=True)
class TaskRuntimeConfig:
    provider: str
    model: str
    timeout_seconds: float
    max_retries: int
    temperature: float
    max_tokens: int
    response_format: str


@dataclass(frozen=True, slots=True)
class ModelRuntimeConfig:
    groq_api_key: str
    gemini_api_key: str
    task_configs: dict[str, TaskRuntimeConfig]
    provider_priority: dict[str, tuple[str, ...]]
    health_failure_threshold: int = 3
    health_cooldown_seconds: int = 45
    semantic_similarity_threshold: float = 0.82
    followup_similarity_threshold: float = 0.72
    role_relevance_threshold: float = 0.12

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    def task_config(self, task_type: str) -> TaskRuntimeConfig:
        normalized = (task_type or DEFAULT_GENERIC_TASK).strip().lower() or DEFAULT_GENERIC_TASK
        return self.task_configs.get(normalized, self.task_configs[DEFAULT_GENERIC_TASK])

    def startup_warnings(self) -> list[str]:
        warnings: list[str] = []
        if not self.has_groq:
            warnings.append("GROQ_API_KEY is not configured; Groq tasks will use fallback routing.")
        if not self.has_gemini:
            warnings.append("GEMINI_API_KEY or GOOGLE_API_KEY is not configured; Gemini tasks will use fallback routing.")
        for task_name, task_config in self.task_configs.items():
            if task_config.timeout_seconds <= 0:
                warnings.append(f"{task_name} timeout must be positive; using configured value may cause immediate failures.")
            if task_config.max_retries < 1:
                warnings.append(f"{task_name} max_retries must be at least 1; current value may disable provider attempts.")
        return warnings

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "has_groq": self.has_groq,
            "has_gemini": self.has_gemini,
            "task_configs": {
                task_name: {
                    "provider": task_config.provider,
                    "model": task_config.model,
                    "timeout_seconds": task_config.timeout_seconds,
                    "max_retries": task_config.max_retries,
                    "temperature": task_config.temperature,
                    "max_tokens": task_config.max_tokens,
                    "response_format": task_config.response_format,
                }
                for task_name, task_config in self.task_configs.items()
            },
        }


def load_model_config() -> ModelRuntimeConfig:
    groq_model = _env_text("GROQ_MODEL", DEFAULT_GROQ_MODEL)
    gemini_model = _env_text("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    return ModelRuntimeConfig(
        groq_api_key=_sanitize_secret(os.getenv("GROQ_API_KEY")),
        gemini_api_key=_sanitize_secret(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        task_configs={
            # Realtime conversational tasks → Groq (recruiter-quality consistency)
            "question_generation": TaskRuntimeConfig(
                provider="groq",  # Changed from gemini
                model=_env_text("GROQ_QUESTION_MODEL", groq_model),
                timeout_seconds=_env_float("LLM_QUESTION_TIMEOUT_SECONDS", 8.0),  # Increased for quality
                max_retries=_env_int("LLM_QUESTION_MAX_RETRIES", 2),
                temperature=_env_float("LLM_QUESTION_TEMPERATURE", 0.35),
                max_tokens=_env_int("LLM_QUESTION_MAX_TOKENS", 360),
                response_format="json",
            ),
            "followup_generation": TaskRuntimeConfig(
                provider="groq",
                model=_env_text("GROQ_FOLLOWUP_MODEL", groq_model),
                timeout_seconds=_env_float("LLM_FOLLOWUP_TIMEOUT_SECONDS", 5.0),  # Increased for quality
                max_retries=_env_int("LLM_FOLLOWUP_MAX_RETRIES", 2),
                temperature=_env_float("LLM_FOLLOWUP_TEMPERATURE", 0.2),
                max_tokens=_env_int("LLM_FOLLOWUP_MAX_TOKENS", 120),
                response_format="text",
            ),
            "realtime_evaluation": TaskRuntimeConfig(
                provider="groq",
                model=_env_text("GROQ_REALTIME_MODEL", groq_model),
                timeout_seconds=_env_float("LLM_EVALUATION_TIMEOUT_SECONDS", 3.5),  # Increased for quality
                max_retries=_env_int("LLM_EVALUATION_MAX_RETRIES", 2),
                temperature=_env_float("LLM_EVALUATION_TEMPERATURE", 0.1),
                max_tokens=_env_int("LLM_EVALUATION_MAX_TOKENS", 420),
                response_format="json",
            ),
            # Greeting uses Groq for recruiter-quality conversational tone
            "greeting_generation": TaskRuntimeConfig(
                provider="groq",  # Changed from configurable gemini
                model=_env_text("GROQ_GREETING_MODEL", groq_model),
                timeout_seconds=_env_float("LLM_GREETING_TIMEOUT_SECONDS", 4.5),  # Increased for quality
                max_retries=_env_int("LLM_GREETING_MAX_RETRIES", 2),
                temperature=_env_float("LLM_GREETING_TEMPERATURE", 0.4),
                max_tokens=_env_int("LLM_GREETING_MAX_TOKENS", 140),
                response_format="text",
            ),
            # Analytical tasks → Gemini (synthesis quality)
            "final_summary": TaskRuntimeConfig(
                provider="gemini",
                model=_env_text("GEMINI_SUMMARY_MODEL", gemini_model),
                timeout_seconds=_env_float("LLM_SUMMARY_TIMEOUT_SECONDS", 15.0),  # Increased for analysis
                max_retries=_env_int("LLM_SUMMARY_MAX_RETRIES", 2),
                temperature=_env_float("LLM_SUMMARY_TEMPERATURE", 0.2),
                max_tokens=_env_int("LLM_SUMMARY_MAX_TOKENS", 1000),
                response_format="text",
            ),
            DEFAULT_GENERIC_TASK: TaskRuntimeConfig(
                provider=_env_text("LLM_GENERIC_PROVIDER", "groq").lower(),
                model=_env_text("LLM_GENERIC_MODEL", groq_model),
                timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 3.0),
                max_retries=_env_int("LLM_MAX_RETRIES", 2),
                temperature=_env_float("LLM_TEMPERATURE", 0.3),
                max_tokens=_env_int("LLM_MAX_TOKENS", 240),
                response_format="text",
            ),
        },
        provider_priority={
            # Realtime tasks prioritize Groq
            "question_generation": ("groq", "gemini"),
            "followup_generation": ("groq", "gemini"),
            "realtime_evaluation": ("groq",),
            "greeting_generation": ("groq", "gemini"),
            # Analytical tasks prioritize Gemini
            "final_summary": ("gemini", "groq"),
            DEFAULT_GENERIC_TASK: ("groq", "gemini"),
        },
        health_failure_threshold=_env_int("LLM_PROVIDER_FAILURE_THRESHOLD", 3),
        health_cooldown_seconds=_env_int("LLM_PROVIDER_COOLDOWN_SECONDS", 45),
        semantic_similarity_threshold=_env_float("LLM_SEMANTIC_SIMILARITY_THRESHOLD", 0.82),
        followup_similarity_threshold=_env_float("LLM_FOLLOWUP_SIMILARITY_THRESHOLD", 0.72),
        role_relevance_threshold=_env_float("LLM_ROLE_RELEVANCE_THRESHOLD", 0.12),
    )


def _sanitize_secret(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip().strip('"').strip("'").strip()


def _env_text(name: str, default: str) -> str:
    return (os.getenv(name, default) or default).strip() or default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default
