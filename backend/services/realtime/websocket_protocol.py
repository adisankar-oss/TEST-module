import json
import logging
from pydantic import ValidationError
from .event_models import WebSocketMessage

logger = logging.getLogger(__name__)

def serialize_message(msg: WebSocketMessage) -> str:
  return msg.json()

def deserialize_message(raw: str) -> WebSocketMessage | None:
  try:
    data = json.loads(raw)
    return WebSocketMessage(**data)
  except (json.JSONDecodeError, ValidationError, TypeError) as e:
    logger.warning("ProtocolDeserializeFailed: %s | raw=%s", e, raw[:200])
    return None

def make_error_message(session_id: str, reason: str) -> str:
  from datetime import datetime
  return WebSocketMessage(
    event_type = "ERROR",
    session_id = session_id,
    timestamp  = datetime.utcnow().isoformat(),
    payload    = {"reason": reason},
  ).json()
