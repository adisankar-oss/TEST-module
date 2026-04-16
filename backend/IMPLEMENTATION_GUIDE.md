"""
IMPLEMENTATION GUIDE: AI Interview System Upgrade

This document shows all changes made to transform the system from simulated mode
into a realistic AI interviewer with real candidate interaction.
"""

# ============================================================================
# SUMMARY OF CHANGES
# ============================================================================

# NEW FILES CREATED:
# ─────────────────────────────────────────────────────────────────────────
# 1. backend/services/session_context_service.py
#    - SessionContextService: Manages conversation history (last 3 Q&A)
#    - QuestionAnswerContext: Data structure for Q&A pair
#
# 2. backend/fsm/websocket_hub.py
#    - WebSocketHub: Real-time event broadcasting to connected clients
#    - Methods: connect, disconnect, broadcast, send_state_changed, etc.
#
# 3. backend/fsm/engine.py
#    - SessionEngine: Core orchestration engine (MAIN COMPONENT)
#    - FSM Loop: Async state machine with 9 states
#    - Session Lifecycle: Create, run, finalize sessions


# MODIFIED FILES:
# ─────────────────────────────────────────────────────────────────────────
# 1. backend/main.py
#    - Fixed WebSocketHub import (moved to separate file)
#    - All endpoints already defined and connected


# ============================================================================
# API ENDPOINTS (POST-UPGRADE)
# ============================================================================

"""
1. CREATE SESSION
───────────────────────────────────────────────────────────────────────────
POST /api/v1/sessions
Content-Type: application/json

Request:
{
  "candidate_id": "cand_12345",
  "job_id": "backend_python_senior",
  "meeting_url": "https://meet.google.com/...",
  "meeting_type": "google_meet",
  "schedule_time": "2024-02-14T10:00:00Z",
  "config": {
    "max_duration_minutes": 45,
    "max_questions": 10,
    "fsm_start_delay_seconds": 1,
    "intro_timeout_seconds": 15,
    "answer_timeout_seconds": 300,
    "intro_delay_seconds": 1,
    "question_delivery_delay_seconds": 1,
    "followup_score_max": 4,
    "next_score_min": 8,
    "topics": ["technical_skills", "problem_solving", "behavioural", "culture_fit"],
    "language": "en",
    "avatar_persona": "alex",
    "force_followup_test": false
  }
}

Response:
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "WAITING",
  "join_url": "https://meet.google.com/..."
}

Behavior:
- Session created in database with state=WAITING
- FSM loop starts in background (asyncio.Task)
- SessionContextService initialized for conversation history
- WebSocket hub ready to accept connections


2. GET SESSION STATUS
───────────────────────────────────────────────────────────────────────────
GET /api/v1/sessions/{session_id}

Response:
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "state": "ASKING",
  "current_question_number": 1,
  "duration_seconds": 45,
  "max_questions": 10,
  "max_duration_minutes": 45,
  "ended_reason": null
}


3. SUBMIT CANDIDATE ANSWER (NEW KEY ENDPOINT)
───────────────────────────────────────────────────────────────────────────
POST /api/v1/sessions/{session_id}/answer
Content-Type: application/json

Request:
{
  "answer": "I have 5 years of experience building scalable APIs with Python and FastAPI.
             I've worked on distributed systems at scale and am passionate about clean code."
}

Constraints:
- Only works when session is in LISTENING state
- Answer must be 1-8000 characters
- FSM will pause in LISTENING state and NOT auto-advance

Response:
{
  "question": "Tell me about yourself and the problems you've been solving recently.",
  "answer": "I have 5 years of experience...",
  "score": 8,
  "feedback": "Clear background with relevant experience mentioned.",
  "next_state": "DECISION"
}

Behavior:
1. Request validated
2. Session state checked (must be LISTENING)
3. Answer stored in pending_answers
4. FSM resume signal sent via asyncio.Event
5. wait ~1 second for evaluation
6. Return evaluation result + next state

Error Cases:
- 400: Invalid answer (too short/long)
- 409: Session not in LISTENING state
- 404: Session not found
- 500: Evaluation service error (returns score=5, fallback feedback)


4. WEBSOCKET: LIVE SESSION UPDATES
───────────────────────────────────────────────────────────────────────────
WebSocket: ws://localhost:8000/api/v1/sessions/{session_id}/live

Connect and receive real-time events:

{
  "event": "state_changed",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "old_state": "INTRO",
  "new_state": "ASKING",
  "question_number": 1
}

{
  "event": "question_delivered",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "question": "Tell me about yourself...",
  "question_number": 1
}

{
  "event": "answer_evaluated",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "score": 8,
  "feedback": "Clear and relevant.",
  "next_state": "DECISION"
}

{
  "event": "session_ended",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "ended_reason": "completed"
}

{
  "event": "session_error",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "error_reason": "Timeout in LISTENING state"
}
"""


