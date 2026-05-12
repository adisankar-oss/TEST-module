import asyncio
import logging
from collections import defaultdict
from typing import Callable, Awaitable
from .event_models import InterviewEvent, EventType

logger = logging.getLogger(__name__)

Handler = Callable[[InterviewEvent], Awaitable[None]]

class InterviewEventBus:
  """
  Async pub/sub event bus.
  All realtime communication flows through here.
  Preserves per-session event ordering via per-session sequence counters.
  """

  def __init__(self):
    self._subscribers:  dict[EventType, list[Handler]] = defaultdict(list)
    self._seq_counters: dict[str, int]                 = {}  # session_id → seq

  def subscribe(self, event_type: EventType, handler: Handler) -> None:
    self._subscribers[event_type].append(handler)
    logger.debug("EventBusSubscribe event=%s handler=%s",
                 event_type, handler.__name__)

  def unsubscribe(self, event_type: EventType, handler: Handler) -> None:
    handlers = self._subscribers.get(event_type, [])
    if handler in handlers:
      handlers.remove(handler)

  async def publish(self, event: InterviewEvent) -> None:
    # Assign monotonic sequence number per session
    seq = self._seq_counters.get(event.session_id, 0) + 1
    self._seq_counters[event.session_id] = seq
    event.sequence = seq

    handlers = self._subscribers.get(event.event_type, [])
    if not handlers:
      logger.debug("EventBusNoHandlers event=%s session=%s",
                   event.event_type, event.session_id)
      return

    logger.debug("EventBusPublish event=%s session=%s seq=%d handlers=%d",
                 event.event_type, event.session_id, seq, len(handlers))

    results = await asyncio.gather(
      *[self._safe_call(h, event) for h in handlers],
      return_exceptions=True
    )
    for i, r in enumerate(results):
      if isinstance(r, Exception):
        logger.error("EventHandlerFailed event=%s handler=%d error=%s",
                     event.event_type, i, r)

  async def _safe_call(self, handler: Handler, event: InterviewEvent) -> None:
    try:
      await handler(event)
    except Exception as e:
      logger.exception("EventHandlerException event=%s: %s",
                       event.event_type, e)
      raise

  def reset_session(self, session_id: str) -> None:
    self._seq_counters.pop(session_id, None)

# Singleton — import this instance everywhere
event_bus = InterviewEventBus()
