from __future__ import annotations

import json
import os
import socket
from urllib import error, request


DEFAULT_PROVIDER = os.getenv("QUESTION_LLM_PROVIDER", "groq").strip().lower() or "groq"
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("QUESTION_LLM_TIMEOUT_SECONDS", "2.0"))
DEFAULT_TEMPERATURE = float(os.getenv("QUESTION_LLM_TEMPERATURE", "0.7"))
DEFAULT_MAX_TOKENS = int(os.getenv("QUESTION_LLM_MAX_TOKENS", "220"))

PROVIDER_CONFIG = {
    "groq": {
        "api_key_env": "GROQ_API_KEY",
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "default_model": os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
        or "llama-3.1-8b-instant",
    },
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "default_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
    },
}


def ask_llm(prompt: str) -> str:
    provider = DEFAULT_PROVIDER if DEFAULT_PROVIDER in PROVIDER_CONFIG else "groq"
    config = PROVIDER_CONFIG[provider]
    api_key = _sanitize_env(os.getenv(config["api_key_env"]))
    if not api_key:
        raise RuntimeError(f"{config['api_key_env']} is not configured")

    payload = {
        "model": config["default_model"],
        "temperature": DEFAULT_TEMPERATURE,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "messages": [
            {
                "role": "system",
                "content": "You generate interview question JSON. Respond with JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "ai-interview-avatar/1.0",
    }
    req = request.Request(config["endpoint"], data=body, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            return _extract_content(parsed)
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{provider} HTTP {exc.code}: {raw[:200]}") from exc
    except (error.URLError, socket.timeout, TimeoutError) as exc:
        raise RuntimeError(f"{provider} request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider} response was not valid JSON: {exc}") from exc


def _sanitize_env(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip().strip('"').strip("'").strip()


def _extract_content(payload: dict) -> str:
    choices = payload.get("choices", [])
    if not choices:
        raise RuntimeError("LLM response did not include choices")
    message = choices[0].get("message", {})
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM response did not include message content")
    return content.strip()
