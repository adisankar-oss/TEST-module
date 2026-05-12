from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.evaluation_service import EvaluationService
from backend.services.llm.llm_interfaces import ProviderRequest, ProviderResponse
from backend.services.llm.model_configs import (
    DEFAULT_GENERIC_TASK,
    LLMTimeoutError,
    ModelRuntimeConfig,
    TaskRuntimeConfig,
)
from backend.services.llm.model_registry import ModelRegistry
from backend.services.llm.model_router import ModelRouter
from backend.services.llm.response_validator import ResponseValidator
from backend.services.llm.task_router import TaskRouter
from backend.services.question_service import QuestionService


class StubProvider:
    def __init__(self, provider_name: str, responses: list[ProviderResponse | Exception], *, available: bool = True) -> None:
        self.provider_name = provider_name
        self._responses = list(responses)
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        await asyncio.sleep(0)
        response = self._responses[0] if len(self._responses) == 1 else self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class StubQuestionBank:
    def __init__(self) -> None:
        self.marked: list[str] = []

    def mark_used(self, question: str, *, session_id: str) -> None:
        self.marked.append(question)

    def get_question(self, topic: str, role_level: str, *, session_id: str, asked_questions: list[str]) -> str:
        return "How do you decide between synchronous and asynchronous APIs in a backend service?"

    def get_adaptive_question(self, **kwargs) -> object:
        raise AssertionError("adaptive fallback should not be called in this test")


class StubTaskRouter:
    def __init__(self, question: str) -> None:
        self._question = question

    async def generate_question(self, **kwargs):
        return {
            "question": self._question,
            "topic": kwargs.get("selected_topic", "technical_skills"),
            "type": "new",
            "reasoning": "test",
        }

    async def generate_greeting(self, *, candidate_name: str, role: str) -> tuple[str, str]:
        return f"Hi {candidate_name}, could you introduce yourself for the {role} interview?", "test_provider"

    async def generate_followup(self, **kwargs) -> str:
        return "You mentioned caching. What trade-offs did you consider before using it?"


class StubEvaluationTaskRouter:
    async def evaluate_realtime(self, **kwargs):
        return {
            "score": 7,
            "strengths": ["clear structure"],
            "weaknesses": ["needs_more_specificity"],
            "followup_required": True,
            "followup_reason": "INCOMPLETE_EXPLANATION",
            "confidence_score": 0.64,
            "decision": "followup",
            "feedback": "The answer was relevant but not specific enough.",
            "semantic_topic": "system design",
        }


def make_config() -> ModelRuntimeConfig:
    return ModelRuntimeConfig(
        groq_api_key="groq-key",
        gemini_api_key="gemini-key",
        task_configs={
            "question_generation": TaskRuntimeConfig("gemini", "gemini-1.5-flash", 3.0, 2, 0.3, 300, "json"),
            "followup_generation": TaskRuntimeConfig("groq", "llama-3.3-70b-versatile", 2.0, 2, 0.2, 120, "text"),
            "realtime_evaluation": TaskRuntimeConfig("groq", "llama-3.3-70b-versatile", 1.5, 2, 0.1, 420, "json"),
            "final_summary": TaskRuntimeConfig("gemini", "gemini-1.5-flash", 8.0, 2, 0.2, 900, "text"),
            "greeting_generation": TaskRuntimeConfig("gemini", "gemini-1.5-flash", 2.5, 2, 0.4, 120, "text"),
            DEFAULT_GENERIC_TASK: TaskRuntimeConfig("groq", "llama-3.3-70b-versatile", 3.0, 2, 0.3, 200, "text"),
        },
        provider_priority={
            "question_generation": ("gemini", "groq"),
            "followup_generation": ("groq", "gemini"),
            "realtime_evaluation": ("groq",),
            "final_summary": ("gemini", "groq"),
            "greeting_generation": ("gemini", "groq"),
            DEFAULT_GENERIC_TASK: ("groq", "gemini"),
        },
    )


def make_registry(gemini_provider: StubProvider, groq_provider: StubProvider) -> ModelRegistry:
    registry = ModelRegistry(make_config())
    registry.register_provider("gemini", gemini_provider)
    registry.register_provider("groq", groq_provider)
    return registry


def test_gemini_question_generation_falls_back_to_groq():
    async def _run() -> None:
        registry = make_registry(
            StubProvider("gemini", [LLMTimeoutError("timeout")]),
            StubProvider(
                "groq",
                [
                    ProviderResponse(
                        provider="groq",
                        model="llama-3.3-70b-versatile",
                        content='{"question":"How would you design a resilient API for a backend service?","type":"new","topic":"technical_skills","reasoning":"valid"}',
                    )
                ],
            ),
        )
        router = ModelRouter(config=make_config(), registry=registry)

        result = await router.generate(
            task_type="question_generation",
            system_prompt="system",
            user_prompt="user",
            metadata={"role": "backend engineer", "topic": "technical_skills", "previous_questions": []},
        )

        assert result.provider == "groq"
        assert result.fallback_used is True

    asyncio.run(_run())


