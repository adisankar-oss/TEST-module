from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket

from dependencies import get_session_engine_ws
from fsm.engine import SessionEngine

router = APIRouter()


@router.websocket("/api/v1/sessions/{session_id}/live")
async def session_live(
    websocket: WebSocket,
    session_id: str,
    session_engine: SessionEngine = Depends(get_session_engine_ws),
) -> None:
    """WebSocket endpoint for real-time session events.

    Delegates entirely to SessionEngine.handle_live_connection()
    which handles accept, validation, snapshot, keepalive, and cleanup.
    """
    await session_engine.handle_live_connection(websocket, session_id)
