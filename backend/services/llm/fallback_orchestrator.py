from __future__ import annotations

import json
from typing import Any

from backend.services.llm.model_configs import DEFAULT_GENERIC_TASK, ModelRuntimeConfig, load_model_config
from backend.services.llm.provider_health_monitor import ProviderHealthMonitor
from backend.services.llm.providers.gemini_fallbacks import (
    final_summary_fallback,
    greeting_fallback,
    question_generation_fallback,
)
from backend.services.llm.providers.groq_fallbacks import (
    followup_generation_fallback,
    realtime_evaluation_fallback,
)


class FallbackOrchestrator:
    def __init__(
        self,
        config: ModelRuntimeConfig | None = None,
        health_monitor: ProviderHealthMonitor | None = None,
    ) -> None:
        self._config = config or load_model_config()
        self._health_monitor = health_monitor

    async def provider_sequence(self, task_type: str, primary_provider: str) -> list[str]:
        task_name = (task_type or DEFAULT_GENERIC_TASK).strip().lower() or DEFAULT_GENERIC_TASK
        preferred = list(self._config.provider_priority.get(task_name, (primary_provider,)))
        if primary_provider and primary_provider not in preferred:
            preferred.insert(0, primary_provider)

        sequence: list[str] = []
        for provider in preferred:
            if provider in sequence:
                continue
            if self._health_monitor is not None and not self._health_monitor.is_available(provider):
                continue
            sequence.append(provider)
        return sequence

    def deterministic_fallback(
        self,
        *,
        task_type: str,
        response_format: str,
        fallback_text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        metadata = metadata or {}
        if fallback_text.strip():
            return fallback_text.strip()

        if task_type == "question_generation":
            payload = question_generation_fallback(metadata)
            return json.dumps(payload) if response_format == "json" else payload["question"]
        if task_type == "followup_generation":
            return followup_generation_fallback(metadata)
        if task_type == "realtime_evaluation":
            return json.dumps(realtime_evaluation_fallback(metadata))
        if task_type == "final_summary":
            return final_summary_fallback(metadata)
        if task_type == "greeting_generation":
            return greeting_fallback(metadata)
        return str(metadata.get("fallback_text") or "").strip()
