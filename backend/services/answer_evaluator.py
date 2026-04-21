from __future__ import annotations

import asyncio
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from services.ai_client import AIClient, AIClientConfig, DEFAULT_MODEL
from utils.logger import get_logger


MIN_ANSWER_WORDS = 20
RELEVANCE_MAX = 25
DEPTH_MAX = 25
TECHNICAL_MAX = 25
COMMUNICATION_MAX = 15
MAX_RED_FLAG_PENALTY = 10
FALLBACK_FEEDBACK = "The answer is partially relevant but needs more depth, structure, and technical detail."
JSON_BLOCK_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_+#.-]+")
RED_FLAG_PENALTIES = {
    "too_short": 6,
    "no_keyword_match": 4,
    "contradiction": 5,
    "unprofessional_tone": 5,
    "vague": 3,
}
UNPROFESSIONAL_TERMS = {"stupid", "idiot", "dumb", "hate", "useless", "whatever"}
DEFAULT_EVALUATOR_MODEL = "openai/gpt-oss-120b"


class AnswerEvaluator:
    """Evaluate interview answers with LLM-backed scoring and deterministic safeguards."""

    def __init__(self, ai_client: AIClient | None = None) -> None:
        self._ai_client = ai_client or AIClient(
            AIClientConfig(
                api_key=os.getenv("GROQ_API_KEY", ""),
                model=self._resolve_model(),
            )
        )
        self._logger = get_logger("services.answer_evaluator")

    def evaluate(
        self,
        question: str,
        answer: str,
        keywords: list[str],
        role_level: str = "fresher",
    ) -> dict[str, Any]:
        """Return a structured evaluation result for a candidate answer."""
        normalized_question = self._normalize_text(question)
        normalized_answer = self._normalize_text(answer)
        normalized_keywords = [self._normalize_text(keyword) for keyword in keywords if self._normalize_text(keyword)]
        normalized_role_level = self._normalize_text(role_level).lower() or "fresher"

        fallback = self._fallback_response()
        if not normalized_question or not normalized_answer:
            return self._post_process_scores(
                fallback,
                answer=normalized_answer,
                keywords=normalized_keywords,
                role_level=normalized_role_level,
            )

        prompt = self._build_prompt(
            question=normalized_question,
            answer=normalized_answer,
            keywords=normalized_keywords,
            role_level=normalized_role_level,
        )

        try:
            raw_response = self._generate(prompt)
            parsed = self._parse_response(raw_response)
        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "answer_evaluator_error",
                        "error": str(exc),
                    }
                )
            )
            parsed = fallback

        return self._post_process_scores(
            parsed,
            answer=normalized_answer,
            keywords=normalized_keywords,
            role_level=normalized_role_level,
        )

    def _build_prompt(
        self,
        *,
        question: str,
        answer: str,
        keywords: list[str],
        role_level: str,
    ) -> str:
        """Build the strict evaluation prompt for the LLM."""
        return (
            "Evaluate this interview answer strictly and return JSON only.\n"
            "Role level: " + role_level + "\n"
            "Question:\n" + question + "\n\n"
            "Candidate Answer:\n" + answer + "\n\n"
            "Expected Keywords:\n" + json.dumps(keywords, ensure_ascii=True) + "\n\n"
            "Scoring dimensions:\n"
            f"- relevance: 0-{RELEVANCE_MAX}\n"
            f"- depth: 0-{DEPTH_MAX}\n"
            f"- technical: 0-{TECHNICAL_MAX}\n"
            f"- communication: 0-{COMMUNICATION_MAX}\n"
            "- red_flags: list of strings, deduct up to 10 total\n\n"
            "Evaluation rules:\n"
            "- Penalize vague answers.\n"
            "- Reward concrete examples, metrics, and structured thinking.\n"
            "- Detect contradictions or unprofessional tone.\n"
            "- Penalize answers under 20 words.\n"
            "- Keep feedback concise and actionable.\n\n"
            "Return JSON only with exactly these keys:\n"
            "{\n"
            '  "relevance_score": 0,\n'
            '  "depth_score": 0,\n'
            '  "technical_score": 0,\n'
            '  "communication_score": 0,\n'
            '  "red_flags": [],\n'
            '  "brief_feedback": "",\n'
            '  "needs_followup": false,\n'
            '  "followup_reason": ""\n'
            "}"
        )

    def _parse_response(self, raw_response: str) -> dict[str, Any]:
        """Safely parse the LLM response into a structured dictionary."""
        if not raw_response:
            raise ValueError("Empty evaluation response")

        match = JSON_BLOCK_PATTERN.search(raw_response)
        if match is None:
            raise ValueError("Evaluation response did not contain JSON")

        payload = json.loads(match.group(0))
        if not isinstance(payload, dict):
            raise ValueError("Evaluation response JSON was not an object")

        required_fields = {
            "relevance_score",
            "depth_score",
            "technical_score",
            "communication_score",
            "red_flags",
            "brief_feedback",
            "needs_followup",
            "followup_reason",
        }
        if not required_fields.issubset(payload):
            missing = sorted(required_fields.difference(payload))
            raise ValueError(f"Evaluation response missing fields: {missing}")

        return payload

    def _post_process_scores(
        self,
        payload: dict[str, Any],
        *,
        answer: str,
        keywords: list[str],
        role_level: str,
    ) -> dict[str, Any]:
        """Validate, normalize, and enrich scores before returning them."""
        relevance = self._clamp_int(payload.get("relevance_score"), 0, RELEVANCE_MAX)
        depth = self._clamp_int(payload.get("depth_score"), 0, DEPTH_MAX)
        technical = self._clamp_int(payload.get("technical_score"), 0, TECHNICAL_MAX)
        communication = self._clamp_int(payload.get("communication_score"), 0, COMMUNICATION_MAX)
        red_flags = self._sanitize_red_flags(payload.get("red_flags"))
        feedback = self._normalize_text(payload.get("brief_feedback") or FALLBACK_FEEDBACK)
        followup_reason = self._normalize_text(payload.get("followup_reason") or "")
        needs_followup = bool(payload.get("needs_followup"))

        word_count = len(answer.split())
        if word_count < MIN_ANSWER_WORDS:
            red_flags = self._add_red_flag(red_flags, "too_short")
            depth = max(0, depth - 8)
            communication = max(0, communication - 4)

        keyword_overlap = self._keyword_overlap(answer, keywords)
        if keywords and not keyword_overlap:
            red_flags = self._add_red_flag(red_flags, "no_keyword_match")
            technical = max(0, technical - 6)

        answer_lower = answer.lower()
        if any(term in answer_lower for term in UNPROFESSIONAL_TERMS):
            red_flags = self._add_red_flag(red_flags, "unprofessional_tone")
            communication = max(0, communication - 5)

        role_adjustment = self._role_level_adjustment(role_level)
        depth = self._clamp_int(depth + role_adjustment["depth"], 0, DEPTH_MAX)
        technical = self._clamp_int(technical + role_adjustment["technical"], 0, TECHNICAL_MAX)

        penalty = min(
            MAX_RED_FLAG_PENALTY,
            sum(RED_FLAG_PENALTIES.get(flag, 2) for flag in red_flags),
        )
        overall_score = max(0, relevance + depth + technical + communication - penalty)

        if not feedback:
            feedback = FALLBACK_FEEDBACK

        if not followup_reason:
            followup_reason = self._derive_followup_reason(red_flags, keyword_overlap, overall_score)

        needs_followup = needs_followup or bool(red_flags) or overall_score < 55 or len(keyword_overlap) == 0

        return {
            "relevance_score": relevance,
            "depth_score": depth,
            "technical_score": technical,
            "communication_score": communication,
            "red_flags": red_flags,
            "overall_score": overall_score,
            "brief_feedback": feedback,
            "needs_followup": needs_followup,
            "followup_reason": followup_reason,
        }

    def _generate(self, prompt: str) -> str:
        """Run the LLM call through the existing AI client."""
        if hasattr(self._ai_client, "generate"):
            return self._ai_client.generate(prompt)

        if not hasattr(self._ai_client, "generate_text"):
            raise RuntimeError("AI client does not support text generation")

        coroutine = self._ai_client.generate_text(
            system_prompt=(
                "You are a strict interview answer evaluator. "
                "Return JSON only. No markdown, no prose outside JSON."
            ),
            user_prompt=prompt,
            temperature=0.2,
            max_tokens=280,
            fallback_text=json.dumps(self._fallback_response()),
        )
        return self._run_async(coroutine)

    def _run_async(self, coroutine: Any) -> str:
        """Execute an async coroutine from a synchronous API safely."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coroutine)
            return future.result(timeout=12)

    @staticmethod
    def _resolve_model() -> str:
        preferred = (
            os.getenv("ANSWER_EVALUATOR_MODEL")
            or os.getenv("GROQ_MODEL")
            or DEFAULT_EVALUATOR_MODEL
            or DEFAULT_MODEL
        )
        return preferred.strip()

    @staticmethod
    def _fallback_response() -> dict[str, Any]:
        return {
            "relevance_score": 12,
            "depth_score": 10,
            "technical_score": 10,
            "communication_score": 8,
            "red_flags": [],
            "brief_feedback": FALLBACK_FEEDBACK,
            "needs_followup": True,
            "followup_reason": "The answer needs more detail and clearer technical evidence.",
        }

    @staticmethod
    def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
        try:
            integer = int(value)
        except (TypeError, ValueError):
            integer = minimum
        return max(minimum, min(maximum, integer))

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return " ".join(str(value or "").strip().split())

    @staticmethod
    def _sanitize_red_flags(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        for item in value:
            normalized = "_".join(str(item or "").strip().lower().split())
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned

    @staticmethod
    def _add_red_flag(red_flags: list[str], flag: str) -> list[str]:
        if flag in red_flags:
            return red_flags
        return [*red_flags, flag]

    @staticmethod
    def _keyword_overlap(answer: str, keywords: list[str]) -> set[str]:
        answer_tokens = set(TOKEN_PATTERN.findall(answer.lower()))
        overlap: set[str] = set()
        for keyword in keywords:
            keyword_tokens = set(TOKEN_PATTERN.findall(keyword.lower()))
            if keyword_tokens and keyword_tokens.issubset(answer_tokens):
                overlap.add(keyword)
        return overlap

    @staticmethod
    def _derive_followup_reason(red_flags: list[str], keyword_overlap: set[str], overall_score: int) -> str:
        if "too_short" in red_flags:
            return "The answer was too short to assess depth confidently."
        if "no_keyword_match" in red_flags or not keyword_overlap:
            return "The answer did not clearly address the expected technical points."
        if "unprofessional_tone" in red_flags:
            return "The answer showed tone issues that should be clarified."
        if overall_score < 55:
            return "The answer needs deeper examples and clearer reasoning."
        return "A follow-up can validate depth and technical accuracy."

    @staticmethod
    def _role_level_adjustment(role_level: str) -> dict[str, int]:
        normalized = role_level.lower()
        if normalized == "senior":
            return {"depth": 0, "technical": 0}
        if normalized == "mid":
            return {"depth": 1, "technical": 1}
        return {"depth": 2, "technical": 2}
