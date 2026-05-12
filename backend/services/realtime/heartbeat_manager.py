import asyncio
import logging
from datetime import datetime, timezone
from .event_models import InterviewEvent, EventType
from .connection_registry import ConnectionRegistry
from .event_bus import event_bus

# Configuration import
from backend.core.config import settings

logger = logging.getLogger(__name__)

class HeartbeatManager:
  """
  Sends periodic pings and detects stale connections.
  Runs as a background task — does not block interview flow.
  """

  # Use configurable intervals
  PING_INTERVAL_SECONDS = settings.HEARTBEAT_INTERVAL_SECONDS
  STALE_TIMEOUT_SECONDS = settings.STALE_CONNECTION_TIMEOUT_SECONDS

  def __init__(self, registry: ConnectionRegistry):
    self.registry = registry
    self._task: asyncio.Task | None = None
    event_bus.subscribe(EventType.HEARTBEAT_PONG, self._on_pong)

  def start(self) -> None:
    if self._task is None or self._task.done():
      self._task = asyncio.create_task(self._loop())
      logger.info("HeartbeatManagerStarted")

  def stop(self) -> None:
    if self._task:
      self._task.cancel()
      logger.info("HeartbeatManagerStopped")

  async def _loop(self) -> None:
    while True:
      await asyncio.sleep(self.PING_INTERVAL_SECONDS)
      await self._ping_all()
      await self._cleanup_stale()

  async def _ping_all(self) -> None:
    for sid in self.registry.all_session_ids():
      await event_bus.publish(InterviewEvent(
        event_type = EventType.HEARTBEAT_PING,
        session_id = sid,
        payload    = {"ts": datetime.now(timezone.utc).isoformat()},
      ))

  async def _cleanup_stale(self) -> None:
    stale = self.registry.get_stale_sessions()
    for sid in stale:
      logger.warning("HeartbeatStaleCleanup session=%s", sid)
      self.registry.mark_stale(sid)
      await event_bus.publish(InterviewEvent(
        event_type = EventType.CONNECTION_LOST,
        session_id = sid,
        payload    = {"reason": "heartbeat_timeout"},
      ))

  async def _on_pong(self, event: InterviewEvent) -> None:
    self.registry.update_heartbeat(event.session_id)
    logger.debug("HeartbeatPong session=%s", event.session_id)
