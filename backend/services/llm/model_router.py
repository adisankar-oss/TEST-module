from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from backend.services.llm.fallback_orchestrator import FallbackOrchestrator
from backend.services.llm.latency_monitor import LatencyMonitor
from backend.services.llm.llm_interfaces import PromptEnvelope, ProviderRequest
from backend.services.llm.model_configs import LLMError, ModelRuntimeConfig, load_model_config
from backend.services.llm.model_registry import ModelRegistry
from backend.services.llm.provider_health_monitor import ProviderHealthMonitor
from backend.services.llm.response_validator import ResponseValidator
from backend.utils.logger import get_logger


@dataclass(slots=True)
class RoutedLLMResponse:
    content: str
    task_type: str
    provider: str
    model: str
    latency_ms: float
    attempts: int
    fallback_used: bool = False
    deterministic_fallback: bool = False
    usage: dict[str, int] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    parsed_payload: dict[str, Any] | None = None


class ModelRouter:
    def __init__(
        self,
        *,
        config: ModelRuntimeConfig | None = None,
        registry: ModelRegistry | None = None,
        validator: ResponseValidator | None = None,
        fallback_orchestrator: FallbackOrchestrator | None = None,
        latency_monitor: LatencyMonitor | None = None,
        health_monitor: ProviderHealthMonitor | None = None,
    ) -> None:
        self._config = config or load_model_config()
        self._health_monitor = health_monitor or ProviderHealthMonitor(
            failure_threshold=self._config.health_failure_threshold,
            cooldown_seconds=self._config.health_cooldown_seconds,
        )
        self._registry = registry or ModelRegistry(self._config)
        self._validator = validator or ResponseValidator(self._config)
        self._latency_monitor = latency_monitor or LatencyMonitor()
        self._fallback_orchestrator = fallback_orchestrator or FallbackOrchestrator(self._config, self._health_monitor)
        self._logger = get_logger("services.llm.model_router")
        self._emit_startup_warnings()

    async def generate(
        self,
        *,
        task_type: str,
        system_prompt: str,
        user_prompt: str,
        metadata: dict[str, Any] | None = None,
        fallback_text: str = "",
        explicit_provider: str | None = None,
        explicit_model: str | None = None,
        response_format: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        raise_on_failure: bool = False,
    ) -> RoutedLLMResponse:
        metadata = dict(metadata or {})
        task_config = self._registry.task_config(task_type)
        primary_provider = (explicit_provider or task_config.provider).strip().lower()
        providers = await self._fallback_orchestrator.provider_sequence(task_type, primary_provider)
        total_start = perf_counter()
        total_attempts = 0
        last_error: Exception | None = None
        last_validation_errors: list[str] = []

        for provider_index, provider_name in enumerate(providers):
            client = self._registry.get_provider(provider_name)
            if not client.available:
                self._health_monitor.record_failure(provider_name, "provider_unavailable")
                continue

            effective_model = explicit_model or (
                task_config.model if provider_name == task_config.provider else self._registry.task_config_for_provider(task_type, provider_name).model
            )
            request_model = ProviderRequest(
                task_type=task_type,
                model=effective_model,
                temperature=temperature if temperature is not None else task_config.temperature,
                max_tokens=max_tokens if max_tokens is not None else task_config.max_tokens,
                timeout_seconds=timeout_seconds if timeout_seconds is not None else task_config.timeout_seconds,
                response_format=(response_format or task_config.response_format).strip().lower(),
                prompt=PromptEnvelope(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    metadata=metadata,
                ),
            )

            for attempt in range(1, (max_retries if max_retries is not None else task_config.max_retries) + 1):
                total_attempts += 1
                attempt_start = perf_counter()
                try:
                    provider_response = await client.generate(request_model)
                    latency_ms = round((perf_counter() - attempt_start) * 1000, 2)
                    await self._latency_monitor.record(provider_name, task_type, latency_ms)

                    validation = self._validator.validate(
                        task_type=task_type,
                        content=provider_response.content,
                        response_format=request_model.response_format,
                        metadata=metadata,
                    )
                    if not validation.ok:
                        last_validation_errors = list(validation.errors)
                        raise LLMError(",".join(validation.errors))

                    self._health_monitor.record_success(provider_name)
                    total_latency_ms = round((perf_counter() - total_start) * 1000, 2)
                    self._logger.info(
                        json.dumps(
                            {
                                "event": "llm_request_succeeded",
                                "task_type": task_type,
                                "provider": provider_name,
                                "model": request_model.model,
                                "latency_ms": total_latency_ms,
                                "attempt": attempt,
                                "fallback_used": provider_index > 0,
                                "token_usage": provider_response.usage,
                            }
                        )
                    )
                    return RoutedLLMResponse(
                        content=validation.content,
                        task_type=task_type,
                        provider=provider_name,
                        model=request_model.model,
                        latency_ms=total_latency_ms,
                        attempts=total_attempts,
                        fallback_used=provider_index > 0,
                        deterministic_fallback=False,
                        usage=provider_response.usage,
                        validation_errors=[],
                        parsed_payload=validation.parsed_payload,
                    )
                except Exception as exc:
                    last_error = exc
                    self._health_monitor.record_failure(provider_name, str(exc))
                    attempt_latency_ms = round((perf_counter() - attempt_start) * 1000, 2)
                    await self._latency_monitor.record(provider_name, task_type, attempt_latency_ms)
                    self._logger.warning(
                        json.dumps(
                            {
                                "event": "llm_request_failed",
                                "task_type": task_type,
                                "provider": provider_name,
                                "model": request_model.model,
                                "attempt": attempt,
                                "latency_ms": attempt_latency_ms,
                                "fallback_candidate": providers[provider_index + 1] if provider_index + 1 < len(providers) else "deterministic",
                                "validation_errors": last_validation_errors,
                                "error": str(exc),
                            }
                        )
                    )
                    if attempt < (max_retries if max_retries is not None else task_config.max_retries):
                        await asyncio.sleep(min(0.25 * attempt, 0.75))

        fallback = self._fallback_orchestrator.deterministic_fallback(
            task_type=task_type,
            response_format=(response_format or task_config.response_format).strip().lower(),
            fallback_text=fallback_text,
            metadata=metadata,
        )
        validation = self._validator.validate(
            task_type=task_type,
            content=fallback,
            response_format=(response_format or task_config.response_format).strip().lower(),
            metadata=metadata,
        )
        if validation.ok:
            total_latency_ms = round((perf_counter() - total_start) * 1000, 2)
            self._logger.warning(
                json.dumps(
                    {
                        "event": "llm_deterministic_fallback_used",
                        "task_type": task_type,
                        "provider": "deterministic",
                        "latency_ms": total_latency_ms,
                        "attempts": total_attempts,
                        "validation_errors": last_validation_errors,
                        "error": str(last_error) if last_error else "",
                    }
                )
            )
            return RoutedLLMResponse(
                content=validation.content,
                task_type=task_type,
                provider="deterministic",
                model="deterministic",
                latency_ms=total_latency_ms,
                attempts=total_attempts,
                fallback_used=True,
                deterministic_fallback=True,
                usage={},
                validation_errors=last_validation_errors,
                parsed_payload=validation.parsed_payload,
            )

        if raise_on_failure:
            raise last_error or LLMError(f"Failed to generate {task_type}")
        raise LLMError(f"Failed to generate {task_type}")

    async def health_snapshot(self) -> dict[str, Any]:
        return {
            "provider_health": self._health_monitor.snapshot(),
            "latency": {
                key: {
                    "count": value.count,
                    "average_ms": value.average_ms,
                    "max_ms": value.max_ms,
                }
                for key, value in (await self._latency_monitor.snapshot()).items()
            },
        }

    def _emit_startup_warnings(self) -> None:
        for warning in self._config.startup_warnings():
            self._logger.warning(json.dumps({"event": "llm_startup_warning", "warning": warning}))


_ROUTER_SINGLETON: ModelRouter | None = None


def get_model_router() -> ModelRouter:
    global _ROUTER_SINGLETON
    if _ROUTER_SINGLETON is None:
        _ROUTER_SINGLETON = ModelRouter()
    return _ROUTER_SINGLETON
