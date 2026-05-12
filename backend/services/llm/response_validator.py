from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from backend.services.llm.model_configs import ModelRuntimeConfig, load_model_config
from backend.services.llm.semantic_duplicate_detector import SemanticDuplicateDetector


CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
HALLUCINATED_REFERENCE_MARKERS = (
    "as you mentioned earlier",
    "you said earlier",
    "as we discussed earlier",
    "from your resume",
    "from your background",
)
ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "backend": ("backend", "api", "microservice", "python", "java", "node", "database", "schema", "migration", "queue", "cache"),
    "frontend": ("frontend", "ui", "ux", "browser", "react", "javascript", "css"),
    "data": ("data", "pipeline", "ml", "analytics", "warehouse", "etl"),
    "devops": ("infra", "deployment", "kubernetes", "ci", "cd", "observability"),
}


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    content: str
    parsed_payload: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


class ResponseValidator:
    def __init__(
        self,
        config: ModelRuntimeConfig | None = None,
        duplicate_detector: SemanticDuplicateDetector | None = None,
    ) -> None:
        self._config = config or load_model_config()
        self._duplicate_detector = duplicate_detector or SemanticDuplicateDetector()

    def validate(
        self,
        *,
        task_type: str,
        content: str,
        response_format: str = "text",
        metadata: dict[str, Any] | None = None,
    ) -> ValidationResult:
        metadata = metadata or {}
        normalized = self._normalize_content(content)
        if not normalized:
            return ValidationResult(ok=False, content="", errors=["empty_response"])

        parsed_payload: dict[str, Any] | None = None
        if response_format == "json":
            parsed_payload = self._extract_json_object(normalized)
            if parsed_payload is None:
                return ValidationResult(ok=False, content=normalized, errors=["malformed_json"])

        errors: list[str] = []
        if self._contains_hallucinated_reference(normalized, metadata):
            errors.append("hallucinated_interviewer_claim")

        task_name = (task_type or "").strip().lower()
        if task_name == "question_generation":
            errors.extend(self._validate_question_generation(parsed_payload, metadata))
        elif task_name == "followup_generation":
            errors.extend(self._validate_followup_generation(normalized, metadata))
        elif task_name == "realtime_evaluation":
            errors.extend(self._validate_realtime_evaluation(parsed_payload))
        elif task_name == "final_summary":
            errors.extend(self._validate_final_summary(normalized, metadata))

        return ValidationResult(
            ok=not errors,
            content=normalized,
            parsed_payload=parsed_payload,
            errors=errors,
        )

    @staticmethod
    def _normalize_content(content: str) -> str:
        raw = " ".join(str(content or "").strip().split())
        if not raw:
            return ""
        match = CODE_FENCE.match(raw)
        if match:
            return " ".join(match.group(1).strip().split())
        return raw

    def _validate_question_generation(
        self,
        parsed_payload: dict[str, Any] | None,
        metadata: dict[str, Any],
    ) -> list[str]:
        if not parsed_payload:
            return ["malformed_json"]

        question = self._normalize_content(str(parsed_payload.get("question") or ""))
        if len(question) < 10:
            return ["empty_question"]

        errors: list[str] = []
        if not question.endswith("?"):
            errors.append("invalid_question_format")

        history = [str(item) for item in metadata.get("previous_questions", []) if str(item).strip()]
        is_duplicate, _ = self._duplicate_detector.is_duplicate(
            question,
            history,
            threshold=self._config.semantic_similarity_threshold,
        )
        if is_duplicate:
            errors.append("semantic_duplicate_question")

        role_label = str(metadata.get("role") or metadata.get("job_id") or "").lower()
        topic = str(metadata.get("topic") or "").lower()
        if role_label and not self._role_relevant(question, role_label, topic):
            errors.append("role_irrelevant_question")

        return errors

    def _validate_followup_generation(self, content: str, metadata: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if len(content) < 10:
            errors.append("empty_followup")
        if content and not content.endswith("?"):
            errors.append("invalid_followup_format")

        original_question = str(metadata.get("original_question") or "")
        if original_question:
            similarity = self._duplicate_detector.similarity(content, original_question)
            if similarity >= self._config.followup_similarity_threshold:
                errors.append("followup_repeats_original")

        recent_followups = [str(item) for item in metadata.get("recent_followups", []) if str(item).strip()]
        is_duplicate, _ = self._duplicate_detector.is_duplicate(
            content,
            recent_followups,
            threshold=self._config.followup_similarity_threshold,
        )
        if is_duplicate:
            errors.append("semantic_duplicate_followup")

        candidate_answer = self._normalize_content(str(metadata.get("candidate_answer") or ""))
        if candidate_answer and not self._shares_context(candidate_answer, content):
            errors.append("followup_not_grounded_in_candidate_answer")

        return errors

    @staticmethod
    def _validate_realtime_evaluation(parsed_payload: dict[str, Any] | None) -> list[str]:
        required_fields = {
            "score",
            "strengths",
            "weaknesses",
            "followup_required",
            "followup_reason",
            "confidence_score",
            "decision",
        }
        if not parsed_payload:
            return ["malformed_json"]

        missing = sorted(required_fields.difference(parsed_payload))
        if missing:
            return [f"missing_fields:{','.join(missing)}"]

        errors: list[str] = []
        score = parsed_payload.get("score")
        confidence = parsed_payload.get("confidence_score")
        if not isinstance(score, int) or score < 0 or score > 10:
            errors.append("invalid_score_range")
        if not isinstance(confidence, (int, float)) or float(confidence) < 0 or float(confidence) > 1:
            errors.append("invalid_confidence_range")
        if not isinstance(parsed_payload.get("strengths"), list):
            errors.append("invalid_strengths_format")
        if not isinstance(parsed_payload.get("weaknesses"), list):
            errors.append("invalid_weaknesses_format")
        return errors

    @staticmethod
    def _validate_final_summary(content: str, metadata: dict[str, Any]) -> list[str]:
        if len(content) < 40:
            return ["summary_too_short"]
        if metadata.get("candidate_name"):
            lowered = content.lower()
            if "hallucinated" in lowered:
                return ["hallucinated_summary_claim"]
        return []

    @staticmethod
    def _contains_hallucinated_reference(content: str, metadata: dict[str, Any]) -> bool:
        if metadata.get("allow_contradiction_reference"):
            return False
        lowered = content.lower()
        return any(marker in lowered for marker in HALLUCINATED_REFERENCE_MARKERS)

    @staticmethod
    def _extract_json_object(content: str) -> dict[str, Any] | None:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            payload = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _shares_context(candidate_answer: str, followup: str) -> bool:
        answer_tokens = {token for token in candidate_answer.lower().split() if len(token) > 3}
        followup_tokens = {token for token in followup.lower().split() if len(token) > 3}
        if not answer_tokens or not followup_tokens:
            return False
        return bool(answer_tokens & followup_tokens)

    def _role_relevant(self, question: str, role_label: str, topic: str) -> bool:
        if topic in {"behavioral", "behavioural", "culture_fit", "background"}:
            return True
        normalized_role = role_label.lower()
        matched_keywords: tuple[str, ...] = ()
        for role_name, keywords in ROLE_KEYWORDS.items():
            if role_name in normalized_role or any(keyword in normalized_role for keyword in keywords):
                matched_keywords = keywords
                break
        if not matched_keywords:
            return True

        lowered = question.lower()
        keyword_hits = sum(1 for keyword in matched_keywords if keyword in lowered)
        if keyword_hits:
            return True
        question_tokens = {token for token in lowered.split() if len(token) > 3}
        return (len(question_tokens & set(matched_keywords)) / max(len(question_tokens), 1)) >= self._config.role_relevance_threshold
