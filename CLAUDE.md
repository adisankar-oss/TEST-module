# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Interview Avatar - A real-time AI-powered interview system with 7 modules:
- **M1 (Session Orchestrator)**: Central FSM-based controller in `backend/`
- **M2 (Meeting Bot)**: Video conference integration
- **M3 (Speech Engine)**: STT/TTS pipeline
- **M4 (Avatar Engine)**: Talking avatar video generation
- **M5 (Question Intelligence)**: Adaptive LLM question generation
- **M6 (Answer Evaluator)**: LLM-based answer scoring
- **M7 (Report Generator)**: Interview summary reports

This repo contains M1 (backend) and a basic frontend test client.

## Quick Start

```bash
# Start backend (from backend/)
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Frontend: open frontend/index.html in browser
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/sessions` | Create interview session |
| GET | `/api/v1/sessions` | List all sessions |
| GET | `/api/v1/sessions/{id}` | Get session status |
| POST | `/api/v1/sessions/{id}/answer` | Submit candidate answer |
| POST | `/api/v1/sessions/{id}/command` | Recruiter commands (pause/resume/skip/end) |
| WS | `/api/v1/sessions/{id}/live` | Real-time event stream |

## Architecture

### Backend Stack
- **FastAPI** + **SQLAlchemy 2.0** (async) + **PostgreSQL**
- **Groq API** (llama-3.1-8b-instant) for LLM operations
- **WebSockets** for real-time event broadcasting

### Core Modules

```
backend/
├── main.py                  # FastAPI app, endpoints, lifespan
├── database.py              # AsyncSession, connection pool, migrations
├── models.py                # InterviewSession, SessionEvent (SQLAlchemy)
├── schemas.py               # Pydantic request/response models
├── fsm/
│   ├── engine.py            # SessionEngine: FSM orchestration (MAIN)
│   ├── decision.py          # Score-based next-action logic
│   ├── transitions.py       # State validation, RecruiterCommand
│   └── websocket_hub.py     # Live event broadcasting
├── services/
│   ├── question_service.py  # LLM question generation + context
│   ├── answer_evaluator.py  # LLM answer scoring (1-10)
│   ├── evaluation_service.py| # Orchestrates evaluation pipeline
│   └── session_context_service.py  # Last 3 Q&A memory
├── ai/
│   ├── llm_client.py        # Groq API wrapper
│   ├── prompt_builder.py    # Dynamic prompt construction
│   ├── topic_selector.py    # Question topic selection
│   └── duplicate_checker.py # Prevents question repetition
└── tests/
```

### FSM State Machine

```
WAITING → INTRO → ASKING → LISTENING → EVALUATING → DECISION
                                                    ↓
                    ┌───────────────────────────────┼───────────────────────────────┐
                    ↓                               ↓                               ↓
                FOLLOWUP                         ASKING                          WRAPPING
                (score ≤ 4)                   (score 5-7)                    (max_questions)
                    ↓                               ↓                               ↓
                ASKING                          LISTENING                         ENDED
```

Key states:
- **LISTENING**: FSM pauses, awaits `POST /answer`
- **EVALUATING**: LLM scores answer, generates feedback
- **DECISION**: Routes to FOLLOWUP/NEXT/WRAP based on score

### Session Lifecycle

1. `POST /sessions` → creates `InterviewSession`, starts FSM loop
2. FSM runs: generates greeting → questions → evaluates answers
3. Each answer submitted via `POST /answer` resumes paused FSM
4. Ends when `max_questions` reached or timeout
5. Events broadcast via WebSocket to `/live` endpoint

## Configuration

### Environment Variables
```bash
GROQ_API_KEY=gsk_...           # Required
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/interview_db
GROQ_MODEL=llama-3.1-8b-instant  # Optional
LOG_LEVEL=INFO                   # Optional
```

### Session Config (create request)
```json
{
  "max_questions": 10,
  "max_duration_minutes": 45,
  "answer_timeout_seconds": 300,
  "followup_score_max": 4,
  "next_score_min": 8,
  "topics": ["technical_skills", "problem_solving", "behavioural"]
}
```

## Testing

### Manual API Test
```bash
# Create session
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"candidate_id":"test","job_id":"backend","meeting_url":"https://example.com","meeting_type":"google_meet","schedule_time":"2024-02-14T10:00:00Z"}'

# Submit answer (when state=LISTENING)
curl -X POST http://localhost:8000/api/v1/sessions/{id}/answer \
  -H "Content-Type: application/json" \
  -d '{"answer":"I have 5 years of experience..."}'

# WebSocket (real-time events)
websocat ws://localhost:8000/api/v1/sessions/{id}/live
```

### Test Files
- `backend/tests/test_question_bank_fallback.py` - Fallback question bank tests

## Key Design Decisions

- **Context Memory**: SessionContextService keeps last 3 Q&A pairs for adaptive questioning
- **Adaptive Difficulty**: Low scores → follow-up; high scores → harder questions
- **Fallback System**: If Groq API fails, uses hardcoded fallback questions (score=5)
- **Non-blocking FSM**: LISTENING state uses `asyncio.Event` to pause without blocking
- **Event Sourcing**: All state changes logged to `session_events` table

## Common Operations

```bash
# Check session state
GET /api/v1/sessions/{id}

# Pause/resume interview
POST /api/v1/sessions/{id}/command {"command": "pause"}

# Handle candidate disconnect
POST /api/v1/sessions/{id}/events/candidate_left
```

## Database Schema

**interview_sessions**: `id, candidate_id, job_id, state, status, current_question_number, config (JSON), topics (JSON[]), created_at, started_at, ended_at`

**session_events**: `id, session_id, event, payload (JSON), created_at`

Auto-migration runs on startup (adds columns if missing).
