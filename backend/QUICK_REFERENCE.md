"""
QUICK REFERENCE: API Usage Guide

Copy-paste snippets for common operations.
"""

# ============================================================================
# QUICK API REFERENCE
# ============================================================================

"""
1. CREATE SESSION
───────────────────────────────────────────────
curl -X POST http://localhost:8000/api/v1/sessions \\
  -H "Content-Type: application/json" \\
  -d '{
    "candidate_id": "cand_12345",
    "job_id": "backend_engineer",
    "meeting_url": "https://meet.google.com/abc",
    "meeting_type": "google_meet",
    "schedule_time": "2024-02-14T15:00:00Z",
    "config": {
      "max_duration_minutes": 45,
      "max_questions": 10,
      "followup_score_max": 4,
      "next_score_min": 8,
      "answer_timeout_seconds": 300
    }
  }'

Response:
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "WAITING",
  "join_url": "https://meet.google.com/abc"
}

Save session_id: SESSION_ID="550e8400-e29b-41d4-a716-446655440000"


2. WATCH REAL-TIME EVENTS (WebSocket)
───────────────────────────────────────────────
websocat ws://localhost:8000/api/v1/sessions/${SESSION_ID}/live

Or with curl:
curl -i -N -H "Connection: Upgrade" \\
     -H "Upgrade: websocket" \\
     http://localhost:8000/api/v1/sessions/${SESSION_ID}/live

Events received:
{
  "event": "state_changed",
  "old_state": "INTRO",
  "new_state": "ASKING",
  "question_number": 1
}

{
  "event": "question_delivered",
  "question": "Tell me about yourself...",
  "question_number": 1
}


3. CHECK SESSION STATUS
───────────────────────────────────────────────
curl http://localhost:8000/api/v1/sessions/${SESSION_ID}

Response:
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "state": "LISTENING",
  "current_question_number": 1,
  "duration_seconds": 45,
  "max_questions": 10,
  "max_duration_minutes": 45
}


4. SUBMIT CANDIDATE ANSWER
───────────────────────────────────────────────
curl -X POST http://localhost:8000/api/v1/sessions/${SESSION_ID}/answer \\
  -H "Content-Type: application/json" \\
  -d '{
    "answer": "I have 7 years of experience building distributed systems
              with Python and Go. Specializing in high-traffic services
              handling millions of requests per day."
  }'

Response:
{
  "question": "Tell me about yourself and the problems you have been solving.",
  "answer": "I have 7 years...",
  "score": 8,
  "feedback": "Clear background with relevant experience mentioned.",
  "next_state": "DECISION"
}

Repeat this step for each question until session ends.


5. LIST ALL SESSIONS
───────────────────────────────────────────────
curl http://localhost:8000/api/v1/sessions

Response:
[
  {
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "state": "ENDED",
    "current_question_number": 10,
    "duration_seconds": 1245,
    "max_questions": 10,
    "max_duration_minutes": 45,
    "ended_reason": "completed"
  },
  ...
]


6. SEND RECRUITER COMMAND (Pause/Resume/etc)
───────────────────────────────────────────────
curl -X POST http://localhost:8000/api/v1/sessions/${SESSION_ID}/command \\
  -H "Content-Type: application/json" \\
  -d '{"command": "pause"}'

Valid commands: pause, resume, skip_question, end_interview, extend_5min

Response:
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "command": "pause",
  "state": "WAITING",
  "max_duration_minutes": 45
}
"""


# ============================================================================
# WORKFLOW EXAMPLE (Complete Interview)
# ============================================================================

