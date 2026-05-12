import json
import logging
import uuid
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from .event_models import InterviewEvent, EventType, WebSocketMessage
from .connection_registry import ConnectionRegistry
from .event_bus import event_bus

logger = logging.getLogger(__name__)

class WebSocketManager:
  """
  Centralized WebSocket lifecycle management.
  Decoupled from FSM — communicates only via event bus.
  """

  def __init__(self, registry: ConnectionRegistry):
    self.registry = registry
    # Subscribe to all outbound events
    for et in [
      EventType.QUESTION_GENERATED,
      EventType.EVALUATION_COMPLETED,
      EventType.FOLLOWUP_TRIGGERED,
      EventType.FOLLOWUP_RESOLVED,
      EventType.AI_THINKING,
      EventType.MODEL_RESPONSE_STREAM,
      EventType.STATE_CHANGED,
      EventType.INTERVIEW_STARTED,
      EventType.INTERVIEW_COMPLETED,
      EventType.CONNECTION_LOST,
      EventType.CONNECTION_RECOVERED,
      EventType.HEARTBEAT_PING,
    ]:
      event_bus.subscribe(et, self._on_event)

  async def connect(
    self,
    websocket:  WebSocket,
    session_id: str,
    client_metadata: dict = {},
  ) -> str:
    await websocket.accept()
    conn_id = str(uuid.uuid4())
    self.registry.register(session_id, conn_id, websocket, client_metadata)

    await event_bus.publish(InterviewEvent(
      event_type = EventType.CONNECTION_RECOVERED,
      session_id = session_id,
      payload    = {"connection_id": conn_id},
    ))
    return conn_id

  async def disconnect(self, session_id: str) -> None:
    self.registry.deregister(session_id)
    await event_bus.publish(InterviewEvent(
      event_type = EventType.CONNECTION_LOST,
      session_id = session_id,
      payload    = {},
    ))

  async def send(self, session_id: str, event: InterviewEvent) -> bool:
    ws = self.registry.get_socket(session_id)
    if not ws:
      logger.warning("SendFailed:NoSocket session=%s event=%s",
                     session_id, event.event_type)
      return False
    try:
      msg = WebSocketMessage.from_event(event)
      await ws.send_text(msg.json())
      return True
    except Exception as e:
      logger.error("SendFailed session=%s event=%s error=%s",
                   session_id, event.event_type, e)
      self.registry.mark_stale(session_id)
      return False

  async def receive_text(self, websocket: WebSocket) -> dict | None:
    try:
      raw = await websocket.receive_text()
      return json.loads(raw)
    except json.JSONDecodeError as e:
      logger.warning("MalformedWebSocketMessage: %s", e)
      return None
    except WebSocketDisconnect:
      return None

  async def broadcast(
    self,
    session_ids: list[str],
    event: InterviewEvent,
  ) -> None:
    for sid in session_ids:
      await self.send(sid, event)

  async def _on_event(self, event: InterviewEvent) -> None:
    """Event bus handler — forwards events to correct WS connection."""
    await self.send(event.session_id, event)

# Singleton
_registry = ConnectionRegistry()
ws_manager = WebSocketManager(_registry)
