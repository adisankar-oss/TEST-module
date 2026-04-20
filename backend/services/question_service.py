from __future__ import annotations

import asyncio
import json
from collections import Counter
from typing import Any

from ai.duplicate_checker import is_duplicate_question
from ai.llm_client import ask_llm
from ai.topic_selector import choose_topic
from services.ai_client import AIClient
from utils.logger import get_logger


FALLBACK_QUESTION = "Tell me about a challenging system you worked on and the trade-offs you had to make?"
FOLLOWUP_BANNED_PHRASES = (
    "explain deeper",
    "improve your answer",
    "follow-up question",
    "follow up question",
    "previous question",
    "as I asked before",
)
TRACKED_CONCEPTS = ("async", "api", "database", "cache", "auth", "scaling")
ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "backend": ("backend", "api", "python", "java", "node", "service"),
    "frontend": ("frontend", "ui", "web", "react", "angular", "vue"),
    "data": ("data", "ml", "analytics", "etl", "pipeline", "ai"),
}
CONCEPT_TOPIC_MAP = {
    "async": "technical_skills",
    "api": "technical_skills",
    "database": "technical_skills",
    "cache": "problem_solving",
    "auth": "technical_skills",
    "scaling": "problem_solving",
}


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
            f"Hi {candidate_id}, it is good to meet you. "
            f"I'm Alex, and I'll be your interviewer today for the {role} role. "
            "We'll cover a mix of technical and behavioural topics, and I want this to feel like a real discussion."
        )

        try:
            greeting = await self._ai_client.generate_text(
                system_prompt=(
                    "You are a professional and friendly interviewer representing a top tech company. "
                    "Your job is to start the interview in a natural, human-like way. "
                    "Be warm, confident, and professional. Keep it under 120 words, do not output JSON, and end with a question."
                ),
                user_prompt=(
                    f"CANDIDATE NAME: {candidate_id}\n"
                    f"JOB ROLE: {job_id}\n"
                    "Greet the candidate by name, introduce yourself as interviewer, "
                    "mention the role, explain that the interview will mix technical and behavioural discussion, "
                    "and ask a natural opening question."
                ),
                temperature=0.4,
                max_tokens=120,
                fallback_text=fallback,
            )
            cleaned = self._clean_question(greeting)
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

    async def generate_question(self, session: Any) -> str:
        session_id = str(getattr(session, "id", getattr(session, "session_id", "unknown")))
        job_id = getattr(session, "job_id", "") or ""
        question_number = int(getattr(session, "current_question_number", 1) or 1)
        max_questions = int(getattr(session, "max_questions", 1) or 1)

        memory = self._ensure_memory(session)
        questions = memory["questions"]
        previous_questions = [entry.get("question", "") for entry in questions if entry.get("question")]
        covered_topics = [topic for topic in memory["topics"] if topic]
        concept_counts = self._concept_counts(questions, memory["concepts"])
        last_turn = questions[-1] if questions else {}
        last_answer = self._normalize_text(last_turn.get("answer", ""))
        last_score = self._coerce_int(last_turn.get("score"))
        last_answer_concepts = self._extract_concepts(last_answer)
        followup_anchor = self._choose_followup_anchor(last_answer_concepts, concept_counts)

        selected_topic = choose_topic(question_number, max_questions, covered_topics)
        if followup_anchor is not None and concept_counts.get(followup_anchor, 0) >= 2:
            followup_anchor = None
        if followup_anchor is None:
            selected_topic = self._force_new_topic(selected_topic, covered_topics)

        self._logger.info(
            json.dumps(
                {
                    "event": "topic_selected",
                    "session_id": session_id,
                    "question_number": question_number,
                    "topic": selected_topic,
                    "followup_anchor": followup_anchor,
                    "used_concepts": dict(concept_counts),
                }
            )
        )

        prompt = self._build_dynamic_prompt(
            job_id=job_id,
            question_number=question_number,
            max_questions=max_questions,
            selected_topic=selected_topic,
            previous_questions=previous_questions,
            last_turns=questions[-3:],
            last_answer=last_answer,
            last_score=last_score,
            covered_topics=covered_topics,
            used_concepts=memory["concepts"],
            followup_anchor=followup_anchor,
        )

        try:
            raw_response = await asyncio.wait_for(asyncio.to_thread(ask_llm, prompt), timeout=2.0)
            parsed = self._parse_llm_json(raw_response)
            candidate_question = self._clean_question(parsed.get("question", ""))
            question_type = str(parsed.get("type", "new")).strip().lower()
            question_topic = str(parsed.get("topic", selected_topic)).strip() or selected_topic

            if len(candidate_question) <= 10:
                raise ValueError("Generated question was too short")
            if is_duplicate_question(candidate_question, previous_questions):
                self._logger.warning(
                    json.dumps(
                        {
                            "event": "duplicate_detection",
                            "session_id": session_id,
                            "question": candidate_question,
                            "result": "duplicate",
                        }
                    )
                )
                raise ValueError("Generated question duplicated prior history")
            if self._contains_overused_concept(candidate_question, concept_counts):
                raise ValueError("Generated question reused an overused concept")
            if question_type == "followup":
                if followup_anchor is None:
                    raise ValueError("Model marked follow-up without a valid anchor")
                if followup_anchor not in candidate_question.lower():
                    raise ValueError("Follow-up question was not anchored to the last answer")
            if question_type != "followup" and followup_anchor is not None:
                question_topic = selected_topic

            concepts_in_question = self._extract_concepts(candidate_question)
            self._store_question_memory(
                session=session,
                memory=memory,
                question=candidate_question,
                topic=question_topic,
                concepts=concepts_in_question,
            )

            self._logger.info(
                json.dumps(
                    {
                        "event": "question_generated",
                        "session_id": session_id,
                        "job_id": job_id,
                        "question_number": question_number,
                        "type": question_type,
                        "topic": question_topic,
                        "question": candidate_question,
                    }
                )
            )
            return candidate_question
        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "question_generation_failure",
                        "session_id": session_id,
                        "job_id": job_id,
                        "question_number": question_number,
                        "error": str(exc),
                        "fallback_question": FALLBACK_QUESTION,
                    }
                )
            )
            self._store_question_memory(
                session=session,
                memory=memory,
                question=FALLBACK_QUESTION,
                topic=selected_topic,
                concepts=self._extract_concepts(FALLBACK_QUESTION),
            )
            return FALLBACK_QUESTION

    async def generate_followup(
        self,
        *,
        original_question: str,
        candidate_answer: str,
        evaluation_feedback: str,
        context: list[dict[str, Any]] | None = None,
    ) -> str:
        original_question = self._normalize_text(original_question)
        candidate_answer = self._normalize_text(candidate_answer)
        evaluation_feedback = self._normalize_text(evaluation_feedback)
        context = context or []
        anchor = self._choose_followup_anchor(self._extract_concepts(candidate_answer), Counter())

        if not all((original_question, candidate_answer, evaluation_feedback)) or anchor is None:
            return FALLBACK_QUESTION

        fallback = self._anchored_followup_fallback(anchor)

        try:
            followup = await self._ai_client.generate_text(
                system_prompt=(
                    "You are an expert interviewer. Generate one real follow-up question. "
                    "A true follow-up must explicitly reference a concept from the candidate's last answer, "
                    "go deeper into that exact concept, and avoid generic restatement. "
                    "Return only the question."
                ),
                user_prompt=(
                    f"Original question: {original_question}\n"
                    f"Candidate answer: {candidate_answer}\n"
                    f"Evaluation feedback: {evaluation_feedback}\n"
                    f"Anchor concept: {anchor}\n"
                    f"Recent context: {json.dumps(context, ensure_ascii=True)}\n"
                    "If you cannot ask a true anchored follow-up, return an empty string."
                ),
                temperature=0.4,
                max_tokens=120,
                fallback_text=fallback,
            )
            cleaned = self._clean_question(followup)
            if (
                cleaned
                and anchor in cleaned.lower()
                and cleaned.lower() != original_question.lower()
                and not any(phrase in cleaned.lower() for phrase in FOLLOWUP_BANNED_PHRASES)
            ):
                return cleaned
        except Exception:
            pass

        return fallback

    def resolve_role(self, job_id: str) -> str:
        normalized = (job_id or "").strip().lower()
        for role, keywords in ROLE_KEYWORDS.items():
            if any(keyword in normalized for keyword in keywords):
                return role
        return "general"

    def _ensure_memory(self, session: Any) -> dict[str, Any]:
        config = dict(getattr(session, "config", {}) or {})
        memory = getattr(session, "memory", None)
        if not isinstance(memory, dict):
            memory = dict(config.get("memory", {}) or {})

        questions = memory.get("questions")
        if not isinstance(questions, list):
            questions = []

        # Bootstrap from legacy question history if needed.
        legacy_history = config.get("question_history", [])
        if isinstance(legacy_history, list):
            normalized_legacy = [
                {
                    "question": entry.get("question", ""),
                    "answer": entry.get("answer", ""),
                    "score": entry.get("score"),
                    "topic": entry.get("topic", ""),
                }
                for entry in legacy_history
                if isinstance(entry, dict) and entry.get("question")
            ]
            if len(normalized_legacy) >= len(questions):
                questions = normalized_legacy

        topics = memory.get("topics")
        if not isinstance(topics, list):
            topics = [
                entry.get("topic", "")
                for entry in questions
                if isinstance(entry, dict) and entry.get("topic")
            ]

        concepts = memory.get("concepts")
        if not isinstance(concepts, list):
            concepts = []
            for entry in questions:
                if not isinstance(entry, dict):
                    continue
                concepts.extend(self._extract_concepts(entry.get("question", "")))
                concepts.extend(self._extract_concepts(entry.get("answer", "")))

        memory = {
            "questions": questions,
            "topics": topics,
            "concepts": concepts,
        }
        try:
            setattr(session, "memory", memory)
        except Exception:
            pass
        config["memory"] = memory
        if hasattr(session, "config"):
            session.config = config
        return memory

    def _store_question_memory(
        self,
        *,
        session: Any,
        memory: dict[str, Any],
        question: str,
        topic: str,
        concepts: list[str],
    ) -> None:
        questions = list(memory.get("questions", []))
        questions.append({"question": question, "answer": "", "score": None, "topic": topic})
        memory["questions"] = questions[-10:]
        memory["topics"] = (list(memory.get("topics", [])) + [topic])[-10:]
        memory["concepts"] = (list(memory.get("concepts", [])) + concepts)[-30:]

        config = dict(getattr(session, "config", {}) or {})
        config["memory"] = memory
        config["question_history"] = [
            {
                "question": entry.get("question", ""),
                "answer": entry.get("answer", ""),
                "score": entry.get("score"),
                "topic": entry.get("topic", ""),
            }
            for entry in memory["questions"]
        ]
        if hasattr(session, "config"):
            session.config = config
        try:
            setattr(session, "memory", memory)
        except Exception:
            pass

    def _build_dynamic_prompt(
        self,
        *,
        job_id: str,
        question_number: int,
        max_questions: int,
        selected_topic: str,
        previous_questions: list[str],
        last_turns: list[dict[str, Any]],
        last_answer: str,
        last_score: int | None,
        covered_topics: list[str],
        used_concepts: list[str],
        followup_anchor: str | None,
    ) -> str:
        return (
            "You are a professional technical interviewer conducting a real interview.\n"
            "Your task is to decide the best next question, not just generate a random one.\n"
            "Before generating a question, decide:\n"
            "* Should I follow up on a specific concept from the last answer?\n"
            "* OR move to a new concept to avoid repetition?\n\n"
            "Rules:\n"
            "- A true follow-up must explicitly reference a concept from the last answer.\n"
            "- If no meaningful anchored follow-up is possible, do not mark the question as follow-up.\n"
            "- Avoid repeating any previous question or concept.\n"
            "- If a concept has already been used twice, avoid it.\n"
            "- Return strict JSON only.\n\n"
            f"Job ID: {job_id}\n"
            f"Question Number: {question_number} / {max_questions}\n"
            f"Selected Topic: {selected_topic}\n"
            f"Previous Questions: {json.dumps(previous_questions[-10:], ensure_ascii=True)}\n"
            f"Last Answer Full Text: {last_answer}\n"
            f"Last 3 Q/A: {json.dumps(last_turns[-3:], ensure_ascii=True)}\n"
            f"Used Concepts: {json.dumps(used_concepts[-20:], ensure_ascii=True)}\n"
            f"Covered Topics: {json.dumps(covered_topics[-10:], ensure_ascii=True)}\n"
            f"Last Score: {json.dumps(last_score)}\n"
            f"Follow-up Anchor: {json.dumps(followup_anchor)}\n\n"
            "Return JSON with exactly these fields:\n"
            "{\n"
            '  "question": "string",\n'
            '  "type": "followup | new | easier | harder",\n'
            '  "topic": "technical_skills | problem_solving | behavioural | culture_fit | background",\n'
            '  "reasoning": "short internal reasoning"\n'
            "}"
        )

    def _parse_llm_json(self, raw_text: str) -> dict[str, Any]:
        trimmed = (raw_text or "").strip()
        start = trimmed.find("{")
        end = trimmed.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM response did not contain JSON")
        parsed = json.loads(trimmed[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("LLM response JSON was not an object")
        if not parsed.get("question"):
            raise ValueError("LLM response did not include a question")
        return parsed

    def _choose_followup_anchor(
        self,
        last_answer_concepts: list[str],
        concept_counts: Counter[str],
    ) -> str | None:
        for concept in last_answer_concepts:
            if concept_counts.get(concept, 0) < 2:
                return concept
        return None

    @staticmethod
    def _force_new_topic(selected_topic: str, covered_topics: list[str]) -> str:
        ordered_topics = [
            "background",
            "behavioural",
            "technical_skills",
            "problem_solving",
            "culture_fit",
        ]
        for topic in ordered_topics:
            if topic != selected_topic and topic not in covered_topics:
                return topic
        return selected_topic

    def _contains_overused_concept(
        self,
        question: str,
        concept_counts: Counter[str],
    ) -> bool:
        question_concepts = self._extract_concepts(question)
        return any(concept_counts.get(concept, 0) >= 2 for concept in question_concepts)

    def _concept_counts(
        self,
        questions: list[dict[str, Any]],
        stored_concepts: list[str],
    ) -> Counter[str]:
        counts: Counter[str] = Counter()
        for concept in stored_concepts:
            if concept in TRACKED_CONCEPTS:
                counts[concept] += 1
        for entry in questions:
            if not isinstance(entry, dict):
                continue
            for concept in self._extract_concepts(entry.get("answer", "")):
                counts[concept] += 1
            for concept in self._extract_concepts(entry.get("question", "")):
                counts[concept] += 1
        return counts

    @staticmethod
    def _extract_concepts(text: str) -> list[str]:
        lowered = f" {text.lower()} "
        return [concept for concept in TRACKED_CONCEPTS if f" {concept} " in lowered]

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        return value if isinstance(value, int) else None

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return " ".join(str(value or "").strip().split())

    def _clean_question(self, raw_text: str) -> str:
        cleaned = self._normalize_text(raw_text)
        cleaned = cleaned.replace(".?", "?")
        if cleaned and not cleaned.endswith("?"):
            cleaned = f"{cleaned.rstrip('.')}?"
        return cleaned.replace(" ?", "?")

    def _anchored_followup_fallback(self, concept: str) -> str:
        if concept == "async":
            return "You mentioned async work in your answer. How did you decide the right concurrency limits in that system?"
        if concept == "api":
            return "You mentioned APIs in your answer. How did you decide the contract boundaries and error handling strategy?"
        if concept == "database":
            return "You mentioned the database in your answer. How did you protect correctness while still meeting performance needs?"
        if concept == "cache":
            return "You mentioned caching in your answer. How did you decide what to cache and how to handle stale data risk?"
        if concept == "auth":
            return "You mentioned auth in your answer. How did you balance security requirements with developer and user experience?"
        if concept == "scaling":
            return "You mentioned scaling in your answer. What bottleneck became the hardest to manage as load increased?"
        return FALLBACK_QUESTION