"""
=== START: Create Session ===
POST /sessions
←  session_id = "abc-123"
    state = WAITING

[FSM runs in background]

=== Q1: Greeting ===
WebSocket Event: state_changed (INTRO → ASKING)
WebSocket Event: question_delivered
  "Tell me about yourself and the problems you have been solving recently."

=== Q1: Waiting for Answer ===
Session state: LISTENING
question_number: 1

[UI shows question to candidate, waits for input]

=== Q1: Candidate Responds ===
POST /answer
{
  "answer": "I have 5 years building backend systems. Recently worked on
             a real-time analytics platform handling 100K events/sec."
}

←  score: 8
    feedback: "Strong background with quantified impact."
    next_state: DECISION

WebSocket Event: answer_evaluated (score: 8, feedback: "...")

[FSM resumes]

WebSocket Event: state_changed (DECISION → ASKING)
WebSocket Event: question_delivered
  Q2: "Walk me through how you handled a production incident."

=== Q2: Waiting for Answer ===
Session state: LISTENING
question_number: 2

[UI shows Q2]

=== Q2: Candidate Responds (Poor answer) ===
POST /answer
{
  "answer": "There was an issue. I fixed it."
}

←  score: 3
    feedback: "Need more specific details about diagnosis and resolution."
    next_state: DECISION

WebSocket Event: answer_evaluated (score: 3, feedback: "...")

[FSM resumes]

WebSocket Event: state_changed (DECISION → FOLLOWUP)
  decision: "FOLLOWUP" (score 3 ≤ maxFollowupScore 4)

WebSocket Event: state_changed (FOLLOWUP → ASKING)
WebSocket Event: question_delivered
  Q2b: "In that situation, what were the key symptoms you observed,
        and how did you systematically isolate the root cause?"

=== Q2b: Candidate Responds (Better follow-up answer) ===
POST /answer
{
  "answer": "I saw high latency in the database query logs. Checked slow_log,
             found a missing index on user_sessions.created_at. Added index,
             latency dropped from 2s to 50ms for affected queries."
}

←  score: 7
    feedback: "Good systematic diagnosis. Could mention impact metrics."
    next_state: DECISION

[FSM resumes]

WebSocket Event: state_changed (DECISION → ASKING)
WebSocket Event: question_delivered
  Q3: "Tell me about a time you had to make a difficult architectural decision."

=== Q3: Candidate Responds ===
POST /answer
{
  "answer": "..."
}

[Pattern repeats N times until max_questions reached]

=== Final Q: Get to Wrapping ===
[After 10 questions completed]

WebSocket Event: state_changed (DECISION → WRAPPING)
WebSocket Event: state_changed (WRAPPING → ENDED)
WebSocket Event: session_ended
  ended_reason: "completed"

=== END: Session Complete ===
GET /sessions/{session_id}
←  state: ENDED
    duration_seconds: 1245
    ended_reason: "completed"
"""


# ============================================================================
# FSM STATE DIAGRAM
# ============================================================================

"""
    [Start]
      ↓
   WAITING (~1s delay)
      ↓
    INTRO (generate greeting, ~2-3s)
      ↓
    ASKING (generate question, ~2-3s)
      ↓
    LISTENING (pause for user input, ∞ or timeout)
      ↓ (when POST /answer called)
   EVALUATING (evaluate answer, ~1-3s)
      ↓
    DECISION (decide next action)
      ├─→ score ≤ 4: FOLLOWUP (same topic, drill deeper)
      │                  ↓
      │            ASKING (show follow-up question)
      │                  ↓
      │            LISTENING (pause for user input)
      │                  ↓→ back to EVALUATING
      │
      ├─→ score 5-7: ASKING (next question, same difficulty)
      │                  ↓
      │            LISTENING (pause for user input)
      │                  ↓→ back to EVALUATING
      │
      ├─→ score ≥ 8: ASKING (next question, harder)
      │                  ↓
      │            LISTENING (pause for user input)
      │                  ↓→ back to EVALUATING
      │
      └─→ question# ≥ max: WRAPPING
                          ↓
                        ENDED (interview complete)

    ERROR (on unhandled exception)
      ↓
    ENDED
"""


# ============================================================================
# CONTEXT MEMORY PROGRESSION
# ============================================================================

"""
Session Context Grows with Each Q&A:

Q1 Answer Submitted:
  context = [
    {
      "question_number": 1,
      "question": "Tell me about yourself...",
      "answer": "I have 5 years...",
      "score": 8,
      "feedback": "Strong background..."
    }
  ]

Q2 Question Generation (uses this context):
  → QuestionService passes context to LLM
  → Next question adapts based on score 8 (harder difficulty)
  → Avoids repeating Q1 topic

Q2 Answer Submitted:
  context = [
    { Q1 data },
    {
      "question_number": 2,
      "question": "Walk me through a production incident...",
      "answer": "There was an issue...",
      "score": 3,
      "feedback": "Need more details..."
    }
  ]

Q3 Question Generation:
  → LLM sees score 3 (recent poor performance)
  → Adjusts track to "behavioral" (help candidate succeed)
  → Or if Q2 followed by Q2b, context shows recovery: score 7
  → Decides if harder/easier based on trend

Q4, Q5, Q6... (Keep only last 3):
  context = [
    { Q4 data },
    { Q5 data },
    { Q6 data }
  ]
  
  (Q1, Q2, Q3 data no longer in context)

This keeps memory bounded and focuses on recent performance.
"""


