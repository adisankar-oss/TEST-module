from __future__ import annotations

import json
from statistics import mean
from typing import Any

from services.ai_client import AIClient
from utils.logger import get_logger


SOFT_SKILL_OPENERS = [
    "Tell me about yourself and the kind of problems you have been solving recently.",
    "Describe a challenging situation at work and how you handled it.",
    "Tell me about a time you had to make a difficult decision with incomplete information.",
]

ROLE_FALLBACK_QUESTIONS: dict[str, dict[str, list[str]]] = {
    "backend": {
        "behavioral": [
            "Tell me about a time you had to coordinate a backend change across multiple teams.",
            "Describe a situation where a production issue forced you to balance speed and correctness.",
        ],
        "technical": [
            "How would you design an API that remains reliable under heavy concurrent traffic?",
            "How do you investigate and fix a production latency issue in a backend service?",
            "What tradeoffs would you consider when choosing between strong consistency and availability?",
        ],
        "problem_solving": [
            "You see rising database latency after a feature launch. How would you isolate the root cause?",
            "How would you redesign a failing queue-based workflow to improve reliability and observability?",
        ],
    },
    "frontend": {
        "behavioral": [
            "Tell me about a time you had to defend a UX decision with limited data.",
            "Describe a situation where you had to balance product pressure against frontend quality.",
        ],
        "technical": [
            "How would you structure state management for a complex, data-heavy frontend application?",
            "How do you diagnose a rendering performance regression that only appears in production?",
        ],
        "problem_solving": [
            "A page becomes slow after adding several widgets. How would you narrow down the cause?",
            "How would you handle partial API failures in a user-facing workflow without confusing users?",
        ],
    },
    "data": {
        "behavioral": [
            "Describe a time you had to challenge an assumption in a data-driven decision.",
            "Tell me about a situation where bad data changed the direction of a project.",
        ],
        "technical": [
            "How would you design a pipeline that handles delayed and malformed events reliably?",
            "How do you validate data quality before downstream models or reports consume the data?",
        ],
        "problem_solving": [
            "A key metric suddenly drops. How would you determine whether the issue is product-related or data-related?",
            "How would you debug a recurring pipeline failure that happens only under peak load?",
        ],
    },
    "general": {
        "behavioral": [
            "Tell me about a time you had to earn trust quickly on a new project.",
            "Describe a situation where your first approach failed and what you changed next.",
        ],
        "technical": [
            "Walk me through how you break down a complex problem when the path forward is not obvious.",
            "How do you decide when a quick fix is acceptable versus when a deeper redesign is necessary?",
        ],
        "problem_solving": [
            "You inherit a process that is failing unpredictably. How would you stabilize it?",
            "How would you investigate a problem when the available signals are incomplete or conflicting?",
        ],
    },
}

ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "backend": ("backend", "api", "python", "java", "node", "service"),
    "frontend": ("frontend", "ui", "web", "react", "angular", "vue"),
    "data": ("data", "ml", "analytics", "etl", "pipeline", "ai"),
}

FOLLOWUP_BANNED_PHRASES = (
    "explain deeper",
    "improve your answer",
    "follow-up question",
    "follow up question",
    "previous question",
    "as I asked before",
)


