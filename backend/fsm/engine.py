"""Session Engine - Orchestrates the interview FSM and manages session lifecycle."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionFactory
from fsm.transitions import TERMINAL_STATES, RecruiterCommand, SessionState, can_transition
from fsm.websocket_hub import WebSocketHub
from models import InterviewSession, SessionEvent
from schemas import (
    SessionAnswerResponse,
    SessionCommandResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionEventResponse,
    SessionStatusResponse,
)
from services.evaluation_service import EvaluationService
from services.question_service import QuestionService
from services.session_context_service import SessionContextService
from utils.logger import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import AsyncSession as AsyncSessionType

logger = get_logger("fsm.session_engine")

# Type alias for session factory
AsyncSessionFactoryType = type(AsyncSessionFactory)


class SessionEngine:
    """Orchestrates interview sessions using an async finite state machine."""

    def __init__(
        self,
        session_factory: AsyncSessionFactoryType,
        websocket_hub: WebSocketHub,
        question_service: QuestionService,
        evaluation_service: EvaluationService,
    ) -> None:
        self._session_factory = session_factory
        self._websocket_hub = websocket_hub
        self._question_service = question_service
        self._evaluation_service = evaluation_service
        self._logger = get_logger("fsm.session_engine")

        # In-memory tracking for active sessions
        # session_id -> {"fsm_task": asyncio.Task, "context": SessionContextService}
        self._active_sessions: dict[str, dict[str, Any]] = {}
        # session_id -> asyncio.Event for signaling pending answer
        self._answer_events: dict[str, asyncio.Event] = {}
        # session_id -> submitted answer text
        self._pending_answers: dict[str, str] = {}

    async def create_session(
        self, payload: SessionCreateRequest
    ) -> SessionCreateResponse:
        """Create a new interview session and start FSM loop."""
        session_id = str(uuid.uuid4())

        try:
            async with self._session_factory() as session:
                # Create InterviewSession record
                interview_session = InterviewSession(
                    id=session_id,
                    candidate_id=payload.candidate_id,
                    job_id=payload.job_id,
                    meeting_url=payload.meeting_url,
                    meeting_type=payload.meeting_type,
                    schedule_time=payload.schedule_time,
                    state=SessionState.WAITING.value,
                    status=SessionState.WAITING.value,
                    config=payload.config.model_dump(),
                    topics=payload.config.topics,
                    language=payload.config.language,
                    avatar_persona=payload.config.avatar_persona,
                    force_followup_test=payload.config.force_followup_test,
                    is_running=True,
                )
                session.add(interview_session)
                await session.commit()

            self._logger.info(
                json.dumps(
                    {
                        "event": "session_created",
                        "session_id": session_id,
                        "candidate_id": payload.candidate_id,
                        "job_id": payload.job_id,
                    }
                )
            )

            # Initialize context and start FSM loop in background
            context_service = SessionContextService()
            fsm_task = asyncio.create_task(
                self._run_fsm_loop(session_id, payload)
            )
            self._active_sessions[session_id] = {
                "fsm_task": fsm_task,
                "context": context_service,
            }
            self._answer_events[session_id] = asyncio.Event()

            return SessionCreateResponse(
                session_id=session_id,
                status=SessionState.WAITING.value,
                join_url=payload.meeting_url,
            )

        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "session_creation_failed",
                        "error": str(exc),
                        "candidate_id": payload.candidate_id,
                    }
                )
            )
            raise

    async def list_sessions(self) -> list[SessionStatusResponse]:
        """List all sessions."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(select(InterviewSession))
                sessions = result.scalars().all()
                return [
                    SessionStatusResponse(
                        session_id=s.id,
                        state=s.state,
                        current_question_number=s.current_question_number,
                        duration_seconds=s.duration_seconds,
                        max_questions=s.max_questions,
                        max_duration_minutes=s.max_duration_minutes,
                        ended_reason=s.ended_reason,
                    )
                    for s in sessions
                ]
        except Exception as exc:
            self._logger.error(
                json.dumps({"event": "session_list_failed", "error": str(exc)})
            )
            raise

    async def get_status(self, session_id: str) -> SessionStatusResponse:
        """Get session status."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(InterviewSession).where(InterviewSession.id == session_id)
                )
                interview_session = result.scalar_one_or_none()
                if not interview_session:
                    raise ValueError(f"Session {session_id} not found")

                return SessionStatusResponse(
                    session_id=interview_session.id,
                    state=interview_session.state,
                    current_question_number=interview_session.current_question_number,
                    duration_seconds=interview_session.duration_seconds,
                    max_questions=interview_session.max_questions,
                    max_duration_minutes=interview_session.max_duration_minutes,
                    ended_reason=interview_session.ended_reason,
                )
        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "session_status_failed",
                        "session_id": session_id,
                        "error": str(exc),
                    }
                )
            )
            raise

    async def apply_command(
        self, session_id: str, command: RecruiterCommand
    ) -> SessionCommandResponse:
        """Apply a recruiter command to a session."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(InterviewSession).where(InterviewSession.id == session_id)
                )
                interview_session = result.scalar_one_or_none()
                if not interview_session:
                    raise ValueError(f"Session {session_id} not found")

                # TODO: Implement command handling (pause, resume, skip, etc.)
                # For now, just return current state
                self._logger.info(
                    json.dumps(
                        {
                            "event": "command_received",
                            "session_id": session_id,
                            "command": command.value,
                            "state": interview_session.state,
                        }
                    )
                )

                return SessionCommandResponse(
                    session_id=session_id,
                    command=command.value,
                    state=interview_session.state,
                    max_duration_minutes=interview_session.max_duration_minutes,
                )

        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "command_failed",
                        "session_id": session_id,
                        "command": command.value,
                        "error": str(exc),
                    }
                )
            )
            raise

    async def submit_answer(
        self, session_id: str, answer: str
    ) -> SessionAnswerResponse:
        """Submit candidate answer. Only valid in LISTENING state.
        
        This endpoint:
        1. Validates session is in LISTENING state
        2. Stores the answer
        3. Signals the FSM to resume (move to EVALUATING)
        4. Waits for evaluation
        5. Returns score, feedback, and next state
        """
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(InterviewSession).where(InterviewSession.id == session_id)
                )
                interview_session = result.scalar_one_or_none()
                if not interview_session:
                    raise ValueError(f"Session {session_id} not found")

                # Only accept answers in LISTENING state
                if interview_session.state != SessionState.LISTENING.value:
                    raise ValueError(
                        f"Cannot submit answer in state {interview_session.state}. "
                        f"Session must be in LISTENING state."
                    )

                # Store the answer and signal FSM to resume
                self._pending_answers[session_id] = answer
                event = self._answer_events.get(session_id)
                if event:
                    event.set()

                # Log answer received
                self._logger.info(
                    json.dumps(
                        {
                            "event": "answer_received",
                            "session_id": session_id,
                            "question_number": interview_session.current_question_number,
                            "answer_length": len(answer),
                        }
                    )
                )

                # Wait a bit for evaluation to complete
                await asyncio.sleep(1)

                # Re-fetch to get updated state and evaluation
                await session.refresh(interview_session)

                # Get context for response
                context_service = self._active_sessions.get(session_id, {}).get(
                    "context"
                )
                history = context_service.get_history() if context_service else []
                last_item = history[-1] if history else None

                return SessionAnswerResponse(
                    question=interview_session.current_question_text or "",
                    answer=answer,
                    score=last_item["score"] if last_item else 5,
                    feedback=last_item["feedback"] if last_item else "Evaluating...",
                    next_state=interview_session.state,
                )

        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "answer_submission_failed",
                        "session_id": session_id,
                        "error": str(exc),
                    }
                )
            )
            raise

    async def handle_candidate_disconnected(
        self, session_id: str
    ) -> SessionEventResponse:
        """Handle candidate disconnection."""
        self._logger.info(
            json.dumps(
                {
                    "event": "candidate_disconnected",
                    "session_id": session_id,
                }
            )
        )
        # TODO: Implement reconnection window
        return SessionEventResponse(
            session_id=session_id,
            status="candidate_disconnected",
        )

    async def handle_candidate_reconnected(
        self, session_id: str
    ) -> SessionEventResponse:
        """Handle candidate reconnection."""
        self._logger.info(
            json.dumps(
                {
                    "event": "candidate_reconnected",
                    "session_id": session_id,
                }
            )
        )
        return SessionEventResponse(
            session_id=session_id,
            status="candidate_reconnected",
        )

    async def handle_live_connection(
        self, websocket: WebSocket, session_id: str
    ) -> None:
        """Handle WebSocket connection for live session updates."""
        await self._websocket_hub.connect(session_id, websocket)
        try:
            while True:
                # Keep connection alive and listen for any incoming messages
                await websocket.receive_text()
        except WebSocketDisconnect:
            await self._websocket_hub.disconnect(session_id, websocket)
        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "websocket_error",
                        "session_id": session_id,
                        "error": str(exc),
                    }
                )
            )
            await self._websocket_hub.disconnect(session_id, websocket)

    async def shutdown(self) -> None:
        """Gracefully shut down all active sessions."""
        self._logger.info(
            json.dumps(
                {
                    "event": "session_engine_shutdown",
                    "active_sessions": len(self._active_sessions),
                }
            )
        )
        for session_id, session_info in list(self._active_sessions.items()):
            fsm_task = session_info.get("fsm_task")
            if fsm_task:
                fsm_task.cancel()

    # ===== Private FSM Loop =====

    async def _run_fsm_loop(
        self, session_id: str, payload: SessionCreateRequest
    ) -> None:
        """Main FSM loop - orchestrates state transitions and actions."""
        try:
            # Wait for FSM start delay
            await asyncio.sleep(payload.config.fsm_start_delay_seconds)

            # Initialize session state
            current_state = SessionState.WAITING
            question_number = 0
            started_at = datetime.now(timezone.utc)

            while current_state not in TERMINAL_STATES:
                # Execute state-specific actions
                try:
                    current_state = await self._execute_state(
                        session_id,
                        current_state,
                        payload,
                        question_number,
                    )
                    question_number += 1

                except Exception as exc:
                    self._logger.error(
                        json.dumps(
                            {
                                "event": "state_execution_error",
                                "session_id": session_id,
                                "state": current_state.value,
                                "error": str(exc),
                            }
                        )
                    )
                    current_state = SessionState.ERROR

            # Session ended or errored
            ended_at = datetime.now(timezone.utc)
            duration_seconds = int((ended_at - started_at).total_seconds())

            await self._finalize_session(
                session_id, current_state, duration_seconds
            )

        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "fsm_loop_failed",
                        "session_id": session_id,
                        "error": str(exc),
                    }
                )
            )
            await self._finalize_session(
                session_id, SessionState.ERROR, 0, f"FSM loop error: {str(exc)}"
            )
        finally:
            # Cleanup
            if session_id in self._active_sessions:
                del self._active_sessions[session_id]
            if session_id in self._answer_events:
                del self._answer_events[session_id]
            if session_id in self._pending_answers:
                del self._pending_answers[session_id]

    async def _execute_state(
        self,
        session_id: str,
        current_state: SessionState,
        payload: SessionCreateRequest,
        question_number: int,
    ) -> SessionState:
        """Execute actions for current state and return next state."""

        if current_state == SessionState.WAITING:
            return await self._state_waiting(session_id, payload)

        elif current_state == SessionState.INTRO:
            return await self._state_intro(session_id, payload)

        elif current_state == SessionState.ASKING:
            return await self._state_asking(session_id, payload, question_number)

        elif current_state == SessionState.LISTENING:
            return await self._state_listening(session_id, payload)

        elif current_state == SessionState.EVALUATING:
            return await self._state_evaluating(session_id, payload, question_number)

        elif current_state == SessionState.DECISION:
            return await self._state_decision(session_id, payload, question_number)

        elif current_state == SessionState.FOLLOWUP:
            return await self._state_followup(session_id, payload)

        elif current_state == SessionState.WRAPPING:
            return await self._state_wrapping(session_id, payload)

        else:
            return SessionState.ERROR

    async def _state_waiting(
        self, session_id: str, payload: SessionCreateRequest
    ) -> SessionState:
        """WAITING state: Prepare for interview."""
        self._logger.info(
            json.dumps(
                {
                    "event": "state_transition",
                    "session_id": session_id,
                    "from_state": SessionState.WAITING.value,
                    "to_state": SessionState.INTRO.value,
                }
            )
        )

        await self._update_session_state(
            session_id, SessionState.WAITING, SessionState.INTRO, 0
        )
        await self._websocket_hub.send_state_changed(
            session_id, SessionState.WAITING.value, SessionState.INTRO.value
        )

        # Add small delay before intro
        await asyncio.sleep(payload.config.intro_delay_seconds)

        return SessionState.INTRO

    async def _state_intro(
        self, session_id: str, payload: SessionCreateRequest
    ) -> SessionState:
        """INTRO state: Generate and deliver greeting."""
        # Generate greeting
        greeting = await self._question_service.generate_greeting(
            candidate_id=payload.candidate_id,
            job_id=payload.job_id,
        )

        # Store greeting in session
        async with self._session_factory() as session:
            result = await session.execute(
                select(InterviewSession).where(InterviewSession.id == session_id)
            )
            interview_session = result.scalar_one_or_none()
            if interview_session:
                interview_session.greeting_text = greeting
                await session.commit()

        self._logger.info(
            json.dumps(
                {
                    "event": "greeting_delivered",
                    "session_id": session_id,
                    "greeting": greeting[:100],
                }
            )
        )

        # Notify clients of new greeting
        await self._websocket_hub.broadcast(
            session_id,
            {
                "event": "greeting_delivered",
                "greeting": greeting,
            },
        )

        # Small delay then transition to ASKING
        await asyncio.sleep(payload.config.intro_delay_seconds)

        await self._update_session_state(
            session_id, SessionState.INTRO, SessionState.ASKING, 0
        )
        await self._websocket_hub.send_state_changed(
            session_id, SessionState.INTRO.value, SessionState.ASKING.value
        )

        return SessionState.ASKING

    async def _state_asking(
        self, session_id: str, payload: SessionCreateRequest, question_number: int
    ) -> SessionState:
        """ASKING state: Generate and deliver question."""
        q_num = question_number + 1

        # Get context from history
        context_service = self._active_sessions.get(session_id, {}).get("context")
        context = context_service.get_history() if context_service else []

        # Generate question
        question = await self._question_service.generate_question(
            job_id=payload.job_id,
            question_number=q_num,
            context=context,
        )

        # Store question in session
        async with self._session_factory() as session:
            result = await session.execute(
                select(InterviewSession).where(InterviewSession.id == session_id)
            )
            interview_session = result.scalar_one_or_none()
            if interview_session:
                interview_session.current_question_number = q_num
                interview_session.current_question_text = question
                await session.commit()

        self._logger.info(
            json.dumps(
                {
                    "event": "question_delivered",
                    "session_id": session_id,
                    "question_number": q_num,
                    "question": question[:100],
                }
            )
        )

        # Notify clients of new question
        await self._websocket_hub.send_question_delivered(
            session_id, question, q_num
        )

        # Small delay then transition to LISTENING
        await asyncio.sleep(payload.config.question_delivery_delay_seconds)

        await self._update_session_state(
            session_id, SessionState.ASKING, SessionState.LISTENING, q_num
        )
        await self._websocket_hub.send_state_changed(
            session_id, SessionState.ASKING.value, SessionState.LISTENING.value, q_num
        )

        return SessionState.LISTENING

    async def _state_listening(
        self, session_id: str, payload: SessionCreateRequest
    ) -> SessionState:
        """LISTENING state: Wait for candidate answer (non-blocking).
        
        This state DOES NOT transition automatically.
        It waits for the /answer endpoint to be called.
        """
        # Wait for answer signal or timeout
        event = self._answer_events.get(session_id)
        if not event:
            # Should not happen, but handle gracefully
            self._logger.warning(
                json.dumps(
                    {
                        "event": "listening_state_no_event",
                        "session_id": session_id,
                    }
                )
            )
            return SessionState.ERROR

        # Clear the event from previous use
        event.clear()

        # Wait for answer submission (with timeout)
        try:
            await asyncio.wait_for(
                event.wait(),
                timeout=payload.config.answer_timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._logger.warning(
                json.dumps(
                    {
                        "event": "listening_timeout",
                        "session_id": session_id,
                        "timeout_seconds": payload.config.answer_timeout_seconds,
                    }
                )
            )
            # For now, treat timeout as session end
            # TODO: Implement configurable timeout behavior
            return SessionState.WRAPPING

        # Answer was submitted, retrieve it
        answer = self._pending_answers.pop(session_id, "")

        if not answer:
            self._logger.error(
                json.dumps(
                    {
                        "event": "listening_no_answer_found",
                        "session_id": session_id,
                    }
                )
            )
            return SessionState.ERROR

        # Store answer temporarily for EVALUATING state
        self._pending_answers[session_id] = answer

        await self._update_session_state(
            session_id, SessionState.LISTENING, SessionState.EVALUATING
        )
        await self._websocket_hub.send_state_changed(
            session_id, SessionState.LISTENING.value, SessionState.EVALUATING.value
        )

        return SessionState.EVALUATING

    async def _state_evaluating(
        self, session_id: str, payload: SessionCreateRequest, question_number: int
    ) -> SessionState:
        """EVALUATING state: Evaluate answer and store score."""
        # Retrieve stored answer
        answer = self._pending_answers.pop(session_id, "")

        async with self._session_factory() as session:
            result = await session.execute(
                select(InterviewSession).where(InterviewSession.id == session_id)
            )
            interview_session = result.scalar_one_or_none()
            if not interview_session:
                return SessionState.ERROR

            question = interview_session.current_question_text or ""

            # Get context for evaluation
            context_service = self._active_sessions.get(session_id, {}).get("context")
            context = context_service.get_history() if context_service else []

            # Evaluate answer
            evaluation = await self._evaluation_service.evaluate_answer(
                question=question,
                answer=answer,
                context=context,
            )

            # Store in context
            if context_service:
                context_service.add_qa_pair(
                    question=question,
                    answer=answer,
                    score=evaluation.score,
                    feedback=evaluation.feedback,
                    question_number=question_number + 1,
                )

            self._logger.info(
                json.dumps(
                    {
                        "event": "answer_evaluated",
                        "session_id": session_id,
                        "question_number": question_number + 1,
                        "score": evaluation.score,
                        "feedback": evaluation.feedback,
                    }
                )
            )

            # Notify clients of evaluation
            await self._websocket_hub.send_answer_evaluated(
                session_id,
                evaluation.score,
                evaluation.feedback,
                SessionState.DECISION.value,
            )

        await self._update_session_state(
            session_id, SessionState.EVALUATING, SessionState.DECISION
        )

        return SessionState.DECISION

    async def _state_decision(
        self, session_id: str, payload: SessionCreateRequest, question_number: int
    ) -> SessionState:
        """DECISION state: Decide next action based on score."""
        from fsm.decision import decide_next_action

        async with self._session_factory() as session:
            result = await session.execute(
                select(InterviewSession).where(InterviewSession.id == session_id)
            )
            interview_session = result.scalar_one_or_none()
            if not interview_session:
                return SessionState.ERROR

            # Get last score from context
            context_service = self._active_sessions.get(session_id, {}).get("context")
            history = context_service.get_history() if context_service else []
            last_score = history[-1].get("score", 5) if history else 5

            # Decide next action
            decision = decide_next_action(
                score=last_score,
                question_number=question_number + 1,
                config=interview_session.config,
            )

            self._logger.info(
                json.dumps(
                    {
                        "event": "decision_made",
                        "session_id": session_id,
                        "score": last_score,
                        "question_number": question_number + 1,
                        "decision": decision,
                    }
                )
            )

            # Route based on decision
            if decision == "FOLLOWUP":
                return SessionState.FOLLOWUP
            elif decision == "WRAPPING":
                return SessionState.WRAPPING
            else:  # NEXT, HARDER
                return SessionState.ASKING

    async def _state_followup(
        self, session_id: str, payload: SessionCreateRequest
    ) -> SessionState:
        """FOLLOWUP state: Generate and deliver follow-up question."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(InterviewSession).where(InterviewSession.id == session_id)
            )
            interview_session = result.scalar_one_or_none()
            if not interview_session:
                return SessionState.ERROR

            question = interview_session.current_question_text or ""

            # Get context
            context_service = self._active_sessions.get(session_id, {}).get("context")
            history = context_service.get_history() if context_service else []
            last_item = history[-1] if history else {}
            answer = last_item.get("answer", "")
            feedback = last_item.get("feedback", "")

            # Generate followup
            followup_question = await self._question_service.generate_followup(
                original_question=question,
                candidate_answer=answer,
                evaluation_feedback=feedback,
                context=history,
            )

            # Store followup as next question
            interview_session.current_question_text = followup_question
            await session.commit()

            self._logger.info(
                json.dumps(
                    {
                        "event": "followup_generated",
                        "session_id": session_id,
                        "followup_question": followup_question[:100],
                    }
                )
            )

            # Proceed to ASKING with followup question
            await self._update_session_state(
                session_id,
                SessionState.FOLLOWUP,
                SessionState.ASKING,
            )

        await asyncio.sleep(payload.config.question_delivery_delay_seconds)

        return SessionState.ASKING

    async def _state_wrapping(
        self, session_id: str, payload: SessionCreateRequest
    ) -> SessionState:
        """WRAPPING state: Generate closing and end session."""
        closing = (
            "Thank you for your time today. We've covered a lot of ground. "
            "You'll hear back from us within 2-3 business days. Best of luck!"
        )

        self._logger.info(
            json.dumps(
                {
                    "event": "interview_closing",
                    "session_id": session_id,
                    "closing": closing,
                }
            )
        )

        await self._websocket_hub.send_session_ended(session_id, "completed")

        await self._update_session_state(
            session_id, SessionState.WRAPPING, SessionState.ENDED
        )

        return SessionState.ENDED

    # ===== Persistence Helpers =====

    async def _update_session_state(
        self,
        session_id: str,
        old_state: SessionState,
        new_state: SessionState,
        question_number: int | None = None,
    ) -> None:
        """Update session state in database."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(InterviewSession).where(InterviewSession.id == session_id)
                )
                interview_session = result.scalar_one_or_none()
                if interview_session:
                    interview_session.state = new_state.value
                    interview_session.status = new_state.value
                    if question_number is not None:
                        interview_session.current_question_number = question_number
                    await session.commit()
        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "state_update_failed",
                        "session_id": session_id,
                        "from_state": old_state.value,
                        "to_state": new_state.value,
                        "error": str(exc),
                    }
                )
            )

    async def _finalize_session(
        self,
        session_id: str,
        final_state: SessionState,
        duration_seconds: int,
        error_reason: str | None = None,
    ) -> None:
        """Finalize session after completion."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(InterviewSession).where(InterviewSession.id == session_id)
                )
                interview_session = result.scalar_one_or_none()
                if interview_session:
                    interview_session.state = final_state.value
                    interview_session.status = final_state.value
                    interview_session.duration_seconds = duration_seconds
                    interview_session.is_running = False
                    interview_session.ended_at = datetime.now(timezone.utc)
                    if error_reason:
                        interview_session.error_reason = error_reason
                    if final_state == SessionState.ENDED:
                        interview_session.ended_reason = "completed"
                    await session.commit()

            self._logger.info(
                json.dumps(
                    {
                        "event": "session_finalized",
                        "session_id": session_id,
                        "final_state": final_state.value,
                        "duration_seconds": duration_seconds,
                        "error_reason": error_reason,
                    }
                )
            )

            # Notify clients
            if final_state == SessionState.ERROR:
                await self._websocket_hub.send_session_error(
                    session_id, error_reason or "Unknown error"
                )
            else:
                await self._websocket_hub.send_session_ended(
                    session_id, "completed"
                )

        except Exception as exc:
            self._logger.error(
                json.dumps(
                    {
                        "event": "session_finalization_failed",
                        "session_id": session_id,
                        "error": str(exc),
                    }
                )
            )
