from __future__ import annotations

import asyncio
import json
import os
import socket
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from utils.logger import get_logger


GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"
FALLBACK_MODEL = "llama-3.3-70b-versatile"
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_RETRIES = 3
DEFAULT_TEMPERATURE = 0.4
DEFAULT_MAX_TOKENS = 300
RETRY_BACKOFF_SECONDS = (1, 2, 3)


@dataclass(slots=True)
class AIClientConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES


@dataclass(slots=True)
class HTTPResult:
    status_code: int
    text: str
    payload: dict[str, Any]


class AIClient:
    def __init__(self, config: AIClientConfig | None = None) -> None:
        self._logger = get_logger("services.ai_client")
        env_key = self._sanitize_api_key(os.getenv("GROQ_API_KEY"))
        env_model = (os.getenv("GROQ_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL).strip()
        self._config = config or AIClientConfig(api_key=env_key, model=env_model)
        self._config.api_key = self._sanitize_api_key(self._config.api_key)
        self._config.model = (self._config.model or DEFAULT_MODEL).strip()
        self._has_valid_key = bool(self._config.api_key)
        self._log_key_status()

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        fallback_text: str = "",
    ) -> str:
        if not self._has_valid_key:
            self._log_missing_key()
            return fallback_text

        current_model = self._config.model
        fallback_attempted = False

        for attempt in range(1, self._config.max_retries + 1):
            try:
                payload = {
                    "model": current_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                print(f"Using model: {current_model}")
                result = await asyncio.to_thread(self._post_json, payload)
                print("Calling GROQ...")
                print("Status Code:", result.status_code)
                print("Response:", result.text[:200])
                content = self._extract_content(result.payload)
                if content:
                    return content.strip()
                self._log_structured_error(
                    status_code=result.status_code,
                    response="Groq response did not contain message content",
                    attempt=attempt,
                )
            except Exception as exc:
                if self._is_model_decommissioned_error(exc) and not fallback_attempted:
                    fallback_attempted = True
                    current_model = FALLBACK_MODEL
                    self._logger.warning(
                        json.dumps(
                            {
                                "event": "ai_warning",
                                "provider": "groq",
                                "warning": "model_decommissioned",
                                "previous_model": self._config.model,
                                "fallback_model": current_model,
                            }
                        )
                    )
                    continue
                self._handle_request_exception(exc, attempt, current_model)

            if attempt < self._config.max_retries:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt - 1])

        return fallback_text

    def _post_json(self, payload: dict[str, Any]) -> HTTPResult:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ai-interview-avatar/1.0",
        }
        req = request.Request(GROQ_API_URL, data=body, headers=headers, method="POST")

        try:
            with request.urlopen(req, timeout=self._config.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8", errors="replace")
                payload_json = self._safe_json_loads(raw_body)
                return HTTPResult(
                    status_code=getattr(response, "status", 200),
                    text=raw_body,
                    payload=payload_json,
                )
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            print("Calling GROQ...")
            print("Status Code:", exc.code)
            print("Response:", error_body[:200])
            self._log_structured_error(
                status_code=exc.code,
                response=error_body,
            )
            raise RuntimeError(self._http_error_message(exc.code, error_body)) from exc
        except error.URLError as exc:
            self._log_structured_error(status_code=None, response=str(exc.reason))
            raise RuntimeError(f"GROQ network error: {exc.reason}") from exc
        except socket.timeout as exc:
            self._log_structured_error(status_code=None, response="Request timed out")
            raise TimeoutError("GROQ request timed out") from exc

    def _handle_request_exception(self, exc: Exception, attempt: int, model: str) -> None:
        self._logger.error(
            json.dumps(
                {
                    "event": "ai_error",
                    "provider": "groq",
                    "attempt": attempt,
                    "model": model,
                    "error": str(exc),
                }
            )
        )

    @staticmethod
    def _is_model_decommissioned_error(exc: Exception) -> bool:
        return "model_decommissioned" in str(exc).lower()

    def _log_structured_error(
        self,
        *,
        status_code: int | None,
        response: str,
        attempt: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "event": "ai_error",
            "provider": "groq",
            "status_code": status_code,
            "response": response[:500],
        }
        if attempt is not None:
            payload["attempt"] = attempt
        self._logger.error(json.dumps(payload))

    def _log_key_status(self) -> None:
        if self._has_valid_key:
            masked_key = f"{self._config.api_key[:5]}***"
            print("GROQ KEY LOADED:", masked_key)
            self._logger.info(
                json.dumps(
                    {
                        "event": "groq_key_loaded",
                        "key_preview": masked_key,
                        "model": self._config.model,
                    }
                )
            )
            return

        self._log_missing_key()

    def _log_missing_key(self) -> None:
        print("ERROR: GROQ_API_KEY not found")
        self._logger.error(
            json.dumps(
                {
                    "event": "ai_error",
                    "provider": "groq",
                    "status_code": None,
                    "response": (
                        "GROQ_API_KEY not found. Restart the server after setting the env var "
                        "in the same terminal that launches uvicorn."
                    ),
                }
            )
        )

    @staticmethod
    def _sanitize_api_key(raw_value: str | None) -> str:
        if raw_value is None:
            return ""
        return raw_value.strip().strip('"').strip("'").strip()

    @staticmethod
    def _safe_json_loads(raw_body: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _extract_content(response: dict[str, Any]) -> str:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return ""
        message = first_choice.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content", "")
        return content if isinstance(content, str) else ""

    @staticmethod
    def _http_error_message(status_code: int, response: str) -> str:
        if status_code == 401:
            return "GROQ authentication failed (401 invalid API key)"
        if status_code == 403:
            return (
                "GROQ access forbidden (403). Check the API key, remove quotes/spaces, "
                "and ensure the server was restarted in the same terminal."
            )
        if status_code == 429:
            return "GROQ rate limit reached (429)"
        return f"GROQ HTTP {status_code}: {response[:200]}"
