from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LatencySnapshot:
    count: int
    average_ms: float
    max_ms: float


class LatencyMonitor:
    def __init__(self, max_samples: int = 200) -> None:
        self._max_samples = max_samples
        self._samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=max_samples))
        self._lock = asyncio.Lock()

    async def record(self, provider: str, task_type: str, latency_ms: float) -> None:
        key = self._key(provider, task_type)
        async with self._lock:
            self._samples[key].append(float(latency_ms))

    async def snapshot(self) -> dict[str, LatencySnapshot]:
        async with self._lock:
            result: dict[str, LatencySnapshot] = {}
            for key, values in self._samples.items():
                series = list(values)
                if not series:
                    continue
                result[key] = LatencySnapshot(
                    count=len(series),
                    average_ms=round(sum(series) / len(series), 2),
                    max_ms=round(max(series), 2),
                )
            return result

    @staticmethod
    def _key(provider: str, task_type: str) -> str:
        return f"{provider}:{task_type}"