def test_groq_failure_uses_deterministic_realtime_fallback():
    async def _run() -> None:
        registry = make_registry(
            StubProvider("gemini", []),
            StubProvider("groq", [LLMTimeoutError("timeout"), LLMTimeoutError("timeout")]),
        )
        router = ModelRouter(config=make_config(), registry=registry)

        result = await router.generate(
            task_type="realtime_evaluation",
            system_prompt="system",
            user_prompt="user",
            metadata={"semantic_topic": "system design"},
        )

        assert result.provider == "deterministic"
        assert result.deterministic_fallback is True
        assert '"score": 5' in result.content

    asyncio.run(_run())


def test_malformed_question_response_is_rejected_then_recovers():
    async def _run() -> None:
        registry = make_registry(
            StubProvider(
                "gemini",
                [ProviderResponse(provider="gemini", model="gemini-1.5-flash", content='{"question":"repeat","topic":"technical_skills"}')],
            ),
            StubProvider(
                "groq",
                [
                    ProviderResponse(
                        provider="groq",
                        model="llama-3.3-70b-versatile",
                        content='{"question":"How would you design a rollback strategy for a risky schema migration?","type":"new","topic":"technical_skills","reasoning":"valid"}',
                    )
                ],
            ),
        )
        router = ModelRouter(config=make_config(), registry=registry)

        result = await router.generate(
            task_type="question_generation",
            system_prompt="system",
            user_prompt="user",
            metadata={
                "role": "backend engineer",
                "topic": "technical_skills",
                "previous_questions": ["How do you reason about database indexes under write-heavy load?"],
            },
        )

        assert result.provider == "groq"

    asyncio.run(_run())


def test_followup_validator_blocks_repetition():
    validator = ResponseValidator(make_config())
    result = validator.validate(
        task_type="followup_generation",
        content="Could you explain that cache strategy in more detail?",
        response_format="text",
        metadata={
            "original_question": "Could you explain that cache strategy in more detail?",
            "candidate_answer": "We used Redis for cache invalidation and expiry.",
            "recent_followups": ["Could you explain that cache strategy in more detail?"],
        },
    )

    assert result.ok is False
    assert "followup_repeats_original" in result.errors or "semantic_duplicate_followup" in result.errors


def test_latency_snapshot_updates_after_success():
    async def _run() -> None:
        registry = make_registry(
            StubProvider(
                "gemini",
                [
                    ProviderResponse(
                        provider="gemini",
                        model="gemini-1.5-flash",
                        content='{"question":"How do you evaluate API versioning trade-offs?","type":"new","topic":"technical_skills","reasoning":"valid"}',
                    )
                ],
            ),
            StubProvider("groq", []),
        )
        router = ModelRouter(config=make_config(), registry=registry)

        await router.generate(
            task_type="question_generation",
            system_prompt="system",
            user_prompt="user",
            metadata={"role": "backend engineer", "topic": "technical_skills", "previous_questions": []},
        )
        snapshot = await router.health_snapshot()

        assert "gemini:question_generation" in snapshot["latency"]
        assert snapshot["latency"]["gemini:question_generation"]["count"] >= 1

    asyncio.run(_run())


def test_model_router_is_concurrency_safe():
    async def _run() -> None:
        registry = make_registry(
            StubProvider("gemini", []),
            StubProvider(
                "groq",
                [
                    ProviderResponse(
                        provider="groq",
                        model="llama-3.3-70b-versatile",
                        content='{"score":7,"strengths":["clear"],"weaknesses":["needs_more_specificity"],"followup_required":true,"followup_reason":"INCOMPLETE_EXPLANATION","confidence_score":0.66,"decision":"followup","feedback":"Needs more detail.","semantic_topic":"system design"}',
                    )
                    for _ in range(5)
                ],
            ),
        )
        router = ModelRouter(config=make_config(), registry=registry)

        results = await asyncio.gather(
            *[
                router.generate(
                    task_type="realtime_evaluation",
                    system_prompt="system",
                    user_prompt=f"user-{index}",
                    metadata={"semantic_topic": "system design"},
                )
                for index in range(5)
            ]
        )

        assert len(results) == 5
        assert all(result.provider == "groq" for result in results)

    asyncio.run(_run())


def test_question_service_avoids_duplicate_question_with_fallback():
    async def _run() -> None:
        service = QuestionService(
            question_bank_service=StubQuestionBank(),
            task_router=StubTaskRouter("How do you scale a Python API?"),
        )
        session = type(
            "Session",
            (),
            {
                "id": "sess-1",
                "session_id": "sess-1",
                "job_id": "backend_engineer",
                "current_question_number": 2,
                "max_questions": 5,
                "role_level": "mid",
                "config": {
                    "question_history": [
                        {"question": "How do you scale a Python API?", "answer": "With caching.", "score": 7, "topic": "technical_skills"}
                    ]
                },
                "memory": None,
            },
        )()

        result = await service.generate_question_with_fallback(session)

        assert result.source == "fallback"
        assert result.question != "How do you scale a Python API?"

    asyncio.run(_run())


def test_evaluation_service_maps_structured_payload():
    async def _run() -> None:
        service = EvaluationService(task_router=StubEvaluationTaskRouter())

        result = await service.evaluate_answer(
            question="How did you design that system?",
            answer="We broke the service into smaller APIs and added metrics.",
            context=[{"role_level": "mid"}],
        )

        assert result.score == 7
        assert result.followup_required is True
        assert result.semantic_topic == "system design"

    asyncio.run(_run())
