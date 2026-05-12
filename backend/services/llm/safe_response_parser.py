from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ParseResult:
    success: bool
    content: str
    parsed: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    repair_attempted: bool = False


CODE_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
JSON_BRACKET_PATTERN = re.compile(r"\{[\s\S]*\}")


class SafeResponseParser:
    """Reliable response parsing with malformed output recovery."""

    @staticmethod
    def parse_json_response(
        raw_content: str,
        required_fields: set[str] | None = None,
        min_length: int = 10,
    ) -> ParseResult:
        """Parse JSON response with recovery for malformed outputs."""
        if not raw_content or not raw_content.strip():
            return ParseResult(success=False, content="", errors=["empty_response"])

        cleaned = SafeResponseParser._clean_response(raw_content)

        if len(cleaned) < min_length:
            return ParseResult(success=False, content=cleaned, errors=["response_too_short"])

        # Try direct parse first
        parsed = SafeResponseParser._extract_json(cleaned)
        if parsed is not None:
            if required_fields:
                missing = required_fields - set(parsed.keys())
                if missing:
                    return ParseResult(
                        success=False,
                        content=cleaned,
                        parsed=parsed,
                        errors=[f"missing_required_fields:{','.join(sorted(missing))}"],
                    )
            return ParseResult(success=True, content=cleaned, parsed=parsed)

        # Attempt repair: try to fix common JSON issues
        repaired = SafeResponseParser._repair_json(cleaned)
        if repaired:
            parsed = SafeResponseParser._extract_json(repaired)
            if parsed is not None:
                return ParseResult(
                    success=True,
                    content=repaired,
                    parsed=parsed,
                    errors=[],
                    repair_attempted=True,
                )

        # Last resort: extract structured text
        text_result = SafeResponseParser._extract_text_fields(cleaned)
        if text_result:
            return ParseResult(
                success=True,
                content=cleaned,
                parsed=text_result,
                errors=["repaired_from_text"],
                repair_attempted=True,
            )

        return ParseResult(success=False, content=cleaned, errors=["malformed_json_unrecoverable"])

    @staticmethod
    def parse_text_response(
        raw_content: str,
        min_length: int = 5,
        must_end_with_question: bool = False,
    ) -> ParseResult:
        """Parse text response with quality validation."""
        if not raw_content or not raw_content.strip():
            return ParseResult(success=False, content="", errors=["empty_response"])

        cleaned = SafeResponseParser._clean_response(raw_content)

        if len(cleaned) < min_length:
            return ParseResult(success=False, content=cleaned, errors=["response_too_short"])

        errors = []
        if must_end_with_question and not cleaned.rstrip().endswith("?"):
            errors.append("must_end_with_question")

        return ParseResult(
            success=not errors,
            content=cleaned,
            errors=errors,
        )

    @staticmethod
    def _clean_response(raw: str) -> str:
        """Clean and normalize response content."""
        if not raw:
            return ""

        # Handle code fences
        match = CODE_FENCE_PATTERN.match(raw.strip())
        if match:
            raw = match.group(1)

        # Normalize whitespace
        cleaned = " ".join(raw.strip().split())

        return cleaned

    @staticmethod
    def _extract_json(content: str) -> dict[str, Any] | None:
        """Extract JSON object from content."""
        # Try direct parse
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # Try bracket extraction
        match = JSON_BRACKET_PATTERN.search(content)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _repair_json(content: str) -> str | None:
        """Attempt to repair common JSON issues."""
        # Common issues to fix:
        # 1. Trailing commas
        # 2. Single quotes instead of double
        # 3. Missing quotes around keys

        repaired = content

        # Fix trailing commas before closing braces/brackets
        repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)

        # Fix single quotes (limited cases)
        repaired = re.sub(r"'([^']*)'", r'"\1"', repaired)

        # Ensure keys are quoted
        repaired = re.sub(r"(\w+):", r'"\1":', repaired)

        # Validate the repair
        try:
            json.loads(repaired)
            return repaired
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_text_fields(content: str) -> dict[str, Any] | None:
        """Extract structured fields from plain text as fallback."""
        lines = [line.strip() for line in content.split("\n") if line.strip()]

        if not lines:
            return None

        # For question generation, extract from common patterns
        result = {"raw_text": content}

        # Try to find question field
        for line in lines:
            if "question" in line.lower() and ":" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    result["question"] = parts[1].strip().strip('"').strip("'")
                    break

        if len(lines) == 1:
            result["question"] = lines[0]

        return result if len(result) > 1 else None


def safe_parse_json(
    raw: str,
    required_fields: set[str] | None = None,
) -> ParseResult:
    """Convenience function for JSON parsing."""
    return SafeResponseParser.parse_json_response(raw, required_fields)


def safe_parse_text(
    raw: str,
    min_length: int = 5,
) -> ParseResult:
    """Convenience function for text parsing."""
    return SafeResponseParser.parse_text_response(raw, min_length=min_length)