# ============================================================================
# FSM STATE MACHINE FLOW
# ============================================================================

"""
COMPLETE FSM FLOW (per session):

WAITING (initial state, ~1-2 sec)
  └─ FSM delay before start
  └─ Action: None
  └─ Next: INTRO

INTRO (~2-3 sec)
  └─ Action: QuestionService.generate_greeting()
  └─ Greeting stored in session.greeting_text
  └─ WebSocket broadcasts: {"event": "greeting_delivered", "greeting": "..."}
  └─ Small delay for naturalness
  └─ Next: ASKING

ASKING (variable time)
  └─ Action: QuestionService.generate_question()
  │   - First question: soft skill ("Tell me about yourself")
  │   - Later questions: mix of behavioral/technical based on scores + role
  │   - Uses context: last 3 Q&A pairs, candidate scores
  │   - Avoids repetition: checks previous questions
  │
  └─ Question stored in session.current_question_text
  └─ WebSocket broadcasts: {"event": "question_delivered", "question": "...", "question_number": N}
  └─ Small delay (~1 sec)
  └─ Next: LISTENING

LISTENING (waits for user input, up to 30-300 sec configurable)
  └─ FSM PAUSES HERE (non-blocking)
  └─ Awaits: POST /api/v1/sessions/{session_id}/answer
  └─ Timeout: If no answer for answer_timeout_seconds, transition to WRAPPING
  │   (or configurable behavior)
  │
  └─ Resume signal: When submit_answer called, asyncio.Event fires
  └─ Next: EVALUATING

EVALUATING (variable time, usually <5 sec)
  └─ Action: EvaluationService.evaluate_answer()
  │   - Scores answer 1-10 based on:
  │     * Clarity
  │     * Depth
  │     * Relevance to question
  │     * Context of previous answers
  │
  └─ Score + Feedback stored in SessionContextService
  └─ WebSocket broadcasts: {"event": "answer_evaluated", "score": N, "feedback": "..."}
  └─ Next: DECISION

DECISION (immediate)
  └─ Action: Call decision.decide_next_action()
  │   - If score <= 4: FOLLOWUP (ask deeper question about same topic)
  │   - If score >= 8: ASKING (move to next question, harder difficulty)
  │   - Otherwise (5-7): ASKING (normal next question)
  │   - If question_number >= max_questions: WRAPPING
  │
  └─ Next: FOLLOWUP | ASKING | WRAPPING

FOLLOWUP (variable time)
  └─ Triggered when: score <= 4 (config: followup_score_max)
  └─ Action: QuestionService.generate_followup()
  │   - Does NOT repeat original question
  │   - Targets specific missing depth or clarity
  │   - Banned phrases: "explain more", "explain deeper", etc.
  │   - Specific and focused follow-up
  │
  └─ Followup question stored (becomes current_question_text)
  └─ Small delay
  └─ Next: ASKING (start over with new question)

WRAPPING (end sequence)
  └─ Triggered when: question_number >= max_questions OR timeout
  └─ Action: Generate closing statement
  └─ WebSocket broadcasts: {"event": "session_ended", "ended_reason": "completed"}
  └─ Next: ENDED

ENDED (terminal state)
  └─ Session complete
  └─ FSM terminates
  └─ Cleanup: Remove from active sessions, WebSocket hub, context managers
  └─ No further events

ERROR (terminal state)
  └─ Triggered on unrecoverable error
  └─ Session error logged with reason
  └─ WebSocket broadcasts: {"event": "session_error", "error_reason": "..."}
  └─ FSM terminates
  └─ Session marked with error_reason field
"""


# ============================================================================
# KEY FEATURES IN ACTION
# ============================================================================