class QuestionService:
    def __init__(self, ai_client: AIClient | None = None) -> None:
        self._ai_client = ai_client or AIClient()
        self._logger = get_logger("services.question_service")

    async def generate_greeting(
        self,
        *,
        candidate_id: str,
        job_id: str,
        context: list[dict[str, Any]] | None = None,
    ) -> str:
        role = self.resolve_role(job_id)
        fallback = (
            "Hi, I'm Alex, and I'll be your interviewer today. "
            "We'll start with a few questions about your experience and then move into role-specific topics. "
            "Take your time, and let's begin."
        )

        try:
            greeting = await self._ai_client.generate_text(
                system_prompt=(
                    "You are a professional interviewer. "
                    "Greet the candidate warmly. Introduce yourself. "
                    "Explain the interview structure briefly. Keep it natural and professional in 2-3 sentences."
                ),
                user_prompt=(
                    f"Candidate identifier: {candidate_id}\n"
                    f"Role context: {role}\n"
                    f"Recent context: {json.dumps(context or [], ensure_ascii=True)}\n"
                    "Return only the greeting."
                ),
                temperature=0.4,
                max_tokens=120,
                fallback_text=fallback,
            )
            cleaned = self._clean_text(greeting)
            if cleaned:
                self._logger.info(
                    json.dumps(
                        {
                            "event": "greeting_generated",
                            "candidate_id": candidate_id,
                            "job_id": job_id,
                            "source": "ai",
                            "greeting": cleaned,
                        }
                    )
                )
                return cleaned
        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "ai_error",
                        "component": "question_service.greeting",
                        "candidate_id": candidate_id,
                        "job_id": job_id,
                        "error": str(exc),
                    }
                )
            )

        self._logger.info(
            json.dumps(
                {
                    "event": "greeting_generated",
                    "candidate_id": candidate_id,
                    "job_id": job_id,
                    "source": "fallback",
                    "greeting": fallback,
                }
            )
        )
        return fallback

    async def generate_question(
        self,
        job_id: str,
        question_number: int,
        context: list[dict[str, Any]] | None = None,
        difficulty_hint: str = "normal",
    ) -> str:
        role = self.resolve_role(job_id)
        context = context or []
        track = self._question_track(question_number, context)
        fallback = self._fallback_question(
            role=role,
            question_number=question_number,
            track=track,
            context=context,
        )

        try:
            question = await self._ai_client.generate_text(
                system_prompt=self._question_system_prompt(),
                user_prompt=self._question_user_prompt(
                    job_id=job_id,
                    role=role,
                    question_number=question_number,
                    context=context,
                    difficulty_hint=difficulty_hint,
                    track=track,
                ),
                temperature=0.5,
                max_tokens=180,
                fallback_text=fallback,
            )
            cleaned = self._clean_question(question)
            if self._is_valid_question(cleaned, context=context):
                self._logger.info(
                    json.dumps(
                        {
                            "event": "question_generated",
                            "job_id": job_id,
                            "role": role,
                            "question_number": question_number,
                            "track": track,
                            "difficulty": difficulty_hint,
                            "source": "ai",
                            "question": cleaned,
                        }
                    )
                )
                return cleaned
        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "ai_error",
                        "component": "question_service",
                        "job_id": job_id,
                        "question_number": question_number,
                        "error": str(exc),
                    }
                )
            )

        self._logger.info(
            json.dumps(
                {
                    "event": "question_generated",
                    "job_id": job_id,
                    "role": role,
                    "question_number": question_number,
                    "track": track,
                    "difficulty": difficulty_hint,
                    "source": "fallback",
                    "question": fallback,
                }
            )
        )
        return fallback

    async def generate_followup(
        self,
        *,
        original_question: str,
        candidate_answer: str,
        evaluation_feedback: str,
        context: list[dict[str, Any]] | None = None,
    ) -> str:
        original_question = self._normalize_prompt_value(original_question)
        candidate_answer = self._normalize_prompt_value(candidate_answer)
        evaluation_feedback = self._normalize_prompt_value(evaluation_feedback)
        context = context or []

        fallback = self._fallback_followup(
            original_question=original_question,
            candidate_answer=candidate_answer,
            evaluation_feedback=evaluation_feedback,
        )

        if not all((original_question, candidate_answer, evaluation_feedback)):
            self._logger.info(
                json.dumps(
                    {
                        "event": "followup_triggered",
                        "source": "fallback",
                        "reason": "missing_followup_inputs",
                        "original_question_present": bool(original_question),
                        "candidate_answer_present": bool(candidate_answer),
                        "evaluation_feedback_present": bool(evaluation_feedback),
                        "followup_question": fallback,
                    }
                )
            )
            return fallback

        try:
            followup = await self._ai_client.generate_text(
                system_prompt=self._followup_system_prompt(),
                user_prompt=self._followup_user_prompt(
                    original_question=original_question,
                    candidate_answer=candidate_answer,
                    evaluation_feedback=evaluation_feedback,
                    context=context,
                ),
                temperature=0.4,
                max_tokens=120,
                fallback_text=fallback,
            )
            cleaned = self._clean_question(followup)
            if self._is_valid_followup(
                cleaned,
                original_question=original_question,
                context=context,
            ):
                self._logger.info(
                    json.dumps(
                        {
                            "event": "followup_triggered",
                            "source": "ai",
                            "original_question": original_question,
                            "followup_question": cleaned,
                        }
                    )
                )
                return cleaned
        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "ai_error",
                        "component": "question_service.followup",
                        "error": str(exc),
                    }
                )
            )

        self._logger.info(
            json.dumps(
                {
                    "event": "followup_triggered",
                    "source": "fallback",
                    "reason": "invalid_or_failed_ai_followup",
                    "original_question": original_question,
                    "followup_question": fallback,
                }
            )
        )
        return fallback

    def resolve_role(self, job_id: str) -> str:
        normalized = (job_id or "").strip().lower()
        for role, keywords in ROLE_KEYWORDS.items():
            if any(keyword in normalized for keyword in keywords):
                return role
        return "general"

    def _question_track(self, question_number: int, context: list[dict[str, Any]]) -> str:
        if question_number <= 1:
            return "soft_skill"

        if not context:
            return "behavioral"

        average_score = mean(item.get("score", 5) for item in context)
        if question_number % 3 == 0:
            return "problem_solving"
        if average_score >= 8:
            return "technical"
        if average_score <= 5:
            return "behavioral"
        return "technical"

    def _fallback_question(
        self,
        *,
        role: str,
        question_number: int,
        track: str,
        context: list[dict[str, Any]],
    ) -> str:
        previous_questions = {
            self._normalize_prompt_value(item.get("question", "")).lower()
            for item in context
            if item.get("question")
        }

        if track == "soft_skill":
            for question in SOFT_SKILL_OPENERS:
                if question.lower() not in previous_questions:
                    return question
            return SOFT_SKILL_OPENERS[(question_number - 1) % len(SOFT_SKILL_OPENERS)]

        bank = ROLE_FALLBACK_QUESTIONS.get(role, ROLE_FALLBACK_QUESTIONS["general"])
        ordered_tracks = [track, "behavioral", "technical", "problem_solving"]
        for candidate_track in ordered_tracks:
            for question in bank.get(candidate_track, []):
                if question.lower() not in previous_questions:
                    return question

        merged_bank = []
        for candidate_track in ("behavioral", "technical", "problem_solving"):
            merged_bank.extend(bank.get(candidate_track, []))
        return merged_bank[(max(question_number, 1) - 1) % len(merged_bank)]

    @staticmethod
    def _clean_text(raw_text: str) -> str:
        return " ".join((raw_text or "").strip().split())

    def _clean_question(self, raw_text: str) -> str:
        cleaned = self._clean_text(raw_text)
        if not cleaned:
            return ""
        if not cleaned.endswith("?"):
            cleaned = f"{cleaned.rstrip('.')}?"
        return cleaned.replace(" ?", "?")

    def _is_valid_question(self, candidate: str, *, context: list[dict[str, Any]]) -> bool:
        if not candidate:
            return False
        previous_questions = {
            self._normalize_prompt_value(item.get("question", "")).lower()
            for item in context
            if item.get("question")
        }
        return candidate.lower() not in previous_questions

    @staticmethod
    def _question_system_prompt() -> str:
        return (
            "You are a professional AI interviewer conducting a live interview. "
            "Generate exactly one interview question. "
            "The first question must be a soft-skill or experience-opening question. "
            "Later questions should mix behavioral, technical, and problem-solving topics. "
            "Use the candidate's recent answers and scores to adapt depth and difficulty. "
            "Avoid repetition, avoid meta commentary, and output only the question."
        )

    def _question_user_prompt(
        self,
        *,
        job_id: str,
        role: str,
        question_number: int,
        context: list[dict[str, Any]],
        difficulty_hint: str,
        track: str,
    ) -> str:
        return (
            f"Job identifier: {job_id}\n"
            f"Inferred role: {role}\n"
            f"Question number: {question_number}\n"
            f"Target track: {track}\n"
            f"Difficulty target: {difficulty_hint}\n"
            f"Recent context (last up to 3 interactions):\n{json.dumps(context, ensure_ascii=True)}\n"
            "Generate one fresh question with natural interviewer tone."
        )

    @staticmethod
    def _followup_system_prompt() -> str:
        return (
            "You are an expert technical interviewer.\n"
            "Generate ONE high-quality follow-up question based on a candidate's previous answer.\n"
            "STRICT RULES:\n"
            "- Do NOT repeat or rephrase the previous question.\n"
            "- Do NOT include phrases like 'explain more', 'explain deeper', or similar meta-instructions.\n"
            "- Do NOT recursively reference earlier follow-ups.\n"
            "- Identify the missing detail and ask a specific, concrete question.\n"
            "- Output only the follow-up question."
        )

    def _followup_user_prompt(
        self,
        *,
        original_question: str,
        candidate_answer: str,
        evaluation_feedback: str,
        context: list[dict[str, Any]],
    ) -> str:
        return (
            "Original Question:\n"
            f"{original_question}\n\n"
            "Candidate Answer:\n"
            f"{candidate_answer}\n\n"
            "Evaluation Feedback:\n"
            f"{evaluation_feedback}\n\n"
            "Recent Context:\n"
            f"{json.dumps(context, ensure_ascii=True)}\n\n"
            "Return ONLY a follow-up question."
        )

    @staticmethod
    def _normalize_prompt_value(value: str) -> str:
        return " ".join((value or "").strip().split())

    def _is_valid_followup(
        self,
        candidate: str,
        *,
        original_question: str,
        context: list[dict[str, Any]],
    ) -> bool:
        if not candidate:
            return False

        normalized_candidate = candidate.strip().lower()
        normalized_original = self._normalize_prompt_value(original_question).lower()

        if normalized_candidate == normalized_original:
            return False
        if normalized_original and normalized_candidate in normalized_original:
            return False
        if any(phrase in normalized_candidate for phrase in FOLLOWUP_BANNED_PHRASES):
            return False

        previous_questions = {
            self._normalize_prompt_value(item.get("question", "")).lower()
            for item in context
            if item.get("question")
        }
        return normalized_candidate not in previous_questions

    def _fallback_followup(
        self,
        *,
        original_question: str,
        candidate_answer: str,
        evaluation_feedback: str,
    ) -> str:
        feedback = evaluation_feedback.lower()
        question = original_question.lower()
        answer = candidate_answer.lower()

        if "example" in feedback or "specific" in feedback:
            return "Can you give a specific example from your own work that demonstrates that approach and its outcome?"
        if "tradeoff" in feedback or "trade-off" in feedback:
            return "What tradeoff did you make in that approach, and what made that tradeoff acceptable?"
        if "performance" in feedback or "latency" in feedback or "scal" in feedback:
            return "Which metrics would you check first, and how would those metrics shape your next step?"
        if "database" in feedback or "consistency" in feedback or "reliability" in feedback:
            return "How would you handle failure scenarios in that design while keeping the data correct?"
        if "behavior" in question or "conflict" in question or "team" in question:
            return "What was the hardest part of aligning other people in that situation, and how did you handle it?"
        if "architecture" in question or "design" in question or "system" in question or "api" in question:
            return "What was the most important design decision in that system, and what alternative did you reject?"
        if "clarity" in feedback or "depth" in feedback or "detail" in feedback:
            return "What was the first concrete step you took, and what evidence told you it was the right one?"
        if answer:
            return "What constraint or failure case most influenced your approach, and how did you account for it?"
        return "What constraint most influenced your decision, and how did it change your approach?"
