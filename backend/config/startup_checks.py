import asyncio
import logging

from backend.config.settings import settings

logger = logging.getLogger(__name__)


async def run_startup_checks() -> None:
    """
    Called from main.py @app.on_event("startup").
    Runs provider health pings and fails only when every configured provider is unhealthy.
    """
    logger.info("StartupChecksBegin env=%s", settings.APP_ENV)

    results = await asyncio.gather(_ping_groq(), _ping_gemini(), return_exceptions=True)
    errors = [result for result in results if isinstance(result, Exception)]
    successes = [result for result in results if result is True]

    for error in errors:
        logger.error("StartupHealthCheckFailed error=%s", error)

    if errors and not successes and settings.is_development:
        raise RuntimeError(f"Startup health check failed: {errors[0]}")

    logger.info("StartupChecksComplete")


async def _ping_groq() -> bool | None:
    if not settings.GROQ_API_KEY:
        logger.warning("GroqPingSkipped: no API key")
        return None
    try:
        from groq import Groq

        client = Groq(api_key=settings.GROQ_API_KEY)
        await asyncio.wait_for(
            asyncio.to_thread(
                client.chat.completions.create,
                model=settings.GROQ_MODEL,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            ),
            timeout=8,
        )
        logger.info("GroqPing status=healthy model=%s", settings.GROQ_MODEL)
        return True
    except Exception as exc:
        logger.error("GroqPing status=failed error=%s", exc)
        raise RuntimeError(f"Groq ping failed: {exc}") from exc


async def _ping_gemini() -> bool | None:
    key = settings.effective_gemini_key()
    if not key:
        logger.warning("GeminiPingSkipped: no API key")
        return None
    try:
        from backend.services.llm.providers.gemini_sdk import create_gemini_adapter

        client = create_gemini_adapter(key)
        await asyncio.wait_for(
            asyncio.to_thread(
                client.generate_content,
                model=settings.GEMINI_MODEL,
                contents="ping",
                temperature=0.0,
                max_output_tokens=1,
            ),
            timeout=10,
        )
        logger.info(
            "GeminiPing status=healthy model=%s sdk=%s",
            settings.GEMINI_MODEL,
            client.sdk_name,
        )
        return True
    except Exception as exc:
        logger.error("GeminiPing status=failed error=%s model=%s", exc, settings.GEMINI_MODEL)
        raise RuntimeError(f"Gemini ping failed: {exc}") from exc