"""
1. REAL CANDIDATE INTERACTION (NO SIMULATION)
   ─────────────────────────────────────────────────────────────────────────
   Before:
   - System generated fake answers
   - Questions and answers were pre-scripted
   - No real feedback loop
   
   After:
   - POST /answer endpoint accepts REAL user input
   - FSM pauses in LISTENING state until input arrives
   - Each answer fed to LLM for evaluation
   - Next question adapted based on real score and content
   ✓ Human-like conversation flow


2. ADAPTIVE QUESTION GENERATION
   ─────────────────────────────────────────────────────────────────────────
   Adaptation Factors:
   - Question number (1st = soft skill, later = mixed)
   - Score history (high scores → harder, low scores → behavioral)
   - Role detection (backend/frontend/data/general)
   - Previous questions (no repetition)
   - Question track (behavioral, technical, problem-solving)
   
   Example:
   Q1: "Tell me about yourself" (soft skill, always starts here)
   A1: "8/10" (excellent) → decision: NEXT
   Q2: Technical question (score was high)
   A2: "7/10" (good) → decision: NEXT
   Q3: Problem-solving (every 3rd question)
   A3: "3/10" (poor) → decision: FOLLOWUP
   Q3b: Follow-up on problem-solving (targets missing clarity)
   ✓ Interview feels natural and progressive


3. CONTEXT MEMORY (SESSIONCONTEXTSERVICE)
   ─────────────────────────────────────────────────────────────────────────
   Maintains (per session):
   - Last 3 Q&A pairs
   - Scores for each
   - Feedback for each
   
   Used in:
   - Question generation (avoid repeats, adapt difficulty)
   - Follow-up generation (understand previous answer quality)
   - Evaluation (consider context of previous answers)
   
   Example Context Passed to LLM:
   [
     {
       "question_number": 1,
       "question": "Tell me about yourself...",
       "answer": "I have 5 years...",
       "score": 8,
       "feedback": "Clear background."
     },
     {
       "question_number": 2,
       "question": "Design an API that...",
       "answer": "I would use...",
       "score": 7,
       "feedback": "Good structure, missing error handling discussion."
     }
   ]
   ✓ System becomes "aware" of conversation thread


4. FOLLOW-UP LOGIC (SMART RE-ASKING)
   ─────────────────────────────────────────────────────────────────────────
   When Score <= 4:
   - Generate specific follow-up (not repeat)
   - Avoids: "explain more", "explain deeper", etc.
   - Targets: clarity, depth, specific examples
   
   Example:
   Original Q: "Tell me about a time you had to make a difficult decision."
   Poor A (score=3): "It was hard. I thought about it."
   Follow-up Q: "In that situation, what were the key factors that made it difficult,
                  and how did you weigh your options?"
   
   NOT: "Can you explain more about your decision?"
   ✓ Interviewer sounds professional, never patronizing


5. REAL-TIME WEBSOCKET UPDATES
   ─────────────────────────────────────────────────────────────────────────
   Client connects: ws://host/api/v1/sessions/{session_id}/live
   
   Receives events:
   - state_changed: FSM transitioned
   - question_delivered: New question for candidate
   - answer_evaluated: Score + feedback after evaluation
   - session_ended: Interview complete
   - session_error: Something failed
   
   UI can:
   - Update state display
   - Show current question to recruiter
   - Display evaluation results in real-time
   - Know when interview ended
   ✓ Live interviewer dashboard possible


6. ERROR RESILIENCE
   ─────────────────────────────────────────────────────────────────────────
   If AI service fails:
   - evaluate_answer() catches exception
   - Uses fallback: score=5, feedback="needs clarity and depth"
   - Session continues
   
   If question generation fails:
   - Uses fallback question bank (role-specific)
   - Session continues
   
   If unexpected error in any state:
   - FSM transitions to ERROR state
   - error_reason stored
   - WebSocket broadcasts error event
   ✓ System never crashes candidate experience
"""


# ============================================================================
# DATA FLOW DIAGRAM
# ============================================================================

