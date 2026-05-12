import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import WebSocket
from .event_models import ConnectionRecord, ConnectionState

logger = logging.getLogger(__name__)

class ConnectionRegistry:
  """
  Tracks all active WebSocket connections.
  Handles replacement on reconnect and stale cleanup.
  Thread-safe for single-process async use.
  """

  STALE_AFTER_SECONDS = 30

  def __init__(self):
    self._records:  dict[str, ConnectionRecord] = {}   # session_id → record
    self._sockets:  dict[str, WebSocket]        = {}   # session_id → socket
    self._conn_ids: dict[str, str]              = {}   # connection_id → session_id

  def register(
    self,
    session_id:      str,
    connection_id:   str,
    websocket:       WebSocket,
    client_metadata: dict = {}
  ) -> ConnectionRecord:
    if session_id in self._records:
      logger.info("ConnectionReplaced session=%s old_conn=%s new_conn=%s",
                  session_id,
                  self._records[session_id].connection_id,
                  connection_id)
      old_conn_id = self._records[session_id].connection_id
      self._conn_ids.pop(old_conn_id, None)

    record = ConnectionRecord(
      session_id      = session_id,
      connection_id   = connection_id,
      connected_at    = datetime.now(timezone.utc),
      connection_state= ConnectionState.CONNECTED,
      client_metadata = client_metadata,
    )
    self._records[session_id]      = record
    self._sockets[session_id]      = websocket
    self._conn_ids[connection_id]  = session_id

    logger.info("ConnectionRegistered session=%s conn=%s",
                session_id, connection_id)
    return record

  def deregister(self, session_id: str) -> None:
    record = self._records.pop(session_id, None)
    self._sockets.pop(session_id, None)
    if record:
      self._conn_ids.pop(record.connection_id, None)
      logger.info("ConnectionDeregistered session=%s", session_id)

  def get_socket(self, session_id: str) -> Optional[WebSocket]:
    return self._sockets.get(session_id)

  def get_record(self, session_id: str) -> Optional[ConnectionRecord]:
    return self._records.get(session_id)

  def update_heartbeat(self, session_id: str) -> None:
    record = self._records.get(session_id)
    if record:
      record.last_heartbeat = datetime.now(timezone.utc)

  def mark_stale(self, session_id: str) -> None:
    record = self._records.get(session_id)
    if record:
      record.connection_state = ConnectionState.STALE
      logger.warning("ConnectionMarkedStale session=%s", session_id)

  def mark_recovered(self, session_id: str) -> None:
    record = self._records.get(session_id)
    if record:
      record.connection_state = ConnectionState.CONNECTED
      logger.info("ConnectionRecovered session=%s", session_id)

  def get_stale_sessions(self) -> list[str]:
    now = datetime.now(timezone.utc)
    stale = []
    for sid, record in self._records.items():
      if record.last_heartbeat is None:
        continue
      age = (now - record.last_heartbeat).total_seconds()
      if age > self.STALE_AFTER_SECONDS:
        stale.append(sid)
    return stale

  def all_session_ids(self) -> list[str]:
    return list(self._records.keys())

  def active_count(self) -> int:
    return sum(
      1 for r in self._records.values()
      if r.connection_state == ConnectionState.CONNECTED
    )
