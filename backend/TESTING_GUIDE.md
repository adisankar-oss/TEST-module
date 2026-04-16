"""
QUICK START: Testing the Upgraded Interview System

This guide shows how to test each component of the new real-time interview system.
"""

# ============================================================================
# TEST 1: Create a Session and Watch FSM Progression
# ============================================================================

"""
GOAL: Verify session creation and automatic FSM state transitions

STEPS:

1. Start the backend:
   $ cd backend
   $ python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

2. Create a session (curl or Postman):
   POST http://localhost:8000/api/v1/sessions
   Content-Type: application/json
   
   {
     "candidate_id": "test_cand_001",
     "job_id": "backend_engineer",
     "meeting_url": "https://example.com/meet",
     "meeting_type": "google_meet",
     "schedule_time": "2024-02-14T15:00:00Z",
     "config": {
       "max_duration_minutes": 45,
       "max_questions": 3,
       "intro_delay_seconds": 1,
       "question_delivery_delay_seconds": 1,
       "answer_timeout_seconds": 60,
       "followup_score_max": 4,
       "next_score_min": 8
     }
   }

3. Copy session_id from response

4. Check logs for:
   ✓ session_created event
   ✓ greeting_generated event
   ✓ question_generated event (question_number: 1, track: soft_skill)

5. Query session status:
   GET http://localhost:8000/api/v1/sessions/{session_id}
   
   Should show:
   {
     "state": "LISTENING",  ← FSM paused, waiting for answer
     "current_question_number": 1,
     "status": "LISTENING"
   }

VERIFICATION:
✓ Session created and FSM running
✓ WAITING → INTRO → ASKING → LISTENING transitions occurred
✓ First question is soft-skill type
✓ System now waiting for candidate answer
"""


# ============================================================================
# TEST 2: Submit Answer and Verify Evaluation
# ============================================================================

"""
GOAL: Test the /answer endpoint and evaluation pipeline

STEPS (continue from TEST 1):

1. Submit an answer:
   POST http://localhost:8000/api/v1/sessions/{session_id}/answer
   Content-Type: application/json
   
   {
     "answer": "I have 7 years of experience building distributed systems.
               Started with Python, now expert in Go and Rust. Built several
               high-traffic services handling millions of requests per day."
   }

2. Check response:
   Expected:
   {
     "question": "Tell me about yourself...",
     "answer": "I have 7 years...",
     "score": 8,
     "feedback": "Clear professional background with concrete examples.",
     "next_state": "DECISION"  ← Post-evaluation, may transition further
   }

3. Check logs for:
   ✓ answer_received event
   ✓ answer_evaluated event (score, feedback)
   ✓ decision_made event

4. Verify next question was generated:
   GET http://localhost:8000/api/v1/sessions/{session_id}
   
   Should show:
   {
     "state": "LISTENING",  ← Ready for next answer
     "current_question_number": 2,
     "status": "LISTENING"
   }

VERIFICATION:
✓ LISTENING → EVALUATING → DECISION → ASKING → LISTENING flow
✓ Score was calculated (1-10)
✓ Feedback was generated
✓ Next question is different from first
✓ Question number incremented
"""


# ============================================================================
# TEST 3: Test WebSocket Real-Time Updates
# ============================================================================

"""
GOAL: Verify WebSocket receives live events

STEPS (continue from TEST 1):

1. Connect WebSocket in separate terminal (using websocat or wscat):
   websocat ws://localhost:8000/api/v1/sessions/{session_id}/live

2. You should immediately see events as FSM runs:

   First:
   {
     "event": "state_changed",
     "session_id": "...",
     "old_state": "INTRO",
     "new_state": "ASKING",
     "question_number": 1
   }

   Then:
   {
     "event": "question_delivered",
     "session_id": "...",
     "question": "Tell me about yourself...",
     "question_number": 1
   }

3. Submit answer (TEST 2) and watch WebSocket receive:

   {
     "event": "answer_evaluated",
     "session_id": "...",
     "score": 8,
     "feedback": "Clear...",
     "next_state": "DECISION"
   }

   Followed by:

   {
     "event": "state_changed",
     "session_id": "...",
     "old_state": "ASKING",
     "new_state": "LISTENING",
     "question_number": 2
   }

VERIFICATION:
✓ WebSocket receives real-time state changes
✓ Events contain correct data (question text, scores, etc.)
✓ Event order follows FSM progression
"""


# ============================================================================
# TEST 4: Test Adaptive Follow-Up Questions (Low Score)
# ============================================================================

"""
GOAL: Verify score <= 4 triggers follow-up question

SETUP:
- Use TEST 1 to create session
- Make sure question 1 is displayed

STEPS:

1. Submit a POOR answer (aim for score < 5):
   POST http://localhost:8000/api/v1/sessions/{session_id}/answer
   
   {
     "answer": "um... yeah it was hard"
   }

2. Observe response:
   {
     "score": 3,
     "feedback": "Answer lacks detail and specific examples.",
     "next_state": "DECISION"
   }

3. Check logs for:
   ✓ decision_made event with decision: "FOLLOWUP"
   ✓ followup_triggered event
   ✓ followup_question should NOT contain:
     - Original question text
     - Phrases like "explain more", "explain deeper"

4. Get session status:
   GET http://localhost:8000/api/v1/sessions/{session_id}
   
   Question should change (this is the follow-up):
   {
     "current_question_number": 1,  ← Still Q1, but follow-up
     "state": "LISTENING"
   }

5. Submit answer to follow-up - should be different question text

VERIFICATION:
✓ Score 3 triggered FOLLOWUP decision
✓ Follow-up question generated and delivered
✓ Follow-up question is contextual (not generic "explain more")
✓ Can continue with follow-up answer
"""


