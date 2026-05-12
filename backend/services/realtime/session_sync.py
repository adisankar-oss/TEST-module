import logging
from collections import defaultdict, deque
from .event_models import InterviewEvent, EventType
from .event_bus import event_bus

# Configuration import
from backend.core.config import settings

logger = logging.getLogger(__name__)

MAX_REPLAY_EVENTS = settings.MAX_REPLAY_EVENTS

class SessionSyncService:
  """
  Maintains per-session event history for reconnect replay.
  Prevents duplicate delivery using event_id dedup set.
  """

  def __init__(self):
    self._history:  dict[str, deque[InterviewEvent]] = defaultdict(
      lambda: deque(maxlen=MAX_REPLAY_EVENTS)
    )
    self._delivered: dict[str, set[str]] = defaultdict(set)

    for et in EventType:
      event_bus.subscribe(et, self._record_event)

  async def _record_event(self, event: InterviewEvent) -> None:
    if event.event_type in (
      EventType.HEARTBEAT_PING, EventType.HEARTBEAT_PONG
    ):
      return  # do not replay heartbeats
    self._history[event.session_id].append(event)

  def is_duplicate(self, session_id: str, event_id: str) -> bool:
    if event_id in self._delivered[session_id]:
      return True
    self._delivered[session_id].add(event_id)
    return False

  async def replay_on_reconnect(self, session_id: str) -> list[InterviewEvent]:
    events = list(self._history.get(session_id, []))
    logger.info("SessionReplay session=%s events=%d", session_id, len(events))
    return events

  def clear_session(self, session_id: str) -> None:
    self._history.pop(session_id, None)
    self._delivered.pop(session_id, None)
    logger.info("SessionSyncCleared session=%s", session_id)

# Singleton instance
session_sync = SessionSyncService()
