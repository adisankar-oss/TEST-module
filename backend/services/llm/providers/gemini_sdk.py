from __future__ import annotations

from typing import Any


class GeminiSDKUnavailableError(ImportError):
    """Raised when no supported Gemini SDK is installed."""


class GeminiSDKAdapter:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._modern_client = None
        self._legacy_module = None
        self._legacy_types = None
        self._load()

    @property
    def sdk_name(self) -> str:
        if self._modern_client is not None:
            return "google-genai"
        if self._legacy_module is not None:
            return "google-generativeai"
        raise GeminiSDKUnavailableError("Gemini SDK is not available")

    def generate_content(
        self,
        *,
        model: str,
        contents: str,
        temperature: float,
        max_output_tokens: int,
        response_mime_type: str | None = None,
        system_instruction: str | None = None,
    ) -> Any:
        if self._modern_client is not None:
            from google.genai import types

            config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                system_instruction=system_instruction,
            )
            if response_mime_type:
                config.response_mime_type = response_mime_type
            return self._modern_client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )

        if self._legacy_module is not None and self._legacy_types is not None:
            generation_config = self._legacy_types.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_mime_type=response_mime_type,
            )
            model_client = self._legacy_module.GenerativeModel(
                model_name=model,
                system_instruction=system_instruction or None,
            )
            return model_client.generate_content(
                contents,
                generation_config=generation_config,
            )

        raise GeminiSDKUnavailableError("Gemini SDK is not available")

    def _load(self) -> None:
        try:
            from google import genai
        except ImportError:
            genai = None

        if genai is not None:
            self._modern_client = genai.Client(api_key=self._api_key)
            return

        try:
            import google.generativeai as legacy_genai
            from google.generativeai import types as legacy_types
        except ImportError as exc:
            raise GeminiSDKUnavailableError(
                "Install `google-genai` or `google-generativeai` to enable Gemini."
            ) from exc

        legacy_genai.configure(api_key=self._api_key)
        self._legacy_module = legacy_genai
        self._legacy_types = legacy_types


def create_gemini_adapter(api_key: str) -> GeminiSDKAdapter:
    return GeminiSDKAdapter(api_key=api_key)


def extract_response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    candidates = getattr(response, "candidates", None)
    if not candidates:
        return ""

    candidate = candidates[0]
    content = getattr(candidate, "content", None)
    if content is None:
        return ""

    parts = getattr(content, "parts", None)
    if not parts:
        return ""

    texts: list[str] = []
    for part in parts:
        part_text = getattr(part, "text", None)
        if isinstance(part_text, str) and part_text:
            texts.append(part_text)
    return "\n".join(texts).strip()


def extract_usage(response: Any) -> dict[str, int]:
    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata is None:
        return {}

    usage: dict[str, int] = {}
    usage_fields = {
        "prompt_tokens": "prompt_token_count",
        "completion_tokens": "candidates_token_count",
        "total_tokens": "total_token_count",
    }
    for target_key, source_attr in usage_fields.items():
        value = getattr(usage_metadata, source_attr, None)
        if value is None:
            continue
        try:
            usage[target_key] = int(value)
        except (TypeError, ValueError):
            continue
    return usage
