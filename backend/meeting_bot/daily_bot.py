from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

try:
    from daily import CallClient, Daily
except ImportError as exc:  # pragma: no cover - depends on optional runtime dependency
    CallClient = None  # type: ignore[assignment]
    Daily = None  # type: ignore[assignment]
    _DAILY_IMPORT_ERROR: Exception | None = exc
else:
    _DAILY_IMPORT_ERROR = None

if TYPE_CHECKING:
    from daily import CallClient as DailyCallClient
else:
    DailyCallClient = Any


LOGGER = logging.getLogger(__name__)


class InterviewBot:
    _daily_initialized = False

    def __init__(self, room_url: str, bot_name: str = "Alex - AI Interviewer") -> None:
        self.room_url = room_url
        self.bot_name = bot_name
        self.client: DailyCallClient | None = None

    def join(self) -> None:
        self._ensure_daily_sdk()
        self._ensure_client()
        assert self.client is not None

        self.client.set_user_name(self.bot_name)
        self.client.join(
            meeting_url=self.room_url,
            client_settings=self._build_client_settings(),
        )

        LOGGER.info("🤖 Bot joined room: %s", self.room_url)

    async def start(self) -> None:
        try:
            self.join()
        except Exception:
            LOGGER.exception("Failed to start Daily bot for room: %s", self.room_url)

    def inject_audio(self, pcm_chunk: bytes) -> None:
        pass

    def on_audio_received(self, participant_id: str, audio: bytes) -> None:
        pass

    def _ensure_daily_sdk(self) -> None:
        if _DAILY_IMPORT_ERROR is not None or Daily is None:
            raise RuntimeError(
                "daily-python is not installed. Install it with `pip install daily-python`."
            ) from _DAILY_IMPORT_ERROR

        if self.__class__._daily_initialized:
            return

        try:
            Daily.init()
        except Exception as exc:  # pragma: no cover - SDK specific behavior
            message = str(exc).lower()
            if "already" not in message or "initialized" not in message:
                raise
        self.__class__._daily_initialized = True

    def _ensure_client(self) -> None:
        if self.client is None:
            if CallClient is None:
                raise RuntimeError("Daily CallClient is unavailable")
            self.client = CallClient()

    def _build_client_settings(self) -> dict[str, Any]:
        return {
            "inputs": {
                "camera": {"isEnabled": False},
                "microphone": {"isEnabled": True},
            },
            "publishing": {
                "camera": {"isPublishing": False},
                "microphone": {"isPublishing": True},
            },
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bot = InterviewBot("PASTE_ROOM_URL_HERE")
    bot.join()
