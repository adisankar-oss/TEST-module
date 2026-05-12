from __future__ import annotations

from dataclasses import dataclass

from backend.services.llm.llm_interfaces import LLMProvider
from backend.services.llm.model_configs import DEFAULT_GENERIC_TASK, ModelRuntimeConfig, TaskRuntimeConfig, load_model_config
from backend.services.llm.providers.gemini_client import GeminiClient
from backend.services.llm.providers.groq_client import GroqClient


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    name: str
    client: LLMProvider


class ModelRegistry:
    def __init__(self, config: ModelRuntimeConfig | None = None) -> None:
        self._config = config or load_model_config()
        self._providers: dict[str, LLMProvider] = {}
        self.register_provider("groq", GroqClient(self._config))
        self.register_provider("gemini", GeminiClient(self._config))

    def register_provider(self, name: str, client: LLMProvider) -> None:
        self._providers[name.strip().lower()] = client

    def get_provider(self, name: str) -> LLMProvider:
        return self._providers[name.strip().lower()]

    def task_config(self, task_type: str) -> TaskRuntimeConfig:
        return self._config.task_config(task_type)

    def task_config_for_provider(self, task_type: str, provider: str) -> TaskRuntimeConfig:
        base = self.task_config(task_type)
        fallback_model = base.model
        if provider == "groq":
            fallback_model = self._config.task_config("realtime_evaluation").model
        elif provider == "gemini":
            fallback_model = self._config.task_config("question_generation").model
        return TaskRuntimeConfig(
            provider=provider,
            model=fallback_model,
            timeout_seconds=base.timeout_seconds,
            max_retries=base.max_retries,
            temperature=base.temperature,
            max_tokens=base.max_tokens,
            response_format=base.response_format,
        )

    def primary_provider(self, task_type: str) -> str:
        return self.task_config(task_type).provider

    def task_priority(self, task_type: str) -> tuple[str, ...]:
        normalized = (task_type or DEFAULT_GENERIC_TASK).strip().lower() or DEFAULT_GENERIC_TASK
        return self._config.provider_priority.get(normalized, (self.primary_provider(normalized),))

    @property
    def config(self) -> ModelRuntimeConfig:
        return self._config
