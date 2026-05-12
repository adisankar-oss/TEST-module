import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProviderMetrics:
    """Rolling metrics for provider health scoring."""
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    malformed_count: int = 0
    latency_sum_ms: float = 0.0
    latency_count: int = 0
    recent_latencies: deque = field(default_factory=lambda: deque(maxlen=20))
    recent_errors: deque = field(default_factory=lambda: deque(maxlen=10))


class ProviderHealthMonitor:
    """
    Enhanced in-memory health tracker with degradation scoring.
    Tracks: failures, timeouts, malformed outputs, latency trends.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: int = 45,
        latency_warning_threshold_ms: float = 5000.0,
    ) -> None:
        self._failure_threshold = max(1, int(failure_threshold))
        self._cooldown_seconds = max(0, int(cooldown_seconds))
        self._latency_warning = max(1000, float(latency_warning_threshold_ms))

        self._status: dict[str, dict[str, Any]] = {
            "groq": {
                "healthy": True,
                "last_failure": None,
                "last_failure_at": None,
                "failures": 0,
            },
            "gemini": {
                "healthy": True,
                "last_failure": None,
                "last_failure_at": None,
                "failures": 0,
            },
        }

        self._metrics: dict[str, ProviderMetrics] = {
            "groq": ProviderMetrics(),
            "gemini": ProviderMetrics(),
        }

    def record_success(self, provider: str) -> None:
        state = self._status.get(provider)
        if state is None:
            return
        state["healthy"] = True
        state["failures"] = 0
        state["last_failure"] = None
        state["last_failure_at"] = None

        metrics = self._metrics.get(provider)
        if metrics:
            metrics.success_count += 1

    def record_failure(self, provider: str, reason: str) -> None:
        state = self._status.get(provider)
        if state is None:
            return
        state["failures"] += 1
        state["last_failure"] = reason
        state["last_failure_at"] = time.time()
        state["healthy"] = state["failures"] < self._failure_threshold

        metrics = self._metrics.get(provider)
        if metrics:
            metrics.failure_count += 1
            metrics.recent_errors.append({"reason": reason, "at": time.time()})

        logger.warning(
            "ProviderFailureRecorded provider=%s failures=%d reason=%s",
            provider,
            state["failures"],
            reason,
        )

    def record_timeout(self, provider: str) -> None:
        """Record a timeout event."""
        state = self._status.get(provider)
        if state is None:
            return

        metrics = self._metrics.get(provider)
        if metrics:
            metrics.timeout_count += 1

        self.record_failure(provider, "timeout")

    def record_malformed_output(self, provider: str) -> None:
        """Record a malformed output event."""
        metrics = self._metrics.get(provider)
        if metrics:
            metrics.malformed_count += 1
            metrics.recent_errors.append({"reason": "malformed_output", "at": time.time()})

    def record_latency(self, provider: str, latency_ms: float) -> None:
        """Record latency for trend analysis."""
        metrics = self._metrics.get(provider)
        if metrics:
            metrics.latency_sum_ms += latency_ms
            metrics.latency_count += 1
            metrics.recent_latencies.append(latency_ms)

    def get_degradation_score(self, provider: str) -> float:
        """
        Calculate provider degradation score (0.0 = healthy, 1.0 = degraded).
        Factors: failure rate, timeout rate, malformed rate, latency trends.
        """
        metrics = self._metrics.get(provider)
        if not metrics:
            return 0.0

        total_requests = metrics.success_count + metrics.failure_count
        if total_requests == 0:
            return 0.0

        # Failure rate component (40% weight)
        failure_rate = metrics.failure_count / max(total_requests, 1)
        failure_score = min(failure_rate * 2, 1.0) * 0.4

        # Timeout rate component (20% weight)
        timeout_rate = metrics.timeout_count / max(total_requests, 1)
        timeout_score = min(timeout_rate * 3, 1.0) * 0.2

        # Malformed output rate component (20% weight)
        malformed_rate = metrics.malformed_count / max(total_requests, 1)
        malformed_score = min(malformed_rate * 3, 1.0) * 0.2

        # Latency degradation component (20% weight)
        latency_score = 0.0
        if metrics.recent_latencies:
            recent = list(metrics.recent_latencies)
            avg_latency = sum(recent) / len(recent)
            if avg_latency > self._latency_warning:
                latency_score = min((avg_latency / self._latency_warning - 1) * 0.5, 1.0) * 0.2

        return min(failure_score + timeout_score + malformed_score + latency_score, 1.0)

    def is_available(self, provider: str) -> bool:
        state = self._status.get(provider)
        if state is None:
            return False
        if state["healthy"]:
            return True
        failed_at = state.get("last_failure_at")
        if failed_at is None:
            return False
        return (time.time() - failed_at) >= self._cooldown_seconds

    def is_healthy(self, provider: str) -> bool:
        state = self._status.get(provider, {})
        return bool(state.get("healthy", False))

    def should_avoid(self, provider: str) -> bool:
        """Check if provider should be avoided due to degradation."""
        return self.get_degradation_score(provider) > 0.7

    def snapshot(self) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}
        for provider, state in self._status.items():
            metrics = self._metrics.get(provider, ProviderMetrics())
            snapshot[provider] = {
                "healthy": bool(state["healthy"]),
                "available": self.is_available(provider),
                "failures": int(state["failures"]),
                "last_failure": state["last_failure"],
                "degradation_score": round(self.get_degradation_score(provider), 3),
                "total_requests": metrics.success_count + metrics.failure_count,
                "success_rate": round(
                    metrics.success_count / max(metrics.success_count + metrics.failure_count, 1),
                    3,
                ),
                "avg_latency_ms": round(metrics.latency_sum_ms / max(metrics.latency_count, 1), 1)
                if metrics.latency_count > 0
                else None,
            }
        return snapshot

    def status_dict(self) -> dict[str, dict[str, Any]]:
        return self.snapshot()


health_monitor = ProviderHealthMonitor()