# ============================================================================
# TEST 5: Test Context Memory (Adaptive Questions)
# ============================================================================

"""
GOAL: Verify each question adapts based on previous scores and context

SETUP:
- Go through TEST 1, 2, and additional answers
- Answer Q1 with score 8 (excellent)
- Answer Q2 with score 3 (poor, triggers followup)
- Answer Q2b (followup) with score 7 (better)

EXPECTED: Q3 should be different type based on history

Steps:

1. Submit answer to Q3:
   POST /answer with any answer

2. Check logs:
   ✓ question_generated event should show:
     - question_number: 3
     - track: likely "problem_solving" (every 3rd question)
     - context includes: [ {score: 8, ...}, {score: 3, ...}, {score: 7, ...} ]
     - source: "ai" (using LLM with context)

3. Observe Q3 characteristics:
   - More technical than Q1
   - Different from Q2 topic
   - Problem-solving focus
   - Considers candidate's demonstrated skill level

VERIFICATION:
✓ Context history maintained (last 3 Q&A)
✓ Questions adapt based on scores and role
✓ No immediate repetition of earlier questions
✓ Question difficulty progresses naturally
"""


# ============================================================================
# TEST 6: Test Session Timeout (No Answer Submitted)
# ============================================================================

"""
GOAL: Verify session wrapping if candidate doesn't answer

SETUP:
- Create session (TEST 1)
- Wait in LISTENING state without submitting answer

STEPS:

1. Create session with short answer_timeout_seconds:
   {
     "config": {
       "answer_timeout_seconds": 5,  ← Very short for testing
       ...
     }
   }

2. Let it sit without answering for 6 seconds

3. Check logs:
   ✓ listening_timeout event
   ✓ state_transition to WRAPPING
   ✓ session_ended event

4. Get session status:
   {
     "state": "ENDED",
     "ended_reason": "completed"  ← Or could be "timeout"
   }

VERIFICATION:
✓ LISTENING state respects timeout
✓ Session gracefully ends without hanging
✓ No error state, ends cleanly
"""


# ============================================================================
# TEST 7: Test Max Questions Limit
# ============================================================================

"""
GOAL: Verify interview ends after max_questions reached

SETUP:
- Create session with low max_questions:
  {
    "config": {
      "max_questions": 2,  ← Only 2 questions
      ...
    }
  }

STEPS:

1. Answer Q1, then Q2

2. After Q2 evaluation, observe decision logic:
   ✓ question_number (2) >= max_questions (2)
   ✓ decision_made event shows: decision: "WRAPPING"
   ✓ state_transition to WRAPPING
   ✓ session_ended event

3. Try to submit another answer:
   POST /answer
   
   Should fail:
   - 409 error: Session not in LISTENING state
   - State is ENDED

VERIFICATION:
✓ Interview ends when max_questions reached
✓ Doesn't allow extra answers
✓ FSM transitions to WRAPPING consistently
"""


# ============================================================================
# TEST 8: Error Scenarios
# ============================================================================

"""
GOAL: Verify system resilience to errors

SCENARIO 1: Network timeout in question generation
────────────────────────────────────────────────────
- Manually kill network to Groq API
- Try to answer a question
→ Fallback question used
→ Session continues, score=5, feedback=default
→ No crash, no unhandled exception

SCENARIO 2: Invalid session ID
───────────────────────────────
- GET /sessions/invalid_session_id
→ 404 Not Found

- POST /sessions/invalid_session_id/answer
→ 404 Not Found

SCENARIO 3: Wrong session state for answer
──────────────────────────────────────────
- Session in ASKING state (hasn't transitioned to LISTENING yet)
- POST /answer
→ 409 Conflict: "Session must be in LISTENING state"

VERIFICATION:
✓ All errors return appropriate HTTP status codes
✓ Error messages are clear
✓ Session doesn't crash on errors
✓ System logs errors properly
"""


# ============================================================================
# PERFORMANCE BASELINE
# ============================================================================

"""
Expected performance (single session on dev machine):

✓ Session creation: < 100ms
✓ Question generation (via LLM): 1-3 seconds
✓ Answer evaluation (via LLM): 1-3 seconds
✓ FSM state transitions: < 100ms
✓ Question delivery to question: 3-5 seconds total
✓ Answer evaluation to next state: 2-5 seconds total
✓ WebSocket broadcast: < 50ms

For concurrent sessions (10 concurrent):
✓ All metrics remain < 2x of single session
✓ Database connection pool should size 10+
✓ No deadlocks or race conditions
✓ Clean shutdown within 10 seconds
"""


# ============================================================================
# DEBUGGING TIPS
# ============================================================================

"""
If something doesn't work:

1. Check logs for session_id you're testing:
   grep "session_id" backend.log | grep <your_session_id>

2. Verify FSM state sequencing:
   Look for state_transition events in order:
   WAITING -> INTRO -> ASKING -> LISTENING -> EVALUATING -> DECISION -> ...

3. If stuck in LISTENING:
   - Verify /answer endpoint was called
   - Check session state via GET /status
   - Check for listening_timeout in logs

4. If question doesn't change:
   - Check question_generated events have different question text
   - Verify context includes previous Q&A
   - Check if score triggered FOLLOWUP (same question, different phrasing)

5. If WebSocket not receiving events:
   - Verify WebSocket connected with correct session_id
   - Check NetworkError in browser console
   - Verify ws:// protocol (not http://)

6. If evaluation always returns score=5:
   - Groq API likely failed
   - Check GROQ_API_KEY is set
   - Check GROQ_MODEL is valid
   - Check network connectivity to api.groq.com
"""
