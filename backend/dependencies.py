from __future__ import annotations

from fastapi import Request, WebSocket

from fsm.engine import SessionEngine


def get_session_engine(request: Request) -> SessionEngine:
    """HTTP dependency — resolves SessionEngine from app.state."""
    return request.app.state.session_engine


def get_session_engine_ws(websocket: WebSocket) -> SessionEngine:
    """WebSocket dependency — resolves SessionEngine from app.state."""
    return websocket.app.state.session_engine
