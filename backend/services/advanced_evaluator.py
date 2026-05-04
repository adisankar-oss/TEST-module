"""
Advanced Answer Evaluator (M6) - Production-grade technical interview evaluation.

This module implements a reasoning-based evaluation system that behaves like a real
technical interviewer, not a keyword checker.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from services.ai_client import AIClient, AIClientConfig
from utils.logger import get_logger


# =============================================================================
# CONSTANTS & CONFIGURATION
# =============================================================================

MIN_ANSWER_WORDS = 20
SHORT_ANSWER_THRESHOLD = 30

# Score maximums
RELEVANCE_MAX = 25
DEPTH_MAX = 25
TECHNICAL_MAX = 25
COMMUNICATION_MAX = 15
RED_FLAG_PENALTY_MAX = 10

# Model configuration
DEFAULT_EVALUATOR_MODEL = "llama-3.1-8b-instant"
FALLBACK_MODEL = "llama-3.3-70b-versatile"

# Red flag definitions with penalties
RED_FLAG_PENALTIES = {
    "too_short": 8,
    "vague_answer": 5,
    "contradiction": 6,
    "generic_intro": 3,
    "no_real_example": 4,
    "unprofessional_tone": 5,
    "hand_wavy": 3,
    "circular_reasoning": 4,
}

UNPROFESSIONAL_TERMS = {"stupid", "idiot", "dumb", "hate", "useless", "whatever", "crap", "wtf"}

# Follow-up focus types
FOLLOWUP_FOCUS_TYPES = {
    "technical_depth": "Ask for deeper technical explanation",
    "clarity": "Ask candidate to clarify their answer",
    "missing_concepts": "Probe missing key concepts",
    "recovery": "Give candidate chance to recover",
}


@dataclass(slots=True)
class EvaluationResult:
    """Structured result from answer evaluation."""

    relevance_score: int = 0
    depth_score: int = 0
    technical_score: int = 0
    communication_score: int = 0
    reasoning: dict[str, str] = field(default_factory=dict)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    overall_score: int = 0
    confidence_score: float = 0.0
    brief_feedback: str = ""
    needs_followup: bool = False
    followup_focus: str = "technical_depth"
    followup_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "relevance_score": self.relevance_score,
            "depth_score": self.depth_score,
            "technical_score": self.technical_score,
            "communication_score": self.communication_score,
            "reasoning": self.reasoning,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "red_flags": self.red_flags,
            "overall_score": self.overall_score,
            "confidence_score": self.confidence_score,
            "brief_feedback": self.brief_feedback,
            "needs_followup": self.needs_followup,
            "followup_focus": self.followup_focus,
            "followup_reason": self.followup_reason,
        }


class AdvancedAnswerEvaluator:
    """
    Advanced LLM-backed answer evaluator with reasoning-based scoring.

    This evaluator:
    - Understands semantic meaning, not just keywords
    - Provides consistent scoring through normalization
    - Evaluates technical depth properly
    - Generates specific, actionable feedback
    - Explains reasoning behind each score
    - Makes intelligent follow-up decisions
    """

    def __init__(self, ai_client: AIClient | None = None) -> None:
        self._ai_client = ai_client or AIClient(
            AIClientConfig(
                api_key=self._get_api_key(),
                model=self._resolve_model(),
            )
        )
        self._logger = get_logger("services.advanced_evaluator")
        self._thread_pool = ThreadPoolExecutor(max_workers=2)

    def evaluate(
        self,
        question: str,
        answer: str,
        expected_keywords: list[str] | None = None,
        role_level: str = "fresher",
    ) -> dict[str, Any]:
        """
        Evaluate a candidate answer against a technical interview question.

        Args:
            question: The interview question asked
            answer: The candidate's answer
            expected_keywords: Optional list of expected technical terms
            role_level: Target role level (fresher/mid/senior)

        Returns:
            Structured evaluation result as dictionary
        """
        # Normalize inputs
        norm_question = self._normalize_text(question)
        norm_answer = self._normalize_text(answer)
        norm_keywords = [self._normalize_text(kw) for kw in (expected_keywords or []) if self._normalize_text(kw)]
        norm_role = self._normalize_text(role_level).lower() or "fresher"

        # Handle edge cases
        if not norm_question or not norm_answer:
            return self._fallback_response("Missing question or answer").to_dict()

        # Check for short answer
        word_count = len(norm_answer.split())
        is_short = word_count < MIN_ANSWER_WORDS

        # Build and send evaluation prompt
        prompt = self._build_prompt(
            question=norm_question,
            answer=norm_answer,
            keywords=norm_keywords,
            role_level=norm_role,
            is_short=is_short,
        )

        try:
            raw_response = self._generate(prompt)
            result = self._parse_response(raw_response, norm_answer, norm_keywords)
        except Exception as exc:
            self._logger.error(
                json.dumps({
                    "event": "advanced_evaluator_error",
                    "error": str(exc),
                    "answer_length": len(norm_answer),
                })
            )
            result = self._fallback_response(f"Parsing error: {exc}")

        # Apply post-processing and normalization
        result = self._post_process_scores(result, norm_answer, norm_keywords, norm_role, is_short)

        return result.to_dict()

    def _build_prompt(
        self,
        *,
        question: str,
        answer: str,
        keywords: list[str],
        role_level: str,
        is_short: bool,
    ) -> str:
        """Build the evaluation prompt for the LLM."""
        keywords_json = json.dumps(keywords, ensure_ascii=True) if keywords else "[]"

        short_answer_warning = ""
        if is_short:
            short_answer_warning = (
                "WARNING: This answer is very short (under 20 words). "
                "Heavily penalize depth and communication scores. "
                "Add red_flag 'too_short' if answer provides no meaningful content.\n\n"
            )

        return (
            "You are an experienced technical interviewer evaluating a candidate's answer. "
            "Evaluate based on SEMANTIC UNDERSTANDING, not keyword matching.\n\n"
            "---\n"
            f"ROLE LEVEL: {role_level}\n\n"
            f"QUESTION:\n{question}\n\n"
            f"CANDIDATE ANSWER:\n{answer}\n\n"
            f"EXPECTED KEYWORDS (reference only, do not score purely on these):\n{keywords_json}\n\n"
            f"{short_answer_warning}"
            "---\n"
            "EVALUATION CRITERIA:\n\n"
            "1. RELEVANCE (0-25): Does the answer directly address the question?\n"
            "   - 20-25: Directly answers with clear connection to question\n"
            "   - 15-19: Mostly relevant but some tangential content\n"
            "   - 10-14: Partially relevant, misses main point\n"
            "   - 0-9: Off-topic or avoids the question\n"
            "   - Penalize long introductions before actually answering\n\n"
            "2. DEPTH (0-25): Quality of explanation and reasoning\n"
            "   - 20-25: Multi-step reasoning, trade-offs, real-world thinking\n"
            "   - 15-19: Good explanation with some depth\n"
            "   - 10-14: Surface-level explanation\n"
            "   - 0-9: Shallow, no reasoning, just assertions\n"
            "   - A structured but shallow answer = low score\n"
            "   - A deep but imperfect answer = high score\n\n"
            "3. TECHNICAL ACCURACY (0-25): Correctness of concepts\n"
            "   - 20-25: Accurate, appropriate terminology, sound thinking\n"
            "   - 15-19: Mostly accurate with minor gaps\n"
            "   - 10-14: Some correct concepts but notable gaps\n"
            "   - 0-9: Fundamentally flawed or incorrect\n"
            "   - Check if concepts are EXPLAINED correctly, not just mentioned\n\n"
            "4. COMMUNICATION (0-15): Clarity and structure\n"
            "   - 12-15: Clear, well-structured, concise\n"
            "   - 8-11: Understandable but could be clearer\n"
            "   - 4-7: Disorganized or hard to follow\n"
            "   - 0-3: Incoherent or rambling\n\n"
            "5. RED FLAGS (deduct up to 10 points total):\n"
            "   - too_short: Under 20 words with no meaningful content\n"
            "   - vague_answer: No concrete details or examples\n"
            "   - contradiction: Self-contradictory statements\n"
            "   - generic_intro: Long generic intro before answering\n"
            "   - no_real_example: Claims experience but no concrete example\n"
            "   - unprofessional_tone: Inappropriate language\n"
            "   - hand_wavy: Vague hand-waving instead of real explanation\n"
            "   - circular_reasoning: Circular or tautological logic\n\n"
            "6. CONFIDENCE SCORE (0.0-1.0): How certain is this evaluation?\n"
            "   - Base on: answer clarity, completeness, evaluable content\n"
            "   - Short/vague answers = lower confidence\n\n"
            "---\n"
            "OUTPUT FORMAT (JSON ONLY, NO MARKDOWN, NO PROSE):\n"
            "{\n"
            '  "relevance_score": <int 0-25>,\n'
            '  "depth_score": <int 0-25>,\n'
            '  "technical_score": <int 0-25>,\n'
            '  "communication_score": <int 0-15>,\n'
            '  "reasoning": {\n'
            '    "relevance": "<1-2 sentence explanation for relevance score>",\n'
            '    "depth": "<1-2 sentence explanation for depth score>",\n'
            '    "technical": "<1-2 sentence explanation for technical score>",\n'
            '    "communication": "<1-2 sentence explanation for communication score>"\n'
            '  },\n'
            '  "strengths": ["<specific strength 1>", "<specific strength 2>"],\n'
            '  "weaknesses": ["<specific weakness 1>", "<specific weakness 2>"],\n'
            '  "red_flags": ["<flag1>", "<flag2>"],\n'
            '  "overall_score": <int 0-90>,\n'
            '  "confidence_score": <float 0.0-1.0>,\n'
            '  "brief_feedback": "<2-3 sentences of specific, actionable feedback>",\n'
            '  "needs_followup": <boolean>,\n'
            '  "followup_focus": "<technical_depth|clarity|missing_concepts|recovery>",\n'
            '  "followup_reason": "<1 sentence explaining why followup needed>"\n'
            "}\n\n"
            "---\n"
            "IMPORTANT RULES:\n"
            "- Return ONLY valid JSON. No markdown code blocks. No prose.\n"
            "- Explain WHY each score was given in the reasoning field.\n"
            "- Feedback must be SPECIFIC to this answer, not generic.\n"
            "- BAD feedback: 'Add more detail'\n"
            "- GOOD feedback: 'You mentioned X but didn't explain how to implement Y'\n"
            "- Keywords are a SIGNAL only. Missing keywords != wrong answer.\n"
            "- Evaluate semantic understanding, not keyword presence.\n"
        )

    def _parse_response(self, raw_response: str, answer: str, keywords: list[str]) -> EvaluationResult:
        """Parse and validate the LLM response into an EvaluationResult."""
        if not raw_response:
            raise ValueError("Empty evaluation response")

        # Extract JSON block
        json_match = re.search(r"\{.*\}", raw_response, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON object found in response")

        try:
            data = json.loads(json_match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in response: {exc}") from exc

        # Validate required fields
        required = {
            "relevance_score", "depth_score", "technical_score", "communication_score",
            "reasoning", "strengths", "weaknesses", "red_flags", "overall_score",
            "confidence_score", "brief_feedback", "needs_followup", "followup_focus",
            "followup_reason",
        }
        missing = required - set(data.keys())
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        # Build result object
        result = EvaluationResult(
            relevance_score=self._clamp_int(data.get("relevance_score"), 0, RELEVANCE_MAX),
            depth_score=self._clamp_int(data.get("depth_score"), 0, DEPTH_MAX),
            technical_score=self._clamp_int(data.get("technical_score"), 0, TECHNICAL_MAX),
            communication_score=self._clamp_int(data.get("communication_score"), 0, COMMUNICATION_MAX),
            reasoning=self._parse_reasoning(data.get("reasoning", {})),
            strengths=self._parse_string_list(data.get("strengths", [])),
            weaknesses=self._parse_string_list(data.get("weaknesses", [])),
            red_flags=self._sanitize_red_flags(data.get("red_flags", [])),
            overall_score=self._clamp_int(data.get("overall_score"), 0, RELEVANCE_MAX + DEPTH_MAX + TECHNICAL_MAX + COMMUNICATION_MAX),
            confidence_score=self._clamp_float(data.get("confidence_score"), 0.0, 1.0),
            brief_feedback=self._normalize_text(data.get("brief_feedback", "")),
            needs_followup=bool(data.get("needs_followup")),
            followup_focus=self._validate_followup_focus(data.get("followup_focus", "technical_depth")),
            followup_reason=self._normalize_text(data.get("followup_reason", "")),
        )

        return result

    def _post_process_scores(
        self,
        result: EvaluationResult,
        answer: str,
        keywords: list[str],
        role_level: str,
        is_short: bool,
    ) -> EvaluationResult:
        """
        Apply normalization, consistency checks, and rule-based adjustments.

        This ensures:
        - Similar answers get similar scores (consistency layer)
        - Short answers are properly penalized
        - Role-appropriate expectations
        - Red flag penalties applied correctly
        """
        # Short answer handling
        word_count = len(answer.split())
        if word_count < MIN_ANSWER_WORDS:
            if "too_short" not in result.red_flags:
                result.red_flags.append("too_short")
            result.depth_score = max(0, result.depth_score - 10)
            result.communication_score = max(0, result.communication_score - 5)

        # Check for unprofessional tone
        answer_lower = answer.lower()
        if any(term in answer_lower for term in UNPROFESSIONAL_TERMS):
            if "unprofessional_tone" not in result.red_flags:
                result.red_flags.append("unprofessional_tone")
            result.communication_score = max(0, result.communication_score - 5)

        # Keyword overlap check (as signal only, not primary scorer)
        keyword_overlap = self._keyword_overlap(answer, keywords)
        if keywords and not keyword_overlap and word_count >= MIN_ANSWER_WORDS:
            # Only flag if answer is long enough but still misses all keywords
            if "missing_concepts" not in result.red_flags:
                result.red_flags.append("missing_concepts")
            result.technical_score = max(0, result.technical_score - 4)

        # Role-level adjustment (freshers get slight benefit of doubt)
        role_adj = self._role_level_adjustment(role_level)
        result.depth_score = self._clamp_int(result.depth_score + role_adj["depth"], 0, DEPTH_MAX)
        result.technical_score = self._clamp_int(result.technical_score + role_adj["technical"], 0, TECHNICAL_MAX)

        # Apply red flag penalties
        total_penalty = min(
            RED_FLAG_PENALTY_MAX,
            sum(RED_FLAG_PENALTIES.get(flag, 2) for flag in result.red_flags),
        )

        # Calculate overall score
        raw_overall = (
            result.relevance_score +
            result.depth_score +
            result.technical_score +
            result.communication_score
        )
        result.overall_score = max(0, raw_overall - total_penalty)

        # Normalize scores for consistency
        result = self._normalize_scores(result, word_count)

        # Apply rule-based adjustments
        result = self._apply_rule_based_adjustments(result, answer, keywords, is_short)

        # Ensure feedback is specific
        if not result.brief_feedback or len(result.brief_feedback) < 20:
            result.brief_feedback = self._generate_specific_feedback(result, answer, keywords)

        # Ensure follow-up logic is sound
        result = self._finalize_followup_decision(result, is_short, keyword_overlap)

        # Adjust confidence for short/vague answers
        if is_short or word_count < SHORT_ANSWER_THRESHOLD:
            result.confidence_score = min(result.confidence_score, 0.6)

        return result

    def _normalize_scores(self, result: EvaluationResult, word_count: int) -> EvaluationResult:
        """
        Normalize scores to ensure consistency across similar answers.

        This is the consistency layer that prevents random variation.
        """
        # Score coherence check: depth and technical should correlate somewhat
        depth_tech_gap = abs(result.depth_score - result.technical_score)
        if depth_tech_gap > 10:
            # Large gap suggests inconsistent scoring - pull toward each other
            avg = (result.depth_score + result.technical_score) // 2
            result.depth_score = self._clamp_int(avg, 0, DEPTH_MAX)
            result.technical_score = self._clamp_int(avg, 0, TECHNICAL_MAX)

        # Communication should not exceed relevance (unclear answers rarely fully relevant)
        if result.communication_score > result.relevance_score + 5:
            result.communication_score = min(result.communication_score, result.relevance_score + 3)

        # Overall should match component sum (after penalties)
        expected_overall = (
            result.relevance_score +
            result.depth_score +
            result.technical_score +
            result.communication_score -
            min(RED_FLAG_PENALTY_MAX, sum(RED_FLAG_PENALTIES.get(f, 2) for f in result.red_flags))
        )
        # Allow some tolerance but correct large mismatches
        if abs(result.overall_score - expected_overall) > 10:
            result.overall_score = max(0, expected_overall)

        return result

    def _apply_rule_based_adjustments(
        self,
        result: EvaluationResult,
        answer: str,
        keywords: list[str],
        is_short: bool,
    ) -> EvaluationResult:
        """Apply deterministic rule-based adjustments for edge cases."""
        # Rule 1: Very short answers cannot have high depth
        if is_short and result.depth_score > 10:
            result.depth_score = 8
            if "too_short" not in result.red_flags:
                result.red_flags.append("too_short")

        # Rule 2: No concrete examples mentioned -> flag it
        example_indicators = ["for example", "in my", "at my", "i built", "we used", "specifically", "like when"]
        has_example = any(indicator in answer.lower() for indicator in example_indicators)
        answer_word_count = len(answer.split())
        if not has_example and answer_word_count > 30:
            if "no_real_example" not in result.red_flags:
                result.red_flags.append("no_real_example")

        # Rule 3: Generic intro detection (starts with filler)
        generic_starts = ["that's a good question", "well", "let me think", "this is interesting"]
        if any(answer.lower().startswith(gs) for gs in generic_starts):
            # Check if there's actual content after the intro
            content_after_intro = answer
            for gs in generic_starts:
                if answer.lower().startswith(gs):
                    content_after_intro = answer[len(gs):].strip()
                    break
            if len(content_after_intro.split()) < 15:
                if "generic_intro" not in result.red_flags:
                    result.red_flags.append("generic_intro")
                result.relevance_score = max(0, result.relevance_score - 3)

        # Rule 4: Contradiction detection (basic heuristic)
        contradiction_patterns = [
            (r"\bi (?:don't|do not)\b.*\bi (?:do|did|would)\b", "Says won't/can't but then says would"),
            (r"\b(always|never)\b.*\b(sometimes|often|usually)\b", "Absolute vs qualified statement"),
        ]
        for pattern, _ in contradiction_patterns:
            if re.search(pattern, answer.lower()):
                if "contradiction" not in result.red_flags:
                    result.red_flags.append("contradiction")
                break

        return result

    def _finalize_followup_decision(
        self,
        result: EvaluationResult,
        is_short: bool,
        keyword_overlap: set[str],
    ) -> EvaluationResult:
        """Finalize the follow-up decision with clear logic."""
        needs_followup = False
        followup_focus = "technical_depth"
        followup_reason = ""

        # Determine if follow-up is needed
        if result.depth_score < 15:
            needs_followup = True
            followup_focus = "technical_depth"
            followup_reason = "The answer lacked sufficient technical depth and needs deeper exploration."
        elif result.technical_score < 15:
            needs_followup = True
            followup_focus = "missing_concepts"
            followup_reason = "Key technical concepts appear to be missing or misunderstood."
        elif result.communication_score < 8:
            needs_followup = True
            followup_focus = "clarity"
            followup_reason = "The answer was unclear and needs clarification."
        elif is_short:
            needs_followup = True
            followup_focus = "recovery"
            followup_reason = "The answer was too brief; give candidate a chance to elaborate."
        elif not keyword_overlap and len(keyword_overlap) == 0:
            needs_followup = True
            followup_focus = "missing_concepts"
            followup_reason = "The answer didn't address expected technical areas."
        elif result.red_flags and any(f in result.red_flags for f in ["vague_answer", "hand_wavy", "contradiction"]):
            needs_followup = True
            followup_focus = "clarity"
            followup_reason = "The answer contained vague or contradictory statements needing clarification."

        result.needs_followup = needs_followup
        if followup_focus:
            result.followup_focus = followup_focus
        if followup_reason:
            result.followup_reason = followup_reason

        return result

    def _generate(self, prompt: str) -> str:
        """Execute the LLM call with retry and fallback logic."""
        if hasattr(self._ai_client, "generate"):
            return self._ai_client.generate(prompt)

        if not hasattr(self._ai_client, "generate_text"):
            raise RuntimeError("AI client does not support text generation")

        coroutine = self._ai_client.generate_text(
            system_prompt="You are a strict technical interviewer. Return JSON only. No markdown.",
            user_prompt=prompt,
            temperature=0.2,  # Low temperature for consistent scoring
            max_tokens=500,
            fallback_text=json.dumps(self._fallback_response("LLM call failed").to_dict()),
        )

        return self._run_async(coroutine)

    def _run_async(self, coroutine: Any) -> str:
        """Execute async coroutine from synchronous context."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)

        future = self._thread_pool.submit(asyncio.run, coroutine)
        return future.result(timeout=15)

    # =============================================================================
    # UTILITY METHODS
    # =============================================================================

    @staticmethod
    def _get_api_key() -> str:
        """Get API key from environment."""
        key = os.getenv("GROQ_API_KEY", "")
        return key.strip().strip('"').strip("'")

    @staticmethod
    def _role_level_adjustment(role_level: str) -> dict[str, int]:
        """Return score adjustments based on role level (freshers get benefit of doubt)."""
        normalized = role_level.lower()
        if normalized == "senior":
            return {"depth": 0, "technical": 0}
        if normalized == "mid":
            return {"depth": 1, "technical": 1}
        return {"depth": 2, "technical": 2}  # fresher

    @staticmethod
    def _resolve_model() -> str:
        """Resolve model from environment or use default."""
        preferred = (
            os.getenv("ANSWER_EVALUATOR_MODEL")
            or os.getenv("GROQ_MODEL")
            or DEFAULT_EVALUATOR_MODEL
        )
        return preferred.strip() or DEFAULT_EVALUATOR_MODEL

    @staticmethod
    def _normalize_text(value: Any) -> str:
        """Normalize text by collapsing whitespace."""
        return " ".join(str(value or "").strip().split())

    @staticmethod
    def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
        """Clamp a value to an integer range."""
        try:
            integer = int(value)
        except (TypeError, ValueError):
            integer = minimum
        return max(minimum, min(maximum, integer))

    @staticmethod
    def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
        """Clamp a value to a float range."""
        try:
            f = float(value)
        except (TypeError, ValueError):
            f = minimum
        return max(minimum, min(maximum, f))

    @staticmethod
    def _parse_reasoning(value: Any) -> dict[str, str]:
        """Parse reasoning dictionary with defaults."""
        if not isinstance(value, dict):
            value = {}
        return {
            "relevance": value.get("relevance", "No reasoning provided."),
            "depth": value.get("depth", "No reasoning provided."),
            "technical": value.get("technical", "No reasoning provided."),
            "communication": value.get("communication", "No reasoning provided."),
        }

    @staticmethod
    def _parse_string_list(value: Any) -> list[str]:
        """Parse a list of strings, handling edge cases."""
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _sanitize_red_flags(value: Any) -> list[str]:
        """Sanitize red flags to snake_case, deduplicated."""
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        for item in value:
            normalized = "_".join(str(item or "").strip().lower().split())
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned

    @staticmethod
    def _validate_followup_focus(value: Any) -> str:
        """Validate followup_focus is one of the allowed types."""
        valid = {"technical_depth", "clarity", "missing_concepts", "recovery"}
        if value in valid:
            return value
        return "technical_depth"

    @staticmethod
    def _keyword_overlap(answer: str, keywords: list[str]) -> set[str]:
        """Check which keywords appear in the answer (semantic overlap)."""
        answer_lower = answer.lower()
        overlap: set[str] = set()
        for keyword in keywords:
            if keyword.lower() in answer_lower:
                overlap.add(keyword)
        return overlap

    @staticmethod
    def _generate_specific_feedback(result: EvaluationResult, answer: str, keywords: list[str]) -> str:
        """Generate specific feedback when LLM feedback is too generic."""
        feedback_parts = []

        if result.depth_score < 12:
            feedback_parts.append(
                "Your answer stays at a surface level. Explain the 'how' and 'why' behind your approach, "
                "not just the 'what'."
            )

        if result.technical_score < 12 and keywords:
            missing = [kw for kw in keywords if kw.lower() not in answer.lower()]
            if missing:
                feedback_parts.append(
                    f"Consider addressing: {', '.join(missing[:3])}. These concepts are central to the question."
                )

        if result.communication_score < 8:
            feedback_parts.append(
                "Structure your answer more clearly. Start with a direct response, then elaborate with examples."
            )

        if result.red_flags:
            if "too_short" in result.red_flags:
                feedback_parts.append(
                    "Your answer is too brief to assess your understanding. Provide more complete responses."
                )
            if "vague_answer" in result.red_flags:
                feedback_parts.append(
                    "Avoid vague statements. Use concrete examples and specific technical details."
                )
            if "no_real_example" in result.red_flags:
                feedback_parts.append(
                    "You mention experience but don't provide a concrete example. Share a specific situation."
                )

        if feedback_parts:
            return " ".join(feedback_parts)

        return (
            "Your answer demonstrates reasonable understanding. To improve, provide more specific examples "
            "and explain trade-offs in your approach."
        )

    def _fallback_response(self, reason: str = "") -> EvaluationResult:
        """Return a safe fallback response when evaluation fails."""
        return EvaluationResult(
            relevance_score=12,
            depth_score=10,
            technical_score=10,
            communication_score=8,
            reasoning={
                "relevance": "Fallback evaluation due to processing error.",
                "depth": "Fallback evaluation due to processing error.",
                "technical": "Fallback evaluation due to processing error.",
                "communication": "Fallback evaluation due to processing error.",
            },
            strengths=[],
            weaknesses=["Unable to fully evaluate answer."],
            red_flags=[],
            overall_score=40,
            confidence_score=0.3,
            brief_feedback=(
                "The answer is partially relevant but needs more depth, structure, and technical detail. "
                f"({'evaluation error' if reason else 'see weaknesses'})"
            ),
            needs_followup=True,
            followup_focus="technical_depth",
            followup_reason=reason or "Evaluation encountered an error; follow-up needed for proper assessment.",
        )

    def __del__(self) -> None:
        """Cleanup thread pool on deletion."""
        if hasattr(self, "_thread_pool"):
            try:
                self._thread_pool.shutdown(wait=False)
            except Exception:
                pass
