from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from database import AsyncSessionFactory, close_database, init_database
from dependencies import get_session_engine
from fsm.engine import SessionEngine
from fsm.websocket_hub import WebSocketHub
from fsm.transitions import RecruiterCommand
from models import Base
from routes import router as ws_router
from schemas import (
    SessionAnswerRequest,
    SessionAnswerResponse,
    SessionCommandRequest,
    SessionCommandResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionEventResponse,
    SessionStatusResponse,
)
from services.ai_client import AIClient
from services.evaluation_service import EvaluationService
from services.question_service import QuestionService
from utils.logger import configure_logging, get_logger


def websocket_runtime_available() -> bool:
    return any(
        importlib.util.find_spec(module_name) is not None
        for module_name in ("websockets", "wsproto")
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger = get_logger("main")
    await init_database(Base.metadata)
    ai_client = AIClient()

    if not websocket_runtime_available():
        logger.warning(
            "WebSocket transport library not installed for this Python interpreter. "
            "Use the project virtualenv Python to run uvicorn, or install "
            "'uvicorn[standard]' / 'websockets' into the active interpreter."
        )

    app.state.session_engine = SessionEngine(
        session_factory=AsyncSessionFactory,
        websocket_hub=WebSocketHub(),
        question_service=QuestionService(ai_client=ai_client),
        evaluation_service=EvaluationService(),
    )

    yield

    await app.state.session_engine.shutdown()
    await close_database()


app = FastAPI(
    title="AI Interview Avatar - Module 1",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include WebSocket routes
app.include_router(ws_router)


@app.get("/")
async def home() -> dict[str, str]:
    return {"message": "M1 Session Orchestrator service is running"}


@app.get("/api/v1/sessions/{session_id}/live")
async def session_live_upgrade_required(session_id: str) -> JSONResponse:
    detail = (
        "This endpoint is WebSocket-only. Connect with ws:// or wss:// instead of http://. "
        "If the server logs 'No supported WebSocket library detected', start uvicorn with "
        "C:\\ai-interview-avatar\\.venv\\Scripts\\python.exe -m uvicorn main:app "
        "--host 127.0.0.1 --port 8000 --reload."
    )
    return JSONResponse(status_code=426, content={"session_id": session_id, "detail": detail})


@app.post("/api/v1/sessions", response_model=SessionCreateResponse, status_code=201)
async def create_session(
    payload: SessionCreateRequest,
    session_engine: SessionEngine = Depends(get_session_engine),
) -> SessionCreateResponse:
    return await session_engine.create_session(payload)


@app.get("/api/v1/sessions", response_model=list[SessionStatusResponse])
async def list_sessions(
    session_engine: SessionEngine = Depends(get_session_engine),
) -> list[SessionStatusResponse]:
    return await session_engine.list_sessions()


@app.get("/api/v1/sessions/{session_id}", response_model=SessionStatusResponse)
async def get_session_status(
    session_id: str,
    session_engine: SessionEngine = Depends(get_session_engine),
) -> SessionStatusResponse:
    return await session_engine.get_status(session_id)


@app.post("/api/v1/sessions/{session_id}/command", response_model=SessionCommandResponse)
async def issue_session_command(
    session_id: str,
    payload: SessionCommandRequest,
    session_engine: SessionEngine = Depends(get_session_engine),
) -> SessionCommandResponse:
    try:
        command = RecruiterCommand(payload.command)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Invalid command") from exc
    return await session_engine.apply_command(session_id, command)


@app.post("/api/v1/sessions/{session_id}/answer", response_model=SessionAnswerResponse)
async def submit_candidate_answer(
    session_id: str,
    payload: SessionAnswerRequest,
    session_engine: SessionEngine = Depends(get_session_engine),
) -> SessionAnswerResponse:
    return await session_engine.submit_answer(session_id, payload.answer)


@app.post(
    "/api/v1/sessions/{session_id}/events/candidate_left",
    response_model=SessionEventResponse,
)
async def candidate_left(
    session_id: str,
    session_engine: SessionEngine = Depends(get_session_engine),
) -> SessionEventResponse:
    return await session_engine.handle_candidate_disconnected(session_id)


@app.post(
    "/api/v1/sessions/{session_id}/events/candidate_rejoined",
    response_model=SessionEventResponse,
)
async def candidate_rejoined(
    session_id: str,
    session_engine: SessionEngine = Depends(get_session_engine),
) -> SessionEventResponse:
    return await session_engine.handle_candidate_reconnected(session_id)
