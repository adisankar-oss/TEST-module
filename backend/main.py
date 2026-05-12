from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager

# Load .env before any other module reads os.environ
from backend.config.env_loader import load_env
load_env()

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.database import AsyncSessionFactory, close_database, init_database
from backend.dependencies import get_session_engine
from backend.fsm.engine import SessionEngine
from backend.fsm.websocket_hub import WebSocketHub
from backend.fsm.transitions import RecruiterCommand
from backend.models import Base
from backend.routes import router as ws_router
from backend.api.health import router as health_router
from backend.schemas import (
    SessionAnswerRequest,
    SessionAnswerResponse,
    SessionCommandRequest,
    SessionCommandResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionEventResponse,
    SessionStatusResponse,
)
from backend.services.ai_client import AIClient
from backend.services.evaluation_service import EvaluationService
from backend.services.llm.task_router import get_task_router
from backend.services.question_service import QuestionService
from backend.utils.logger import configure_logging, get_logger


def websocket_runtime_available() -> bool:
    return any(
        importlib.util.find_spec(module_name) is not None
        for module_name in ("websockets", "wsproto")
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    import sys, os, logging

    # Startup diagnostics
    startup_logger = logging.getLogger(__name__)
    startup_logger.info(
        "StartupEnvironment project_root=%s "
        "python=%s cwd=%s",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        sys.version.split()[0],
        os.getcwd(),
    )

    configure_logging()
    logger = get_logger("main")

    # Load settings after env is loaded
    from backend.config.settings import settings
    from backend.config.provider_validator import ProviderValidator
    from backend.config.startup_checks import run_startup_checks

    logger.info("AppStartup version=%s env=%s", settings.APP_VERSION, settings.APP_ENV)

    # Validate provider configuration at startup
    ProviderValidator().validate_all()
    await run_startup_checks()

    await init_database(Base.metadata)
    ai_client = AIClient()
    task_router = get_task_router()

    if not websocket_runtime_available():
        logger.warning(
            "WebSocket transport library not installed for this Python interpreter. "
            "Use the project virtualenv Python to run uvicorn, or install "
            "'uvicorn[standard]' / 'websockets' into the active interpreter."
        )

    app.state.session_engine = SessionEngine(
        session_factory=AsyncSessionFactory,
        websocket_hub=WebSocketHub(),
        question_service=QuestionService(ai_client=ai_client, task_router=task_router),
        evaluation_service=EvaluationService(task_router=task_router),
    )

    yield

    await app.state.session_engine.shutdown()
    await close_database()


app = FastAPI(
    title="AI Interview Avatar - Module 1",
    version="2.0.0",
    lifespan=lifespan,
)

# Request ID tracking middleware (must be first)
from backend.core.middleware import RequestIDMiddleware
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include WebSocket routes
app.include_router(ws_router)

# Include health check routes
app.include_router(health_router)


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
