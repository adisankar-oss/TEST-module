from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid

class EventType(str, Enum):
  INTERVIEW_STARTED       = "INTERVIEW_STARTED"
  STATE_CHANGED           = "STATE_CHANGED"
  QUESTION_GENERATED      = "QUESTION_GENERATED"
  ANSWER_RECEIVED         = "ANSWER_RECEIVED"
  EVALUATION_STARTED      = "EVALUATION_STARTED"
  EVALUATION_COMPLETED    = "EVALUATION_COMPLETED"
  FOLLOWUP_TRIGGERED      = "FOLLOWUP_TRIGGERED"
  FOLLOWUP_RESOLVED       = "FOLLOWUP_RESOLVED"
  AI_THINKING             = "AI_THINKING"
  MODEL_RESPONSE_STREAM   = "MODEL_RESPONSE_STREAM"
  INTERVIEW_COMPLETED     = "INTERVIEW_COMPLETED"
  CONNECTION_LOST         = "CONNECTION_LOST"
  CONNECTION_RECOVERED    = "CONNECTION_RECOVERED"
  HEARTBEAT_PING          = "HEARTBEAT_PING"
  HEARTBEAT_PONG          = "HEARTBEAT_PONG"

class ConnectionState(str, Enum):
  CONNECTED    = "connected"
  DISCONNECTED = "disconnected"
  STALE        = "stale"
  RECOVERING   = "recovering"

class InterviewEvent(BaseModel):
  event_id:   str        = Field(default_factory=lambda: str(uuid.uuid4()))
  event_type: EventType
  session_id: str
  timestamp:  datetime   = Field(
    default_factory=lambda: datetime.now(timezone.utc)
  )
  payload:    dict[str, Any] = Field(default_factory=dict)
  sequence:   int        = 0   # monotonic per-session counter

class ConnectionRecord(BaseModel):
  session_id:       str
  connection_id:    str
  connected_at:     datetime
  last_heartbeat:   Optional[datetime] = None
  connection_state: ConnectionState    = ConnectionState.CONNECTED
  client_metadata:  dict[str, Any]     = Field(default_factory=dict)

class WebSocketMessage(BaseModel):
  """Wire format — strict schema for all WS messages."""
  event_type: str
  session_id: str
  timestamp:  str
  payload:    dict[str, Any] = Field(default_factory=dict)
  sequence:   int            = 0
  protocol_version: str      = "1.0"

  @classmethod
  def from_event(cls, event: InterviewEvent) -> "WebSocketMessage":
    return cls(
      event_type = event.event_type.value,
      session_id = event.session_id,
      timestamp  = event.timestamp.isoformat(),
      payload    = event.payload,
      sequence   = event.sequence,
    )
