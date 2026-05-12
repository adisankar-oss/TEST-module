from __future__ import annotations

import asyncio
import json
import socket
from typing import Any
from urllib import error, request

from backend.services.llm.llm_interfaces import LLMProvider, ProviderRequest, ProviderResponse
from backend.services.llm.model_configs import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    ModelRuntimeConfig,
    load_model_config,
)
from backend.services.llm.provider_health_monitor import health_monitor


GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqClient(LLMProvider):
    provider_name = "groq"

    def __init__(self, config: ModelRuntimeConfig | None = None) -> None:
        self._config = config or load_model_config()

    @property
    def available(self) -> bool:
        return bool(self._config.groq_api_key)

    async def generate(self, request_model: ProviderRequest) -> ProviderResponse:
        if not self.available:
            raise LLMAuthError("GROQ_API_KEY is not configured")

        try:
            payload: dict[str, Any] = {
                "model": request_model.model,
                "messages": [
                    {"role": "system", "content": request_model.prompt.system_prompt},
                    {"role": "user", "content": request_model.prompt.user_prompt},
                ],
                "temperature": request_model.temperature,
                "max_tokens": request_model.max_tokens,
            }
            if request_model.response_format == "json":
                payload["response_format"] = {"type": "json_object"}

            response_payload = await asyncio.to_thread(self._post_json, payload, request_model.timeout_seconds)
            content = self._extract_content(response_payload)
            if not content:
                raise LLMError("Groq response did not contain message content")

            health_monitor.record_success("groq")
            return ProviderResponse(
                provider=self.provider_name,
                model=request_model.model,
                content=content.strip(),
                raw_payload=response_payload,
                usage=_normalize_usage(response_payload.get("usage")),
            )
        except Exception as e:
            health_monitor.record_failure("groq", str(e))
            raise

    def _post_json(self, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            GROQ_API_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self._config.groq_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "ai-interview-avatar/1.0",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                raw_body = response.read().decode("utf-8", errors="replace")
                return _safe_json_loads(raw_body)
        except error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403}:
                raise LLMAuthError(f"Groq authentication failed ({exc.code})") from exc
            if exc.code == 429:
                raise LLMRateLimitError("Groq rate limit reached") from exc
            raise LLMError(f"Groq HTTP {exc.code}: {raw_body[:200]}") from exc
        except error.URLError as exc:
            raise LLMError(f"Groq network error: {exc.reason}") from exc
        except socket.timeout as exc:
            raise LLMTimeoutError("Groq request timed out") from exc

    @staticmethod
    def _extract_content(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        choice = choices[0]
        if not isinstance(choice, dict):
            return ""
        message = choice.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        return content if isinstance(content, str) else ""


def _safe_json_loads(raw_body: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    usage: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        try:
            usage[key] = int(value.get(key, 0))
        except (TypeError, ValueError):
            continue
    return usage
