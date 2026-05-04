"""WebSocket hub for broadcasting session events in real-time."""

from __future__ import annotations

import json
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from utils.logger import get_logger


class WebSocketHub:
    """Manages WebSocket connections and broadcasts events to connected clients."""

    def __init__(self) -> None:
        self._logger = get_logger("fsm.websocket_hub")
        # session_id -> set of WebSocket connections
        self._active_connections: dict[str, set[WebSocket]] = {}

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        """Register a WebSocket connection for a session."""
        await websocket.accept()
        if session_id not in self._active_connections:
            self._active_connections[session_id] = set()
        self._active_connections[session_id].add(websocket)
        self._logger.info(
            json.dumps(
                {
                    "event": "websocket_connected",
                    "session_id": session_id,
                    "active_connections": len(self._active_connections[session_id]),
                }
            )
        )

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        """Unregister a WebSocket connection."""
        if session_id in self._active_connections:
            self._active_connections[session_id].discard(websocket)
            if not self._active_connections[session_id]:
                del self._active_connections[session_id]
        self._logger.info(
            json.dumps(
                {
                    "event": "websocket_disconnected",
                    "session_id": session_id,
                    "remaining_connections": len(
                        self._active_connections.get(session_id, set())
                    ),
                }
            )
        )

    async def broadcast(self, session_id: str, message: Any) -> None:
        """Send a message to all connected clients for a session.

        Accepts both plain dicts and Pydantic BaseModel instances
        (e.g. LiveEventEnvelope from the FSM engine).
        """
        if session_id not in self._active_connections:
            return

        # Normalise to dict so send_json always gets a plain dict.
        if hasattr(message, "model_dump"):
            data = message.model_dump()
        elif isinstance(message, dict):
            data = message
        else:
            data = {"raw": str(message)}

        disconnected = set()
        for websocket in self._active_connections[session_id]:
            try:
                await websocket.send_json(data)
            except (WebSocketDisconnect, RuntimeError):
                disconnected.add(websocket)

        # Clean up disconnected clients
        for ws in disconnected:
            await self.disconnect(session_id, ws)

    async def send_state_changed(
        self,
        session_id: str,
        old_state: str,
        new_state: str,
        question_number: int | None = None,
    ) -> None:
        """Broadcast state transition event."""
        await self.broadcast(
            session_id,
            {
                "event": "state_changed",
                "session_id": session_id,
                "old_state": old_state,
                "new_state": new_state,
                "question_number": question_number,
            },
        )

    async def send_question_delivered(
        self,
        session_id: str,
        question: str,
        question_number: int,
    ) -> None:
        """Broadcast question delivery event."""
        await self.broadcast(
            session_id,
            {
                "event": "question_delivered",
                "session_id": session_id,
                "question": question,
                "question_number": question_number,
            },
        )

    async def send_answer_evaluated(
        self,
        session_id: str,
        score: int,
        feedback: str,
        next_state: str,
    ) -> None:
        """Broadcast answer evaluation event."""
        await self.broadcast(
            session_id,
            {
                "event": "answer_evaluated",
                "session_id": session_id,
                "score": score,
                "feedback": feedback,
                "next_state": next_state,
            },
        )

    async def send_session_ended(
        self,
        session_id: str,
        ended_reason: str,
    ) -> None:
        """Broadcast session end event."""
        await self.broadcast(
            session_id,
            {
                "event": "session_ended",
                "session_id": session_id,
                "ended_reason": ended_reason,
            },
        )

    async def send_session_error(
        self,
        session_id: str,
        error_reason: str,
    ) -> None:
        """Broadcast session error event."""
        await self.broadcast(
            session_id,
            {
                "event": "session_error",
                "session_id": session_id,
                "error_reason": error_reason,
            },
        )

    async def close_all(self) -> None:
        """Close all active WebSocket connections.

        Called during server shutdown to ensure every connection receives
        a clean close frame instead of an abnormal 1006 closure.
        """
        all_sessions = list(self._active_connections.items())
        self._active_connections.clear()

        for session_id, websockets in all_sessions:
            for websocket in websockets:
                try:
                    await websocket.close(code=1001, reason="Server shutting down")
                except Exception:
                    pass
            self._logger.info(
                json.dumps(
                    {
                        "event": "websocket_close_all",
                        "session_id": session_id,
                        "closed_count": len(websockets),
                    }
                )
            )

