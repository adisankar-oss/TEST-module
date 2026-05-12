from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


PLACEHOLDER_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass(slots=True)
class RenderResult:
    success: bool
    prompt: str
    missing_vars: list[str] = field(default_factory=list)
    fallback_used: bool = False


class SafePromptRenderer:
    def render(
        self,
        template: str,
        variables: dict[str, Any] | None,
        template_id: str | None = None,
        session_id: str | None = None,
        optional_vars: set[str] | None = None,
    ) -> RenderResult:
        variables = dict(variables or {})
        optional_vars = set(optional_vars or set())
        placeholders = PLACEHOLDER_PATTERN.findall(template or "")

        missing_vars: list[str] = []
        rendered = str(template or "")
        for placeholder in placeholders:
            if placeholder in variables:
                replacement = " ".join(str(variables[placeholder]).strip().split())
            elif placeholder in optional_vars:
                replacement = ""
            else:
                replacement = ""
                if placeholder not in missing_vars:
                    missing_vars.append(placeholder)
            rendered = rendered.replace("{" + placeholder + "}", replacement)

        cleaned = " ".join(rendered.strip().split())
        success = not missing_vars
        if not cleaned:
            cleaned = " ".join(PLACEHOLDER_PATTERN.sub("", str(template or "")).strip().split())

        return RenderResult(
            success=success,
            prompt=cleaned,
            missing_vars=missing_vars,
            fallback_used=not success,
        )
