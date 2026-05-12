# AI Interview Avatar

Real-time AI-powered interview system with 7 modules.

## Run (from project root only)

Always run from: `C:\ai-interview-avatar\`
Never run from inside `backend/`

```bash
# Install package in editable mode (one time)
pip install -e .

# Start server
python -m uvicorn backend.main:app \
  --host 127.0.0.1 --port 8000 --reload
```

Why this matters:
Running from inside `backend/` makes Python unable
to resolve "backend" as a package. The project
root must be on sys.path, which `-e` install and
running from root both guarantee.

## Alternative: use run.py

```bash
python run.py
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
| GET | `/health` | Health check |
| GET | `/health/ready` | Readiness probe |
| GET | `/health/live` | Liveness probe |

## Configuration

Copy `.env.example` to `.env` and fill in your API keys:

- `GROQ_API_KEY` — Required
- `GEMINI_API_KEY` — Required