# ============================================================================
# TROUBLESHOOTING
# ============================================================================

"""
Problem: POST /answer returns 409 Conflict
──────────────────────────────────────────
Check: GET /sessions/{session_id}
  If state != LISTENING:
    → FSM may have moved to next state
    → Try again after waiting
    → Check WebSocket events to see state changes

Problem: Answer always gets score = 5
──────────────────────────────────────
Likely: LLM API failed
  Check: GROQ_API_KEY is set and valid
  Check: Network can reach api.groq.com
  Check: Groq API quota not exceeded
  Workaround: It will retry up to 3 times

Problem: WebSocket not receiving events
────────────────────────────────────────
Check: Connection string is ws:// (not http://)
  Check: session_id is correct
  Check: Browser console for network errors
  Try: Reconnect WebSocket

Problem: Session stuck in LISTENING
───────────────────────────────────
Check: answer_timeout_seconds passed (default 30s)
         → Session will transition to WRAPPING
  Check: FSM task still running (should be)
  Check: answer_events entry exists for session

Problem: Question is repeated
─────────────────────────────
Check: Is it a follow-up to same question?
         → This is expected (different phrasing)
  Check: Context history being passed to LLM
         → Check logs for question_generated event
  Check: If truly duplicate, LLM response not validated

Problem: Database connection pool exhausted
──────────────────────────────────────────
Check: max_database_connections setting
  Increase: pool_size / pool_recycle in database.py
  Check: Stale connections being kept open
  Restart: Process to reconnect
"""


# ============================================================================
# ENVIRONMENT VARIABLES
# ============================================================================

"""
Required:
  GROQ_API_KEY           # Your Groq AI API key
  DATABASE_URL           # PostgreSQL connection string
                         # Format: postgresql+asyncpg://user:pass@host/db

Optional:
  GROQ_MODEL             # LLM model (default: llama-3.1-8b-instant)
  LOG_LEVEL              # Logging level (default: INFO)
  ENVIRONMENT            # "development" or "production"
  DEBUG                  # True/False (default: False)

Example .env:
  GROQ_API_KEY=gsk_your_api_key_here
  DATABASE_URL=postgresql+asyncpg://postgres:password@localhost/interview_db
  LOG_LEVEL=INFO
  ENVIRONMENT=development
  DEBUG=False
"""


# ============================================================================
# COMMON CONFIGS
# ============================================================================

"""
Quick Session Config (5-10 min interview):
  {
    "max_questions": 3,
    "max_duration_minutes": 10,
    "answer_timeout_seconds": 60,
    "followup_score_max": 4,
    "next_score_min": 8
  }

Standard Config (30-45 min interview):
  {
    "max_questions": 10,
    "max_duration_minutes": 45,
    "answer_timeout_seconds": 300,
    "followup_score_max": 4,
    "next_score_min": 8,
    "intro_delay_seconds": 1,
    "question_delivery_delay_seconds": 1
  }

Comprehensive Config (60 min interview):
  {
    "max_questions": 15,
    "max_duration_minutes": 60,
    "answer_timeout_seconds": 300,
    "followup_score_max": 3,
    "next_score_min": 7,
    "intro_delay_seconds": 2,
    "question_delivery_delay_seconds": 1,
    "force_followup_test": false
  }

Testing Config (fast feedback):
  {
    "max_questions": 3,
    "max_duration_minutes": 5,
    "answer_timeout_seconds": 10,
    "followup_score_max": 5,
    "next_score_min": 6,
    "intro_delay_seconds": 0,
    "question_delivery_delay_seconds": 0,
    "force_followup_test": true  ← Always trigger follow-ups
  }
"""
