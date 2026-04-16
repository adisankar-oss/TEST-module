from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket

from database import AsyncSessionFactory, close_database, init_database
from fsm.engine import SessionEngine
from fsm.websocket_hub import WebSocketHub
from fsm.transitions import RecruiterCommand
from models import Base
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
from utils.logger import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    await init_database(Base.metadata)
    ai_client = AIClient()

    app.state.session_engine = SessionEngine(
        session_factory=AsyncSessionFactory,
        websocket_hub=WebSocketHub(),
        question_service=QuestionService(ai_client=ai_client),
        evaluation_service=EvaluationService(ai_client=ai_client),
    )

    yield

    await app.state.session_engine.shutdown()
    await close_database()


app = FastAPI(
    title="AI Interview Avatar - Module 1",
    version="2.0.0",
    lifespan=lifespan,
)


def get_session_engine(request: Request) -> SessionEngine:
    return request.app.state.session_engine


@app.get("/")
async def home() -> dict[str, str]:
    return {"message": "M1 Session Orchestrator service is running"}


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


@app.websocket("/api/v1/sessions/{session_id}/live")
async def session_live(websocket: WebSocket, session_id: str) -> None:
    await websocket.app.state.session_engine.handle_live_connection(websocket, session_id)
