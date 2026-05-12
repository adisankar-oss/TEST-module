from __future__ import annotations

import asyncio
import logging
from typing import Any

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
from backend.services.llm.providers.gemini_sdk import (
    GeminiSDKUnavailableError,
    create_gemini_adapter,
    extract_response_text,
    extract_usage,
)

logger = logging.getLogger(__name__)


class GeminiClient(LLMProvider):
    provider_name = "gemini"

    def __init__(self, config: ModelRuntimeConfig | None = None) -> None:
        self._config = config or load_model_config()
        self._client = None
        if self._config.gemini_api_key:
            try:
                self._client = create_gemini_adapter(self._config.gemini_api_key)
            except GeminiSDKUnavailableError:
                logger.warning("Gemini SDK unavailable; Gemini provider disabled")

    @property
    def available(self) -> bool:
        return bool(self._config.gemini_api_key) and self._client is not None

    async def generate(self, request_model: ProviderRequest) -> ProviderResponse:
        if not self.available:
            raise LLMAuthError("GEMINI_API_KEY or GOOGLE_API_KEY is not configured")

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self._client.generate_content,
                    model=request_model.model,
                    contents=request_model.prompt.user_prompt,
                    temperature=request_model.temperature,
                    max_output_tokens=request_model.max_tokens,
                    response_mime_type="application/json" if request_model.response_format == "json" else None,
                    system_instruction=request_model.prompt.system_prompt,
                ),
                timeout=request_model.timeout_seconds,
            )

            content = extract_response_text(response)
            if not content:
                raise LLMError("Gemini response did not contain content")

            health_monitor.record_success("gemini")

            return ProviderResponse(
                provider=self.provider_name,
                model=request_model.model,
                content=content.strip(),
                raw_payload={},
                usage=extract_usage(response),
            )

        except asyncio.TimeoutError as e:
            logger.error("GeminiTimeout model=%s timeout=%s", request_model.model, request_model.timeout_seconds)
            health_monitor.record_failure("gemini", "timeout")
            raise LLMTimeoutError(f"Gemini request timed out after {request_model.timeout_seconds}s") from e
        except Exception as e:
            error_msg = str(e).lower()
            if "api key" in error_msg or "auth" in error_msg or "permission" in error_msg:
                health_monitor.record_failure("gemini", "auth_error")
                raise LLMAuthError(f"Gemini authentication failed: {e}") from e
            if "rate limit" in error_msg or "429" in error_msg:
                health_monitor.record_failure("gemini", "rate_limit")
                raise LLMRateLimitError(f"Gemini rate limit reached: {e}") from e
            health_monitor.record_failure("gemini", str(e))
            raise LLMError(f"Gemini request failed: {e}") from e


def create_gemini_client(config: ModelRuntimeConfig | None = None) -> GeminiClient:
    """Factory function to create Gemini client."""
    return GeminiClient(config=config)
