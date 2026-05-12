import logging
from dataclasses import dataclass

from backend.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class ProviderStatus:
    name: str
    available: bool
    reason: str = ""


class ProviderValidator:
    """
    Validates provider configuration at startup.
    In development: raises only when every provider is unavailable.
    In production: logs warnings and allows degraded operation.
    """

    def validate_all(self) -> list[ProviderStatus]:
        results = [self._validate_groq(), self._validate_gemini()]
        self._print_status_table(results)
        self._enforce_policy(results)
        return results

    def _validate_groq(self) -> ProviderStatus:
        if not settings.GROQ_API_KEY:
            return ProviderStatus("Groq", False, "GROQ_API_KEY missing")
        if len(settings.GROQ_API_KEY) < 20:
            return ProviderStatus("Groq", False, "GROQ_API_KEY too short - likely invalid")
        return ProviderStatus("Groq", True)

    def _validate_gemini(self) -> ProviderStatus:
        key = settings.effective_gemini_key()
        if not key:
            return ProviderStatus(
                "Gemini",
                False,
                "Neither GEMINI_API_KEY nor GOOGLE_API_KEY is set",
            )
        if len(key) < 20:
            return ProviderStatus("Gemini", False, "Gemini key too short - likely invalid")
        return ProviderStatus("Gemini", True)

    def _enforce_policy(self, results: list[ProviderStatus]) -> None:
        failed = [result for result in results if not result.available]
        healthy = [result for result in results if result.available]
        if not failed:
            return

        for result in failed:
            logger.warning(
                "ProviderValidationFailed provider=%s reason=%s",
                result.name,
                result.reason,
            )

        if settings.is_development and not healthy:
            reasons = "; ".join(f"{result.name}: {result.reason}" for result in failed)
            raise RuntimeError(
                "[DEVELOPMENT] Provider misconfiguration - fix before continuing.\n"
                f"Failed: {reasons}\n"
                "Check your .env file."
            )

        logger.warning(
            "ProviderDegradedMode healthy_providers=%s failed_providers=%s",
            [result.name for result in healthy],
            [result.name for result in failed],
        )

    def _print_status_table(self, results: list[ProviderStatus]) -> None:
        logger.info("=" * 46)
        logger.info("PROVIDER STATUS")
        logger.info("-" * 46)
        for result in results:
            status = "Healthy" if result.available else f"FAILED ({result.reason})"
            logger.info("  %-10s -> %s", result.name, status)
        logger.info("-" * 46)
        logger.info("  Router     -> Ready")
        logger.info("=" * 46)
