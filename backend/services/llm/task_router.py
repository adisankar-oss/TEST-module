from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.services.llm.model_configs import DEFAULT_GENERIC_TASK, ModelRuntimeConfig, TaskRuntimeConfig, load_model_config
from backend.services.llm.model_router import ModelRouter, get_model_router
from backend.services.llm.providers.gemini_fallbacks import greeting_fallback, question_generation_fallback
from backend.services.llm.providers.gemini_prompts import (
    build_final_summary_prompts,
    build_greeting_prompts,
    build_question_generation_prompts,
)
from backend.services.llm.providers.gemini_response_parser import parse_question_generation, parse_summary_text
from backend.services.llm.providers.groq_fallbacks import followup_generation_fallback, realtime_evaluation_fallback
from backend.services.llm.providers.groq_prompts import (
    build_followup_generation_prompts,
    build_realtime_evaluation_prompts,
)
from backend.services.llm.providers.groq_response_parser import parse_followup_text, parse_realtime_evaluation


TASK_MODEL_MAP = {
    "question_generation": "gemini",
    "followup_generation": "groq",
    "realtime_evaluation": "groq",
    "final_summary": "gemini",
    "greeting_generation": "gemini",
    DEFAULT_GENERIC_TASK: "groq",
}


@dataclass(frozen=True, slots=True)
class TaskRoute:
    task_type: str
    provider: str
    model: str
    timeout_seconds: float
    max_retries: int
    temperature: float
    max_tokens: int
    response_format: str


def resolve_route(
    task_type: str,
    *,
    config: ModelRuntimeConfig | None = None,
    explicit_provider: str | None = None,
    explicit_model: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: str | None = None,
) -> TaskRoute:
    resolved_config = config or load_model_config()
    defaults = resolved_config.task_config(task_type)
    provider = (explicit_provider or defaults.provider or TASK_MODEL_MAP.get(task_type, "groq")).strip().lower()
    return TaskRoute(
        task_type=(task_type or DEFAULT_GENERIC_TASK).strip().lower() or DEFAULT_GENERIC_TASK,
        provider=provider,
        model=(explicit_model or defaults.model).strip(),
        timeout_seconds=timeout_seconds if timeout_seconds is not None else defaults.timeout_seconds,
        max_retries=max_retries if max_retries is not None else defaults.max_retries,
        temperature=temperature if temperature is not None else defaults.temperature,
        max_tokens=max_tokens if max_tokens is not None else defaults.max_tokens,
        response_format=(response_format or defaults.response_format).strip().lower(),
    )


class TaskRouter:
    def __init__(
        self,
        *,
        config: ModelRuntimeConfig | None = None,
        model_router: ModelRouter | None = None,
    ) -> None:
        self._config = config or load_model_config()
        self._model_router = model_router or get_model_router()

    async def generate_question(self, **kwargs: Any) -> dict[str, str]:
        system_prompt, user_prompt = build_question_generation_prompts(**kwargs)
        response = await self._model_router.generate(
            task_type="question_generation",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={
                "role": kwargs.get("role", ""),
                "job_id": kwargs.get("role", ""),
                "topic": kwargs.get("selected_topic", ""),
                "previous_questions": kwargs.get("previous_questions", []),
                "fallback_question": kwargs.get("fallback_question", question_generation_fallback({})["question"]),
            },
        )
        return parse_question_generation(response.content, {
            "fallback_question": kwargs.get("fallback_question", ""),
            "topic": kwargs.get("selected_topic", ""),
        })

    async def generate_followup(self, **kwargs: Any) -> str:
        system_prompt, user_prompt = build_followup_generation_prompts(**kwargs)
        response = await self._model_router.generate(
            task_type="followup_generation",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={
                "original_question": kwargs.get("original_question", ""),
                "candidate_answer": kwargs.get("candidate_answer", ""),
                "recent_followups": kwargs.get("recent_followups", []),
                "followup_reason": kwargs.get("followup_reason", ""),
                "anchor": kwargs.get("session_memory", {}).get("anchor", ""),
                "contradiction": kwargs.get("contradiction", ""),
            },
            fallback_text=followup_generation_fallback(
                {
                    "followup_reason": kwargs.get("followup_reason", ""),
                    "anchor": kwargs.get("session_memory", {}).get("anchor", ""),
                    "contradiction": kwargs.get("contradiction", ""),
                }
            ),
        )
        return parse_followup_text(response.content)

    async def evaluate_realtime(self, **kwargs: Any) -> dict[str, Any]:
        system_prompt, user_prompt = build_realtime_evaluation_prompts(**kwargs)
        response = await self._model_router.generate(
            task_type="realtime_evaluation",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={
                "semantic_topic": kwargs.get("context", [{}])[-1].get("semantic_topic", "general") if kwargs.get("context") else "general",
            },
            fallback_text="",
        )
        return parse_realtime_evaluation(
            response.content,
            {
                "semantic_topic": kwargs.get("context", [{}])[-1].get("semantic_topic", "general") if kwargs.get("context") else "general",
            },
        )

    async def generate_final_summary(self, **kwargs: Any) -> str:
        system_prompt, user_prompt = build_final_summary_prompts(**kwargs)
        response = await self._model_router.generate(
            task_type="final_summary",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={"candidate_name": kwargs.get("candidate_name", "")},
        )
        return parse_summary_text(response.content)

    async def generate_greeting(self, *, candidate_name: str, role: str) -> tuple[str, str]:
        system_prompt, user_prompt = build_greeting_prompts(candidate_name=candidate_name, role=role)
        response = await self._model_router.generate(
            task_type="greeting_generation",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            metadata={"candidate_name": candidate_name, "role": role},
            fallback_text=greeting_fallback({"candidate_name": candidate_name, "role": role}),
        )
        return parse_summary_text(response.content), response.provider


_TASK_ROUTER_SINGLETON: TaskRouter | None = None


def get_task_router() -> TaskRouter:
    global _TASK_ROUTER_SINGLETON
    if _TASK_ROUTER_SINGLETON is None:
        _TASK_ROUTER_SINGLETON = TaskRouter()
    return _TASK_ROUTER_SINGLETON
