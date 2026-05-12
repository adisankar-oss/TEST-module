import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta

from event_models import (
  InterviewEvent, EventType, ConnectionState, WebSocketMessage
)
from connection_registry import ConnectionRegistry
from event_bus import InterviewEventBus
from session_sync import SessionSyncService
from websocket_protocol import serialize_message, deserialize_message

# ── helpers ──────────────────────────────────────────────────────────────

def make_event(
  event_type: EventType = EventType.QUESTION_GENERATED,
  session_id: str = "sess-001"
) -> InterviewEvent:
  return InterviewEvent(event_type=event_type, session_id=session_id)

def make_mock_ws() -> MagicMock:
  ws = MagicMock()
  ws.send_text = AsyncMock()
  ws.accept    = AsyncMock()
  return ws

# ── ConnectionRegistry ───────────────────────────────────────────────────

def test_register_and_retrieve():
  reg = ConnectionRegistry()
  ws  = make_mock_ws()
  reg.register("s1", "c1", ws)
  assert reg.get_socket("s1") is ws
  assert reg.get_record("s1").connection_id == "c1"

def test_reconnect_replaces_old_connection():
  reg = ConnectionRegistry()
  ws1, ws2 = make_mock_ws(), make_mock_ws()
  reg.register("s1", "c1", ws1)
  reg.register("s1", "c2", ws2)
  assert reg.get_socket("s1") is ws2
  assert reg.get_record("s1").connection_id == "c2"

def test_deregister_clears_session():
  reg = ConnectionRegistry()
  reg.register("s1", "c1", make_mock_ws())
  reg.deregister("s1")
  assert reg.get_socket("s1") is None

def test_stale_detection():
  reg = ConnectionRegistry()
  reg.register("s1", "c1", make_mock_ws())
  # Simulate heartbeat > threshold ago
  record = reg.get_record("s1")
  record.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=60)
  stale = reg.get_stale_sessions()
  assert "s1" in stale

def test_active_count():
  reg = ConnectionRegistry()
  reg.register("s1", "c1", make_mock_ws())
  reg.register("s2", "c2", make_mock_ws())
  reg.mark_stale("s1")
  assert reg.active_count() == 1

# ── EventBus ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_delivered_to_subscriber():
  bus = InterviewEventBus()
  received = []
  async def handler(e):
    received.append(e)
  bus.subscribe(EventType.QUESTION_GENERATED, handler)
  await bus.publish(make_event(EventType.QUESTION_GENERATED))
  assert len(received) == 1

@pytest.mark.asyncio
async def test_sequence_increments_per_session():
  bus = InterviewEventBus()
  sequences = []
  async def handler(e):
    sequences.append(e.sequence)
  bus.subscribe(EventType.STATE_CHANGED, handler)
  for _ in range(3):
    await bus.publish(make_event(EventType.STATE_CHANGED, "s1"))
  assert sequences == [1, 2, 3]

@pytest.mark.asyncio
async def test_failed_handler_does_not_block_others():
  bus = InterviewEventBus()
  called = []
  async def bad_handler(e):
    raise RuntimeError("boom")
  async def good_handler(e):
    called.append(True)
  bus.subscribe(EventType.AI_THINKING, bad_handler)
  bus.subscribe(EventType.AI_THINKING, good_handler)
  await bus.publish(make_event(EventType.AI_THINKING))
  assert called  # good handler still ran

# ── SessionSyncService ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replay_returns_recorded_events():
  # Bypass __init__ to avoid automatic bus subscriptions
  sync = SessionSyncService.__new__(SessionSyncService)
  sync._history  = __import__('collections').defaultdict(
    lambda: __import__('collections').deque(maxlen=20)
  )
  sync._delivered = __import__('collections').defaultdict(set)
  e = make_event(EventType.QUESTION_GENERATED, "s1")
  await sync._record_event(e)
  replayed = await sync.replay_on_reconnect("s1")
  assert len(replayed) == 1
  assert replayed[0].event_id == e.event_id

def test_duplicate_detection():
  sync = SessionSyncService.__new__(SessionSyncService)
  sync._delivered = __import__('collections').defaultdict(set)
  assert not sync.is_duplicate("s1", "evt-001")
  assert sync.is_duplicate("s1", "evt-001")  # second call is duplicate

def test_heartbeats_not_recorded():
  sync = SessionSyncService.__new__(SessionSyncService)
  sync._history   = __import__('collections').defaultdict(
    lambda: __import__('collections').deque(maxlen=20)
  )
  sync._delivered = __import__('collections').defaultdict(set)
  e = make_event(EventType.HEARTBEAT_PING, "s1")
  asyncio.get_event_loop().run_until_complete(sync._record_event(e))
  assert len(sync._history["s1"]) == 0

# ── Protocol ─────────────────────────────────────────────────────────────

def test_serialize_deserialize_round_trip():
  msg = WebSocketMessage(
    event_type="QUESTION_GENERATED",
    session_id="s1",
    timestamp="2025-01-01T00:00:00",
    payload={"question": "How does Redis work?"}
  )
  raw      = serialize_message(msg)
  restored = deserialize_message(raw)
  assert restored is not None
  assert restored.event_type == "QUESTION_GENERATED"
  assert restored.payload["question"] == "How does Redis work?"

def test_malformed_message_returns_none():
  result = deserialize_message("{not valid json{{")
  assert result is None

def test_missing_fields_returns_none():
  result = deserialize_message('{"event_type": "X"}')  # missing required fields
  assert result is None