"""
┌─ Client: UI/Bot ─────────────────────────────────────────────────────────┐
│                                                                            │
│  POST /sessions                                                            │
│         │                                                                  │
│         ├──> SessionEngine.create_session()                               │
│         │         │                                                       │
│         │         ├─ Create InterviewSession in DB                        │
│         │         ├─ Start FSM loop (asyncio.Task)                        │
│         │         ├─ Initialize SessionContextService                     │
│         │         └─ Return session_id + greeting URL                     │
│         │                                                                  │
│  ws:// /live  (establish WebSocket)                                       │
│         │                                                                  │
│         ├──> WebSocketHub.connect(session_id, websocket)                  │
│         │         └─ Register connection for broadcasts                   │
│         │                                                                  │
│  ─────────────────────────────────────────────────────────────────        │
│                FSM LOOP (in background)                                   │
│  ─────────────────────────────────────────────────────────────────        │
│         │                                                                  │
│         ├─ WAITING → INTRO                                                │
│         │   └─ QuestionService.generate_greeting()                        │
│         │   └─ Broadcast via WebSocket                                    │
│         │                                                                  │
│         ├─ INTRO → ASKING                                                 │
│         │   └─ QuestionService.generate_question(context)                 │
│         │   └─ Broadcast via WebSocket                                    │
│         │                                                                  │
│         ├─ ASKING → LISTENING                                             │
│         │   └─ FSM PAUSES HERE                                            │
│         │   └─ [Client shows question, waits for input]                   │
│         │                                                                  │
│  POST /answer (when ready)                                                │
│         │                                                                  │
│         ├──> SessionEngine.submit_answer(session_id, answer)              │
│         │         │                                                       │
│         │         ├─ Validate: session in LISTENING state                 │
│         │         ├─ Store answer in pending_answers                      │
│         │         ├─ Signal FSM via asyncio.Event                         │
│         │         └─ Return immediately (score pending)                   │
│         │                                                                  │
│         └─ FSM RESUMES                                                    │
│            │                                                              │
│            ├─ LISTENING → EVALUATING                                      │
│            │   └─ EvaluationService.evaluate_answer(question, answer)     │
│            │   └─ Add to SessionContextService history                    │
│            │   └─ Broadcast score + feedback via WebSocket                │
│            │                                                              │
│            ├─ EVALUATING → DECISION                                       │
│            │   └─ Call decide_next_action(score)                          │
│            │                                                              │
│            ├─ DECISION → FOLLOWUP | ASKING | WRAPPING                    │
│            │                                                              │
│            ├─ [LOOP back to ASKING if not ended]                          │
│            │   Context now includes previous Q&A                          │
│            │                                                              │
│            ├─ WRAPPING → ENDED                                            │
│            │   └─ Broadcast session_ended via WebSocket                   │
│            │   └─ Cleanup active_sessions, context, event                 │
│            │                                                              │
│  GET /status  (anytime)                                                   │
│         │                                                                  │
│         └──> SessionEngine.get_status(session_id)                         │
│              └─ Return current state, question#, duration                  │
│                                                                            │
└───────────────────────────────────────────────────────────────────────────┘
"""


# ============================================================================
# DEPLOYMENT CHECKLIST
# ============================================================================

"""
Before deploying to production:

Pre-Launch:
  ☐ All imports verified (check for circular dependencies)
  ☐ Async/await patterns reviewed for race conditions
  ☐ Database schema migrations run
  ☐ GROQ API key set in environment
  ☐ WebSocket support enabled in production (nginx/supervisor config)
  ☐ Logging level configured appropriately
  ☐ Error boundaries tested (what if LLM fails?)

Testing:
  ☐ Single session create + full FSM loop
  ☐ Multiple concurrent sessions
  ☐ Answer submission at each question
  ☐ WebSocket broadcast delivery
  ☐ Timeout handling (no answer in LISTENING)
  ☐ Low score → follow-up logic
  ☐ High score → next question with adapted difficulty
  ☐ max_questions limit enforcement
  ☐ max_duration_minutes tracking
  ☐ Graceful shutdown (cancel all FSM tasks)

Monitoring:
  ☐ Session error rate < 1%
  ☐ Average evaluation latency < 5 sec
  ☐ WebSocket connection stability
  ☐ Database connection pool utilization
  ☐ GROQ API quota monitoring

Optional Enhancements (not blocking):
  ☐ Implement recruiter commands (PAUSE, RESUME, SKIP_QUESTION, etc.)
  ☐ Implement candidate disconnect/reconnect with resume
  ☐ Add question pre-generation for performance
  ☐ Implement audio transcription pipeline (M2 integration)
  ☐ Add performance metrics/dashboard
  ☐ Add interview recording
  ☐ Add candidate feedback collection
  ☐ Implement admin interview cancel/override
"""
