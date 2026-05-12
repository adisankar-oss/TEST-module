
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.fsm.decision import (
    CONFIG_KEY_FOLLOWUP_SCORE_MAX,
    CONFIG_KEY_MAX_QUESTIONS,
    CONFIG_KEY_NEXT_SCORE_MIN,
    Decision,
    decide_next_action,
)
from backend.fsm.transitions import RecruiterCommand, SessionState, TERMINAL_STATES, validate_transition
from backend.models import InterviewSession, SessionEvent
from backend.schemas import (
    LiveEventEnvelope,
    SessionAnswerResponse,
    SessionCommandResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionEventResponse,
    SessionStatusResponse,
)
from backend.services.candidate_model import CandidateModel
from backend.services.evaluation_service import EvaluationResult, EvaluationService
from backend.services.followups.followup_engine import FollowUpEngine
from backend.services.question_service import QuestionService
from backend.services.fsm.followup_resolution import (
    FollowUpResolutionEngine,
    FollowUpResolutionState,
)

try:
    from backend.utils.logger import get_logger as _get_logger
except Exception:
    _get_logger = None


def get_logger(name: str) -> logging.Logger:
    if _get_logger is not None:
        try:
            return _get_logger(name)
        except Exception:
            pass
    return logging.getLogger(name)



def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SessionRuntime:
    session_id: str
    queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    paused: bool = False
    force_wrap: bool = False
    skip_question: bool = False
    forced_followup_used: bool = False
    pending_question: str | None = None
    pending_question_meta: dict[str, Any] | None = None
    pending_topic: str | None = None
    next_difficulty: str = "normal"
    interaction_history: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=3))
    listening_active: bool = False
    _pending_events: deque[str] = field(default_factory=deque)
    _scheduled_handles: set[asyncio.Handle] = field(default_factory=set)
    _command_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _candidate_answer: str | None = None
    _answer_future: asyncio.Future[SessionAnswerResponse] | None = None

    CONTROL_EVENTS = {
        "pause",
        "resume",
        "candidate_left",
        "candidate_rejoined",
        "skip_question",
        "end_interview",
        "candidate_answer",
    }

    def emit(self, event: str) -> None:
        self.queue.put_nowait(event)

    async def pause(self) -> None:
        async with self._command_lock:
            if self.paused:
                raise HTTPException(status_code=409, detail="Session is already paused")
            self.paused = True
            self.emit("pause")

    async def resume(self) -> None:
        async with self._command_lock:
            if not self.paused:
                raise HTTPException(status_code=409, detail="Session is not paused")
            self.paused = False
            self.emit("resume")

    async def skip_current_question(self) -> None:
        async with self._command_lock:
            self.skip_question = True
            self.emit("skip_question")

    async def end_interview(self) -> None:
        async with self._command_lock:
            self.force_wrap = True
            self.emit("end_interview")

    async def mark_candidate_left(self) -> None:
        async with self._command_lock:
            self.paused = True
            self.emit("candidate_left")

    async def mark_candidate_rejoined(self) -> None:
        async with self._command_lock:
            if not self.paused:
                raise HTTPException(status_code=409, detail="Session is not paused")
            self.paused = False
            self.emit("candidate_rejoined")

    async def mark_listening(self) -> None:
        async with self._command_lock:
            self.listening_active = True

    async def submit_answer(self, answer: str) -> asyncio.Future[SessionAnswerResponse]:
        async with self._command_lock:
            if self.paused:
                raise HTTPException(status_code=409, detail="Session is paused")
            if not self.listening_active:
                raise HTTPException(status_code=409, detail="Session is not waiting for an answer")
            if self._candidate_answer is not None:
                raise HTTPException(status_code=409, detail="An answer is already being processed")

            loop = asyncio.get_running_loop()
            self._candidate_answer = answer
            self._answer_future = loop.create_future()
            self.emit("candidate_answer")
            return self._answer_future

    async def consume_answer_submission(
        self,
    ) -> tuple[str | None, asyncio.Future[SessionAnswerResponse] | None]:
        async with self._command_lock:
            answer = self._candidate_answer
            future = self._answer_future
            self._candidate_answer = None
            self._answer_future = None
            self.listening_active = False
            return answer, future

    async def clear_listening(self) -> None:
        async with self._command_lock:
            self.listening_active = False
            self._candidate_answer = None
            self._answer_future = None

    def resolve_answer_future(
        self,
        future: asyncio.Future[SessionAnswerResponse] | None,
        response: SessionAnswerResponse,
    ) -> None:
        if future is not None and not future.done():
            future.set_result(response)

    def schedule_event(self, event: str, delay: float) -> None:
        loop = asyncio.get_running_loop()

        def _dispatch() -> None:
            self._scheduled_handles.discard(handle_ref)
            self.emit(event)

        handle_ref = loop.call_later(delay, _dispatch)
        self._scheduled_handles.add(handle_ref)

    def cancel_scheduled(self) -> None:
        for handle in list(self._scheduled_handles):
            handle.cancel()
        self._scheduled_handles.clear()

    async def wait_for_event(self, event: str, timeout: int) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while True:
            next_event = self._pop_relevant_pending_event(event)
            if next_event is None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError(f"Timed out while waiting for event '{event}'")
                next_event = await asyncio.wait_for(self.queue.get(), timeout=remaining)

            if next_event in {"pause", "candidate_left"}:
                async with self._command_lock:
                    self.paused = True
                continue

            if next_event in {"resume", "candidate_rejoined"}:
                async with self._command_lock:
                    self.paused = False
                continue

            if next_event == "end_interview":
                async with self._command_lock:
                    self.force_wrap = True
                if event == "candidate_answer":
                    return "end_interview"
                continue

            if next_event == "skip_question":
                async with self._command_lock:
                    self.skip_question = True
                if event == "candidate_answer":
                    return "skip_question"
                continue

            if self.paused:
                self._pending_events.append(next_event)
                continue

            if next_event == event:
                return next_event

            self._pending_events.append(next_event)

    def _pop_relevant_pending_event(self, target: str) -> str | None:
        wanted = self.CONTROL_EVENTS | {target}
        for index, event in enumerate(self._pending_events):
            if event in wanted:
                del self._pending_events[index]
                return event
        return None


class SessionEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        websocket_hub: WebSocketHub,
        question_service: QuestionService,
        evaluation_service: EvaluationService,
    ) -> None:
        self._session_factory = session_factory
        self._websocket_hub = websocket_hub
        self._question_service = question_service
        self._evaluation_service = evaluation_service
        self._followup_service = FollowUpEngine()
        self._logger = get_logger("fsm.engine")
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._runtimes: dict[str, SessionRuntime] = {}
        self._followup_engines: dict[str, FollowUpResolutionEngine] = {}
        self._task_lock = asyncio.Lock()

    async def create_session(self, payload: SessionCreateRequest) -> SessionCreateResponse:
        session_id = str(uuid4())
        async with self._session_factory() as db:
            record = InterviewSession(
                id=session_id,
                candidate_id=payload.candidate_id,
                job_id=payload.job_id,
                meeting_url=payload.meeting_url,
                meeting_type=payload.meeting_type,
                schedule_time=payload.schedule_time,
                state=SessionState.WAITING.value,
                status=SessionState.WAITING.value,
                current_question_number=0,
                current_question_text=None,
                greeting_text=None,
                duration_seconds=0,
                max_duration_minutes=payload.config.max_duration_minutes,
                max_questions=payload.config.max_questions,
                config=payload.config.model_dump(),
                topics=payload.config.topics,
                language=payload.config.language,
                avatar_persona=payload.config.avatar_persona,
                force_followup_test=payload.config.force_followup_test,
                is_running=False,
            )
            db.add(record)
            await db.commit()

        await self._start_session_task(session_id)

        return SessionCreateResponse(
            session_id=session_id,
            status="SCHEDULED",
            join_url=payload.meeting_url,
        )

    async def list_sessions(self) -> list[SessionStatusResponse]:
        async with self._session_factory() as db:
            result = await db.execute(
                select(InterviewSession).order_by(InterviewSession.created_at.desc())
            )
            sessions = result.scalars().all()
        return [self._to_status_response(session) for session in sessions]

    async def get_status(self, session_id: str) -> SessionStatusResponse:
        session = await self._get_session(session_id)
        return self._to_status_response(session)

    async def submit_answer(self, session_id: str, answer: str) -> SessionAnswerResponse:
        runtime = self._runtime_for(session_id)
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            self._ensure_not_terminal(session)
            if SessionState(session.status) != SessionState.LISTENING:
                raise HTTPException(status_code=409, detail="Answers are only accepted in LISTENING state")
            if not session.current_question_text:
                raise HTTPException(status_code=409, detail="No active question to answer")
            await db.commit()

        await runtime.mark_listening()
        future = await runtime.submit_answer(answer)
        try:
            return await asyncio.wait_for(future, timeout=45)
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="Timed out waiting for evaluation") from exc

    async def apply_command(
        self,
        session_id: str,
        command: RecruiterCommand,
    ) -> SessionCommandResponse:
        runtime = self._runtime_for(session_id)
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            self._ensure_not_terminal(session)
            self._validate_command(command, session)

            if command == RecruiterCommand.EXTEND_5MIN:
                session.max_duration_minutes += 5
                session.config["max_duration_minutes"] = session.max_duration_minutes
                await self._append_event(
                    db,
                    session_id,
                    "state_changed",
                    {
                        "command": command.value,
                        "status": session.status,
                        "max_duration_minutes": session.max_duration_minutes,
                    },
                )
                await db.commit()
                current_status = session.status
                max_duration_minutes = session.max_duration_minutes
            else:
                current_status = session.status
                max_duration_minutes = session.max_duration_minutes
                await db.commit()

        if command == RecruiterCommand.PAUSE:
            await runtime.pause()
        elif command == RecruiterCommand.RESUME:
            await runtime.resume()
        elif command == RecruiterCommand.SKIP_QUESTION:
            await runtime.skip_current_question()
        elif command == RecruiterCommand.END_INTERVIEW:
            await runtime.end_interview()

        return SessionCommandResponse(
            session_id=session_id,
            command=command.value,
            state=current_status,
            max_duration_minutes=max_duration_minutes if command == RecruiterCommand.EXTEND_5MIN else None,
        )

    async def handle_candidate_disconnected(self, session_id: str) -> SessionEventResponse:
        runtime = self._runtime_for(session_id)
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            self._ensure_not_terminal(session)
            await db.commit()
        await runtime.mark_candidate_left()
        return SessionEventResponse(session_id=session_id, status="paused_waiting_reconnect")

    async def handle_candidate_reconnected(self, session_id: str) -> SessionEventResponse:
        runtime = self._runtime_for(session_id)
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            self._ensure_not_terminal(session)
            await db.commit()
        await runtime.mark_candidate_rejoined()
        return SessionEventResponse(session_id=session_id, status="resumed")

    async def handle_live_connection(self, websocket: WebSocket, session_id: str) -> None:
        # Validate session exists BEFORE accepting the WebSocket.
        # If we accept first and then fail, the browser sees code 1006
        # because the connection is abandoned without a close frame.
        try:
            session = await self._get_session(session_id)
        except HTTPException:
            await websocket.close(code=4004, reason="Session not found")
            return
        except Exception as exc:
            self._logger.error("WS pre-accept lookup failed for %s: %s", session_id, exc)
            await websocket.close(code=1011, reason="Internal error")
            return

        await self._websocket_hub.connect(session_id, websocket)
        try:
            # Send initial state snapshot so the client knows the current FSM state.
            snapshot = self._to_status_response(session)
            await websocket.send_json(
                LiveEventEnvelope(
                    event="session_snapshot",
                    payload=snapshot.model_dump(mode="json"),
                ).model_dump()
            )

            # Keep the connection alive.
            # The FSM pushes events via _websocket_hub.broadcast(); this loop
            # only needs to consume client frames so the connection stays open.
            while True:
                data = await websocket.receive_text()
                # Respond to client-side keepalive pings.  Browsers and
                # proxies may kill idle WebSocket connections after 30-60 s.
                if data == "ping":
                    await websocket.send_json({"event": "pong", "payload": {}})
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            self._logger.warning("WebSocket error for session %s: %s", session_id, exc)
            try:
                await websocket.close(code=1011, reason="Internal error")
            except Exception:
                pass  # already closed / broken pipe
        finally:
            await self._websocket_hub.disconnect(session_id, websocket)


    async def shutdown(self) -> None:
        async with self._task_lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for runtime in self._runtimes.values():
            runtime.cancel_scheduled()
        await self._websocket_hub.close_all()

    async def _start_session_task(self, session_id: str) -> None:
        async with self._task_lock:
            existing = self._tasks.get(session_id)
            if existing and not existing.done():
                return
            task = asyncio.create_task(self._run_session(session_id), name=f"fsm:{session_id}")
            self._tasks[session_id] = task
            task.add_done_callback(lambda done, sid=session_id: self._on_task_done(sid, done))

    def _on_task_done(self, session_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(session_id, None)
        self._cleanup_runtime(session_id)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._logger.exception("Background session task crashed for %s: %s", session_id, exc)

    async def _run_session(self, session_id: str) -> None:
        runtime = self._runtime_for(session_id)
        if not await self._acquire_execution_lock(session_id):
            return

        try:
            start_config = self._session_config(await self._get_session(session_id))
            runtime.schedule_event("fsm_start", start_config["fsm_start_delay_seconds"])
            await runtime.wait_for_event(
                "fsm_start",
                timeout=max(start_config["fsm_start_delay_seconds"] + 5, 5),
            )

            await self._mark_started(session_id)
            await self._transition_state(session_id, SessionState.INTRO, "session_started")

            intro_session = await self._get_session(session_id)
            greeting = await self._question_service.generate_greeting(
                candidate_id=intro_session.candidate_id,
                job_id=intro_session.job_id,
                context=list(runtime.interaction_history),
            )
            await self._store_greeting(session_id, greeting)
            await self._publish_event(session_id, "greeting_generated", {"greeting": greeting})

            intro_config = self._session_config(intro_session)
            runtime.schedule_event("intro_complete", intro_config["intro_delay_seconds"])
            await runtime.wait_for_event(
                "intro_complete",
                timeout=intro_config["intro_timeout_seconds"],
            )

            await self._transition_state(session_id, SessionState.ASKING, "intro_complete")
            question_number = 1

            while True:
                snapshot = await self._get_session(session_id)
                if SessionState(snapshot.status) in TERMINAL_STATES:
                    break

                config = self._session_config(snapshot)
                await self._set_question_number(session_id, question_number)
                snapshot.current_question_number = question_number

                if runtime.pending_question is not None:
                    question = runtime.pending_question
                    runtime.pending_question = None
                    snapshot.config = self._annotate_question_history(
                        config=dict(snapshot.config or {}),
                        question_number=question_number,
                        question=question,
                        question_type="followup",
                        meta=runtime.pending_question_meta,
                    )
                    runtime.pending_question_meta = None
                    runtime.pending_topic = None
                else:
                    question = await self._question_service.generate_question(snapshot)
                    snapshot.config = self._annotate_question_history(
                        config=dict(snapshot.config or {}),
                        question_number=question_number,
                        question=question,
                        question_type="harder" if runtime.next_difficulty == "hard" else "new",
                    )

                await self._store_active_question(session_id, question, snapshot.config)
                await self._publish_event(
                    session_id,
                    "question_asked",
                    {
                        "question_number": question_number,
                        "question": question,
                    },
                )

                runtime.schedule_event("question_delivered", config["question_delivery_delay_seconds"])
                await runtime.wait_for_event(
                    "question_delivered",
                    timeout=config["question_delivery_timeout_seconds"],
                )
                await self._transition_state(session_id, SessionState.LISTENING, "question_delivered")

                await runtime.mark_listening()
                try:
                    answer_event = await runtime.wait_for_event(
                        "candidate_answer",
                        timeout=self._listening_timeout_seconds(snapshot, config),
                    )
                except TimeoutError:
                    await runtime.clear_listening()
                    answer_event = "end_interview"

                answer, answer_future = await runtime.consume_answer_submission()
                if answer_event == "skip_question":
                    answer = "Question skipped by recruiter."
                    await self._publish_answer_received(session_id, question, answer, skipped=True)
                    await self._transition_state(session_id, SessionState.EVALUATING, "skip_question")
                    evaluation = EvaluationResult(
                        score=5,
                        feedback="The question was skipped before the candidate answered.",
                    )
                elif answer_event == "end_interview":
                    answer = answer or ""
                    await self._transition_state(session_id, SessionState.EVALUATING, "end_interview")
                    evaluation = EvaluationResult(
                        score=5,
                        feedback="The interview ended before an answer was provided.",
                    )
                else:
                    if not answer:
                        raise RuntimeError("Candidate answer event received without answer payload")
                    await self._publish_answer_received(session_id, question, answer, skipped=False)
                    await self._transition_state(session_id, SessionState.EVALUATING, "answer_received")
                    evaluation = await self._evaluate_answer(
                        snapshot,
                        runtime,
                        question=question,
                        answer=answer,
                    )

                await self._publish_event(
                    session_id,
                    "evaluation_completed",
                    {
                        "question_number": question_number,
                        "score": evaluation.score,
                        "feedback": evaluation.feedback,
                        "overall_score": evaluation.overall_score,
                        "relevance_score": evaluation.relevance_score,
                        "depth_score": evaluation.depth_score,
                        "technical_score": evaluation.technical_score,
                        "communication_score": evaluation.communication_score,
                        "red_flags": evaluation.red_flags,
                        "needs_followup": evaluation.needs_followup,
                        "followup_required": evaluation.followup_required,
                        "followup_reason": evaluation.followup_reason,
                        "followup_priority": evaluation.followup_priority,
                        "missing_dimensions": evaluation.missing_dimensions,
                        "followup_type": evaluation.followup_type,
                        "semantic_topic": evaluation.semantic_topic,
                    },
                )
                await self._publish_event(
                    session_id,
                    "score_ready",
                    {
                        "question_number": question_number,
                        "score": evaluation.score,
                        "feedback": evaluation.feedback,
                        "overall_score": evaluation.overall_score,
                        "relevance_score": evaluation.relevance_score,
                        "depth_score": evaluation.depth_score,
                        "technical_score": evaluation.technical_score,
                        "communication_score": evaluation.communication_score,
                        "red_flags": evaluation.red_flags,
                        "needs_followup": evaluation.needs_followup,
                        "followup_required": evaluation.followup_required,
                        "followup_reason": evaluation.followup_reason,
                        "followup_priority": evaluation.followup_priority,
                        "missing_dimensions": evaluation.missing_dimensions,
                        "followup_type": evaluation.followup_type,
                        "semantic_topic": evaluation.semantic_topic,
                    },
                )

                await self._record_answer_outcome(
                    session_id=session_id,
                    answer=answer or "",
                    evaluation=evaluation,
                    clear_current_question=True,
                )

                # Record answer in followup resolution engine
                current_intent = evaluation.semantic_topic or "general"
                current_topic = evaluation.semantic_topic or "general"
                followup_engine = self._followup_engines.get(session_id)
                if followup_engine:
                    followup_engine.record_answer(
                        answer=answer or "",
                        score=evaluation.score,
                        intent=current_intent,
                        topic=current_topic,
                    )

                await self._transition_state(session_id, SessionState.DECISION, "score_ready")
                decision_session = await self._get_session(session_id)
                config = self._session_config(decision_session)

                runtime.interaction_history.append(
                    {
                        "question_number": question_number,
                        "question": question,
                        "answer": answer or "",
                        "score": evaluation.score,
                        "feedback": evaluation.feedback,
                        "overall_score": evaluation.overall_score,
                        "relevance_score": evaluation.relevance_score,
                        "depth_score": evaluation.depth_score,
                        "technical_score": evaluation.technical_score,
                        "communication_score": evaluation.communication_score,
                        "red_flags": evaluation.red_flags,
                        "needs_followup": evaluation.needs_followup,
                        "followup_required": evaluation.followup_required,
                        "followup_reason": evaluation.followup_reason,
                        "followup_priority": evaluation.followup_priority,
                        "missing_dimensions": evaluation.missing_dimensions,
                        "followup_type": evaluation.followup_type,
                        "semantic_topic": evaluation.semantic_topic,
                    }
                )

                decision = self._resolve_decision(
                    evaluation=evaluation,
                    question_number=question_number,
                    config=config,
                    runtime=runtime,
                    contradiction=self._latest_contradiction(decision_session.config),
                    session_started_at=decision_session.started_at,
                )
                self._logger.info(
                    json.dumps(
                        {
                            "event": "decision_made",
                            "session_id": session_id,
                            "question_number": question_number,
                            "score": evaluation.score,
                            "overall_score": evaluation.overall_score,
                            "red_flags": evaluation.red_flags,
                            "needs_followup": evaluation.needs_followup,
                            "followup_required": evaluation.followup_required,
                            "followup_reason": evaluation.followup_reason,
                            "followup_priority": evaluation.followup_priority,
                            "missing_dimensions": evaluation.missing_dimensions,
                            "followup_type": evaluation.followup_type,
                            "decision": decision,
                            "timestamp": utc_now().isoformat(),
                        }
                    )
                )

                next_state = "ASKING" if decision in {
                    Decision.FOLLOWUP.value,
                    Decision.NEXT.value,
                    Decision.HARDER.value,
                } else "WRAPPING"
                runtime.resolve_answer_future(
                    answer_future,
                    SessionAnswerResponse(
                        question=question,
                        answer=answer or "",
                        score=evaluation.score,
                        feedback=evaluation.feedback,
                        next_state=next_state,
                    ),
                )

                if decision == Decision.FOLLOWUP.value:
                    # Gate FOLLOWUP transition with FollowUpResolutionEngine
                    current_intent = evaluation.semantic_topic or "general"
                    current_topic = evaluation.semantic_topic or "general"
                    followup_engine = self._followup_engines.get(session_id)
                    if followup_engine:
                        should, reason = followup_engine.should_followup(
                            intent=current_intent,
                            topic=current_topic,
                        )
                        if should:
                            await self._transition_state(session_id, SessionState.FOLLOWUP, "targeted_followup")
                        else:
                            self._logger.info(
                                "FollowUpBlocked reason=%s session=%s",
                                reason, session_id,
                            )
                            # Convert to NEXT decision
                            decision = Decision.NEXT.value
                            question_number += 1
                            runtime.next_difficulty = "normal"
                            await self._store_session_config(
                                session_id,
                                self._followup_service.reset_for_next_question(config),
                            )
                            await self._transition_state(session_id, SessionState.ASKING, "next_question")
                            continue
                    else:
                        await self._transition_state(session_id, SessionState.FOLLOWUP, "targeted_followup")
                    followup_session = await self._get_session(session_id)
                    followup = self._followup_service.build_followup(
                        config=dict(followup_session.config or {}),
                        question_number=question_number,
                        original_question=question,
                        candidate_answer=answer or "",
                        evaluation=evaluation,
                        contradiction=self._latest_contradiction(followup_session.config),
                    )
                    await self._store_session_config(session_id, followup.config)
                    if followup.decision == Decision.FOLLOWUP.value and followup.question:
                        runtime.pending_question = await self._question_service.generate_followup(
                            original_question=question,
                            candidate_answer=answer or "",
                            evaluation_feedback=evaluation.followup_reason or evaluation.feedback,
                            context=list(runtime.interaction_history),
                            contradiction=self._latest_contradiction(followup_session.config),
                        )
                        runtime.pending_question_meta = followup.question_meta
                        runtime.pending_topic = followup.question_meta.get("semantic_topic")
                        await self._publish_event(
                            session_id,
                            "followup_planned",
                            {
                                "question_number": question_number,
                                "followup_reason": evaluation.followup_reason,
                                "followup_priority": evaluation.followup_priority,
                                "probe_type": followup.probe_type,
                                "probe_depth": followup.probe_depth,
                                "semantic_repeat_score": followup.semantic_repeat_score,
                                "topic_transition_reason": followup.decision_reason,
                            },
                        )
                        runtime.next_difficulty = "normal"
                        await self._transition_state(session_id, SessionState.ASKING, "followup_generated")
                        continue

                if decision == Decision.HARDER.value:
                    question_number += 1
                    runtime.next_difficulty = "hard"
                    await self._store_session_config(
                        session_id,
                        self._followup_service.reset_for_next_question(config),
                    )
                    # Reset followup resolution engine for next question
                    followup_engine = self._followup_engines.get(session_id)
                    if followup_engine:
                        followup_engine.reset_for_next_question()
                    await self._transition_state(session_id, SessionState.ASKING, "harder_question")
                    continue

                if decision == Decision.NEXT.value:
                    question_number += 1
                    runtime.next_difficulty = "normal"
                    await self._store_session_config(
                        session_id,
                        self._followup_service.reset_for_next_question(config),
                    )
                    # Reset followup resolution engine for next question
                    followup_engine = self._followup_engines.get(session_id)
                    if followup_engine:
                        followup_engine.reset_for_next_question()
                    await self._transition_state(session_id, SessionState.ASKING, "next_question")
                    continue


                runtime.next_difficulty = "normal"
                await self._transition_state(session_id, SessionState.WRAPPING, "decision_wrap")
                break

            await self._clear_active_question(session_id)
            await self._transition_state(session_id, SessionState.ENDED, "interview_completed")
            await self._finalize_completion(session_id)
        except Exception as exc:
            await runtime.clear_listening()
            await self._handle_failure(session_id, exc)
        finally:
            runtime.cancel_scheduled()
            await self._release_execution_lock(session_id)

    async def _acquire_execution_lock(self, session_id: str) -> bool:
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            if session.is_running or SessionState(session.status) in TERMINAL_STATES:
                await db.commit()
                return False
            session.is_running = True
            await db.commit()
            return True

    async def _release_execution_lock(self, session_id: str) -> None:
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            session.is_running = False
            await db.commit()

    async def _mark_started(self, session_id: str) -> None:
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            if session.started_at is None:
                session.started_at = utc_now()
            session.duration_seconds = 0
            await db.commit()

    async def _set_question_number(self, session_id: str, question_number: int) -> None:
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            session.current_question_number = question_number
            session.duration_seconds = self._current_duration_seconds(session)
            await db.commit()

    async def _store_greeting(self, session_id: str, greeting: str) -> None:
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            session.greeting_text = greeting
            await db.commit()

    async def _store_active_question(
        self,
        session_id: str,
        question: str,
        config: dict[str, Any],
    ) -> None:
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            session.current_question_text = question
            session.config = dict(config or {})
            session.duration_seconds = self._current_duration_seconds(session)
            await db.commit()

    async def _clear_active_question(self, session_id: str) -> None:
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            session.current_question_text = None
            await db.commit()

    async def _record_answer_outcome(
        self,
        *,
        session_id: str,
        answer: str,
        evaluation: EvaluationResult,
        clear_current_question: bool,
    ) -> None:
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            config = dict(session.config or {})
            history = config.get("question_history", [])
            if isinstance(history, list) and history:
                latest = dict(history[-1])
                latest["answer"] = answer
                latest["score"] = evaluation.score
                latest["feedback"] = evaluation.feedback
                latest["overall_score"] = evaluation.overall_score
                latest["relevance_score"] = evaluation.relevance_score
                latest["depth_score"] = evaluation.depth_score
                latest["technical_score"] = evaluation.technical_score
                latest["communication_score"] = evaluation.communication_score
                latest["red_flags"] = list(evaluation.red_flags)
                latest["needs_followup"] = evaluation.needs_followup
                latest["followup_required"] = evaluation.followup_required
                latest["followup_reason"] = evaluation.followup_reason
                latest["followup_priority"] = evaluation.followup_priority
                latest["missing_dimensions"] = list(evaluation.missing_dimensions)
                latest["followup_type"] = evaluation.followup_type
                latest["semantic_topic"] = evaluation.semantic_topic
                history[-1] = latest

                candidate_model = CandidateModel.from_dict(config.get("candidate_model"))
                candidate_update = candidate_model.update_from_evaluation(
                    question=latest.get("question", ""),
                    answer=answer,
                    topic=latest.get("topic", ""),
                    technical_score=evaluation.technical_score,
                    depth_score=evaluation.depth_score,
                    communication_score=evaluation.communication_score,
                    history=history[:-1],
                )
                latest["candidate_model_update"] = candidate_update
                history[-1] = latest
                config["candidate_model"] = candidate_model.to_dict()
                config = self._followup_service.sync_memory_after_answer(
                    config=config,
                    evaluation=evaluation,
                    contradiction=candidate_model.latest_contradiction(),
                )

                config["question_history"] = history[-10:]
                session.config = config
            if clear_current_question:
                session.current_question_text = None
            session.duration_seconds = self._current_duration_seconds(session)
            await db.commit()

    async def _transition_state(
        self,
        session_id: str,
        target_state: SessionState,
        reason: str,
        *,
        force: bool = False,
    ) -> None:
        timestamp = utc_now().isoformat()
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            current_state = SessionState(session.status)
            if not force:
                validate_transition(current_state, target_state)

            session.status = target_state.value
            session.state = target_state.value
            session.duration_seconds = self._current_duration_seconds(session)
            if target_state == SessionState.WRAPPING and session.ended_reason is None:
                session.ended_reason = "complete"
            if target_state == SessionState.ENDED:
                session.ended_at = utc_now()
                session.ended_reason = session.ended_reason or "complete"
            if target_state == SessionState.ERROR:
                session.ended_at = utc_now()
                session.ended_reason = session.ended_reason or "error"

            payload = {
                "from": current_state.value,
                "to": target_state.value,
                "reason": reason,
                "timestamp": timestamp,
            }
            await self._append_event(db, session_id, "state_changed", payload)
            await db.commit()

        self._logger.info(
            json.dumps(
                {
                    "event": "state_transition",
                    "session_id": session_id,
                    "from": current_state.value,
                    "to": target_state.value,
                    "reason": reason,
                    "timestamp": timestamp,
                }
            )
        )
        await self._websocket_hub.broadcast(
            session_id,
            LiveEventEnvelope(event="state_changed", payload=payload),
        )

    async def _publish_event(self, session_id: str, event: str, payload: dict[str, Any]) -> None:
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            payload["followup_resolution"] = dict(session.config or {}).get("followup_memory", {})
            await self._append_event(db, session_id, event, payload)
            await db.commit()

        await self._websocket_hub.broadcast(
            session_id,
            LiveEventEnvelope(event=event, payload=payload),
        )

    async def _publish_answer_received(
        self,
        session_id: str,
        question: str,
        answer: str,
        *,
        skipped: bool,
    ) -> None:
        payload = {"question": question, "answer": answer, "skipped": skipped}
        self._logger.info(
            json.dumps(
                {
                    "event": "answer_received",
                    "session_id": session_id,
                    "question": question,
                    "answer": answer,
                    "skipped": skipped,
                    "timestamp": utc_now().isoformat(),
                }
            )
        )
        await self._publish_event(session_id, "answer_received", payload)
        await self._publish_event(session_id, "answer_transcribed", payload)

    async def _append_event(
        self,
        db: AsyncSession,
        session_id: str,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        db.add(SessionEvent(session_id=session_id, event=event, payload=payload))
        await db.flush()

    async def _finalize_completion(self, session_id: str) -> None:
        await self._publish_event(
            session_id,
            "session_ended",
            {"status": SessionState.ENDED.value, "timestamp": utc_now().isoformat()},
        )

    async def _handle_failure(self, session_id: str, exc: Exception) -> None:
        error_message = str(exc) or exc.__class__.__name__
        timestamp = utc_now().isoformat()
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            previous_state = session.status
            session.state = SessionState.ERROR.value
            session.status = SessionState.ERROR.value
            session.error_reason = error_message
            session.ended_reason = "error"
            session.ended_at = utc_now()
            session.duration_seconds = self._current_duration_seconds(session)
            session.current_question_text = None
            session.is_running = False
            await self._append_event(
                db,
                session_id,
                "state_changed",
                {
                    "from": previous_state,
                    "to": SessionState.ERROR.value,
                    "reason": "exception",
                    "timestamp": timestamp,
                    "error": error_message,
                },
            )
            await db.commit()

        self._logger.error(
            json.dumps(
                {
                    "event": "fsm_error",
                    "session_id": session_id,
                    "status": SessionState.ERROR.value,
                    "error": error_message,
                    "timestamp": timestamp,
                }
            )
        )

    def _resolve_decision(
        self,
        *,
        evaluation: EvaluationResult,
        question_number: int,
        config: dict[str, Any],
        runtime: SessionRuntime,
        contradiction: str | None,
        session_started_at: datetime | None,
    ) -> str:
        max_questions = config[CONFIG_KEY_MAX_QUESTIONS]
        if runtime.force_wrap:
            runtime.force_wrap = False
            runtime.skip_question = False
            return Decision.WRAPPING.value
        if runtime.skip_question:
            runtime.skip_question = False
            if question_number >= max_questions:
                return Decision.WRAPPING.value
            return Decision.NEXT.value
        if session_started_at is not None:
            elapsed_seconds = int((utc_now() - session_started_at).total_seconds())
            if elapsed_seconds >= config["max_duration_minutes"] * 60:
                return Decision.WRAPPING.value
        candidate_model = CandidateModel.from_dict(config.get("candidate_model"))
        base_decision = decide_next_action(evaluation.score, question_number, config)
        if base_decision == Decision.NEXT.value and candidate_model.recommended_difficulty() == "harder":
            base_decision = Decision.HARDER.value
        decision, _ = self._followup_service.determine_next_action(
            base_decision=base_decision,
            config=config,
            evaluation=evaluation,
            question_number=question_number,
            max_questions=max_questions,
            contradiction=contradiction,
        )
        return decision

    async def _evaluate_answer(
        self,
        session: InterviewSession,
        runtime: SessionRuntime,
        *,
        question: str,
        answer: str,
    ) -> EvaluationResult:
        if session.force_followup_test and not runtime.forced_followup_used:
            runtime.forced_followup_used = True
            return EvaluationResult(
                score=3,
                feedback="Forced follow-up test.",
                overall_score=24,
                relevance_score=8,
                depth_score=6,
                technical_score=6,
                communication_score=4,
                red_flags=["forced_followup_test"],
                needs_followup=True,
                followup_required=True,
                followup_reason="INCOMPLETE_EXPLANATION",
                followup_priority="HIGH",
                missing_dimensions=["depth", "specificity"],
                followup_type="clarification_probe",
                semantic_topic="general",
            )
        return await self._evaluation_service.evaluate_answer(
            question=question,
            answer=answer,
            context=list(runtime.interaction_history),
        )

    async def _store_session_config(self, session_id: str, config: dict[str, Any]) -> None:
        async with self._session_factory() as db:
            session = await self._get_session_for_update(db, session_id)
            config = dict(config or {})
            # Persist followup resolution state
            followup_engine = self._followup_engines.get(session_id)
            if followup_engine:
                config["followup_resolution"] = followup_engine.to_dict()
            session.config = config
            await db.commit()

    def _annotate_question_history(
        self,
        *,
        config: dict[str, Any],
        question_number: int,
        question: str,
        question_type: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = dict(config or {})
        history = config.get("question_history", [])
        if not isinstance(history, list):
            history = []

        latest = dict(history[-1]) if history and isinstance(history[-1], dict) else {}
        if latest.get("question") != question or latest.get("answer"):
            latest = {"question": question, "answer": "", "score": None}
            history.append(latest)

        meta = dict(meta or {})
        latest["question_id"] = latest.get("question_id") or meta.get("question_id") or self._question_id(question_number, question)
        latest["type"] = question_type
        latest["topic"] = latest.get("topic") or meta.get("topic") or meta.get("semantic_topic") or ""
        latest["root_question_id"] = meta.get("root_question_id") or latest.get("root_question_id")
        latest["followup_chain_id"] = meta.get("followup_chain_id") or latest.get("followup_chain_id")
        latest["probe_reason"] = meta.get("probe_reason") or latest.get("probe_reason")
        latest["probe_type"] = meta.get("probe_type") or latest.get("probe_type")
        latest["probe_depth"] = meta.get("probe_depth", latest.get("probe_depth"))
        latest["semantic_topic"] = meta.get("semantic_topic") or latest.get("semantic_topic") or latest.get("topic", "")
        history[-1] = latest
        config["question_history"] = history[-10:]
        return config

    @staticmethod
    def _question_id(question_number: int, question: str) -> str:
        return f"q{question_number}_{abs(hash(question)) % 10000}"

    @staticmethod
    def _latest_contradiction(config: dict[str, Any]) -> str | None:
        candidate_model = CandidateModel.from_dict(dict(config or {}).get("candidate_model"))
        return candidate_model.latest_contradiction()

    async def _get_session(self, session_id: str) -> InterviewSession:
        async with self._session_factory() as db:
            result = await db.execute(
                select(InterviewSession).where(InterviewSession.id == session_id)
            )
            session = result.scalar_one_or_none()
            if session is None:
                raise HTTPException(status_code=404, detail="Session not found")
            return session

    async def _get_session_for_update(
        self,
        db: AsyncSession,
        session_id: str,
    ) -> InterviewSession:
        result = await db.execute(
            select(InterviewSession)
            .where(InterviewSession.id == session_id)
            .with_for_update()
        )
        session = result.scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    def _ensure_not_terminal(self, session: InterviewSession) -> None:
        if SessionState(session.status) in TERMINAL_STATES:
            raise HTTPException(status_code=409, detail="Session is already terminal")

    def _validate_command(self, command: RecruiterCommand, session: InterviewSession) -> None:
        state = SessionState(session.status)
        if command == RecruiterCommand.PAUSE:
            if state == SessionState.WRAPPING:
                raise HTTPException(status_code=409, detail="Cannot pause while wrapping")
            return
        if command == RecruiterCommand.SKIP_QUESTION:
            if state not in {SessionState.ASKING, SessionState.LISTENING}:
                raise HTTPException(
                    status_code=409,
                    detail="skip_question is only allowed while asking or listening",
                )
            return
        if command == RecruiterCommand.END_INTERVIEW:
            if state == SessionState.WRAPPING:
                raise HTTPException(status_code=409, detail="Session is already wrapping")
            return

    def _runtime_for(self, session_id: str) -> SessionRuntime:
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            runtime = SessionRuntime(session_id=session_id)
            self._runtimes[session_id] = runtime
        # Initialize followup engine for this session if not exists
        if session_id not in self._followup_engines:
            self._followup_engines[session_id] = FollowUpResolutionEngine(
                FollowUpResolutionState(session_id=session_id)
            )
        return runtime

    def _cleanup_runtime(self, session_id: str) -> None:
        runtime = self._runtimes.pop(session_id, None)
        if runtime is not None:
            runtime.cancel_scheduled()

    def _to_status_response(self, session: InterviewSession) -> SessionStatusResponse:
        return SessionStatusResponse(
            session_id=session.id,
            state=session.status,
            current_question_number=session.current_question_number,
            duration_seconds=max(session.duration_seconds, self._current_duration_seconds(session)),
            max_questions=session.max_questions,
            max_duration_minutes=session.max_duration_minutes,
            ended_reason=session.ended_reason,
        )

    def _session_config(self, session: InterviewSession) -> dict[str, int]:
        config = dict(session.config or {})
        config[CONFIG_KEY_MAX_QUESTIONS] = session.max_questions
        config["max_duration_minutes"] = session.max_duration_minutes
        config["fsm_start_delay_seconds"] = int(config.get("fsm_start_delay_seconds", 1))
        config["intro_timeout_seconds"] = int(config.get("intro_timeout_seconds", 15))
        config["question_delivery_timeout_seconds"] = int(config.get("question_delivery_timeout_seconds", 15))
        config["answer_timeout_seconds"] = int(config.get("answer_timeout_seconds", 30))
        config["intro_delay_seconds"] = int(config.get("intro_delay_seconds", 1))
        config["question_delivery_delay_seconds"] = int(config.get("question_delivery_delay_seconds", 1))
        config["answer_capture_delay_seconds"] = int(config.get("answer_capture_delay_seconds", 1))
        config[CONFIG_KEY_FOLLOWUP_SCORE_MAX] = int(config.get(CONFIG_KEY_FOLLOWUP_SCORE_MAX, 4))
        config[CONFIG_KEY_NEXT_SCORE_MIN] = int(config.get(CONFIG_KEY_NEXT_SCORE_MIN, 8))
        return config

    def _listening_timeout_seconds(self, session: InterviewSession, config: dict[str, int]) -> int:
        started_at = session.started_at or utc_now()
        elapsed = int((utc_now() - started_at).total_seconds())
        remaining = max((config["max_duration_minutes"] * 60) - elapsed, 1)
        return remaining

    def _current_duration_seconds(self, session: InterviewSession) -> int:
        started_at = session.started_at or session.created_at
        finished_at = session.ended_at or utc_now()
        return max(int((finished_at - started_at).total_seconds()), 0)
