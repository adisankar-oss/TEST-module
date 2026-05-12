from __future__ import annotations

from dataclasses import dataclass

from backend.services.llm.model_configs import DEFAULT_GROQ_MODEL
from backend.services.llm.model_router import get_model_router


DEFAULT_MODEL = DEFAULT_GROQ_MODEL


@dataclass(slots=True)
class AIClientConfig:
    api_key: str = ""
    model: str = DEFAULT_MODEL
    timeout_seconds: int = 3
    max_retries: int = 2


class AIClient:
    """Compatibility shim over the routed LLM stack."""

    def __init__(self, config: AIClientConfig | None = None) -> None:
        self._config = config or AIClientConfig()

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 240,
        fallback_text: str = "",
    ) -> str:
        response = await get_model_router().generate(
            task_type="generic_text",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            explicit_model=self._config.model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=float(self._config.timeout_seconds),
            max_retries=self._config.max_retries,
            fallback_text=fallback_text,
        )
        return response.content
