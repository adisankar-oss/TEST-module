from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class PromptEnvelope:
    system_prompt: str
    user_prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    task_type: str
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: float
    response_format: str
    prompt: PromptEnvelope


@dataclass(slots=True)
class ProviderResponse:
    provider: str
    model: str
    content: str
    raw_payload: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)


class LLMProvider(Protocol):
    provider_name: str

    @property
    def available(self) -> bool: ...

    async def generate(self, request: ProviderRequest) -> ProviderResponse: ...
