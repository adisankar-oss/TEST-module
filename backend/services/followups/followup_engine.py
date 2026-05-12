from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import uuid4

from backend.fsm.decision import Decision
from backend.services.followups.followup_classifier import FollowUpAnalysis, FollowUpClassifier
from backend.services.followups.followup_memory import FollowUpChain, FollowUpMemory
from backend.services.followups.probe_generator import ProbeGenerator
from backend.services.followups.semantic_probe_tracker import ProbeFingerprint, SemanticProbeTracker
from backend.utils.logger import get_logger


MAX_PROBE_DEPTH = 2
MAX_SEMANTIC_REPEATS = 2


@dataclass(slots=True)
class FollowUpDecision:
    decision: str
    decision_reason: str
    question: str = ""
    probe_type: str = ""
    probe_depth: int = 0
    semantic_repeat_score: float = 0.0
    followup_resolution: str = ""
    topic_transition_reason: str = ""
    termination_reason: str = ""
    config: dict = field(default_factory=dict)
    question_meta: dict = field(default_factory=dict)


class FollowUpEngine:
    def __init__(self) -> None:
        self._classifier = FollowUpClassifier()
        self._generator = ProbeGenerator(max_semantic_repeats=MAX_SEMANTIC_REPEATS)
        self._logger = get_logger("services.followups.engine")

    def sync_memory_after_answer(
        self,
        *,
        config: dict,
        evaluation,
        contradiction: str | None = None,
    ) -> dict:
        config = dict(config or {})
        history = config.get("question_history", [])
        if not isinstance(history, list) or not history:
            return config

        memory = FollowUpMemory.from_dict(config.get("followup_memory"))
        latest = dict(history[-1]) if isinstance(history[-1], dict) else {}
        active_chain = memory.active_chain()
        analysis = self._analysis_from_evaluation(evaluation, contradiction)

        if latest.get("type") == "followup" and active_chain is not None:
            active_chain.last_score = getattr(evaluation, "score", None)
            resolution = self._resolve_chain(active_chain, evaluation, latest, analysis)
            memory.upsert_chain(active_chain)
            if active_chain.resolved or active_chain.termination_reason:
                memory.set_active_chain(None)
            config["followup_memory"] = memory.to_dict()
            latest["followup_resolution"] = resolution
            history[-1] = latest
            config["question_history"] = history[-10:]
            self._logger.info(
                json.dumps(
                    {
                        "event": "followup_memory_updated",
                        "followup_chain_id": active_chain.followup_chain_id,
                        "probe_depth": active_chain.probe_depth,
                        "followup_resolution": resolution,
                        "termination_reason": active_chain.termination_reason,
                        "resolved": active_chain.resolved,
                    }
                )
            )
            return config

        if active_chain is not None and (active_chain.resolved or active_chain.termination_reason):
            memory.set_active_chain(None)
            config["followup_memory"] = memory.to_dict()
            return config

        config["followup_memory"] = memory.to_dict()
        return config

    def determine_next_action(
        self,
        *,
        base_decision: str,
        config: dict,
        evaluation,
        question_number: int,
        max_questions: int,
        contradiction: str | None = None,
    ) -> tuple[str, str]:
        memory = FollowUpMemory.from_dict(dict(config or {}).get("followup_memory"))
        history = dict(config or {}).get("question_history", [])
        latest = history[-1] if isinstance(history, list) and history else {}
        active_chain = memory.active_chain()
        latest_chain = memory.chains[-1] if memory.chains else None
        analysis = self._analysis_from_evaluation(evaluation, contradiction)

        if latest.get("type") == "followup" and latest_chain is not None:
            if latest_chain.resolved:
                return self._advance_after_followup(base_decision, evaluation), latest_chain.resolution_reason or "followup_resolved"
            if latest_chain.termination_reason:
                return self._advance_after_followup(base_decision, evaluation), latest_chain.termination_reason

        if active_chain is not None:
            if active_chain.resolved:
                return self._advance_after_followup(base_decision, evaluation), active_chain.resolution_reason or "followup_resolved"
            if active_chain.termination_reason:
                return self._advance_after_followup(base_decision, evaluation), active_chain.termination_reason
            if latest.get("type") == "followup":
                if question_number >= max_questions:
                    return Decision.WRAPPING.value, "followup_at_max_questions"
                return Decision.FOLLOWUP.value, "followup_chain_continues"

        if question_number >= max_questions and base_decision == Decision.WRAPPING.value:
            return base_decision, "max_questions"
        if analysis.followup_required and question_number < max_questions:
            return Decision.FOLLOWUP.value, analysis.followup_reason.lower()
        return base_decision, "base_decision"

    def build_followup(
        self,
        *,
        config: dict,
        question_number: int,
        original_question: str,
        candidate_answer: str,
        evaluation,
        contradiction: str | None = None,
    ) -> FollowUpDecision:
        config = dict(config or {})
        history = config.get("question_history", [])
        memory = FollowUpMemory.from_dict(config.get("followup_memory"))
        analysis = self._analysis_from_evaluation(evaluation, contradiction)
        active_chain = memory.active_chain()

        if active_chain is None:
            root_question_id = self._root_question_id(history, question_number, original_question)
            active_chain = FollowUpChain(
                root_question_id=root_question_id,
                followup_chain_id=f"fu_{uuid4().hex[:10]}",
                probe_reason=analysis.followup_reason,
                semantic_topic=analysis.semantic_topic,
                probe_type=analysis.followup_type,
            )
            memory.upsert_chain(active_chain)
            memory.set_active_chain(active_chain)

        if active_chain.probe_depth >= MAX_PROBE_DEPTH:
            active_chain.termination_reason = "max_probe_depth"
            memory.upsert_chain(active_chain)
            memory.set_active_chain(None)
            config["followup_memory"] = memory.to_dict()
            return FollowUpDecision(
                decision=Decision.NEXT.value,
                decision_reason="max_probe_depth",
                followup_resolution="terminated",
                termination_reason="max_probe_depth",
                topic_transition_reason="probe_depth_limit",
                config=config,
            )

        tracker = SemanticProbeTracker.from_dicts(memory.semantic_probes)
        generated = self._generator.generate(
            analysis=analysis,
            original_question=original_question,
            candidate_answer=candidate_answer,
            probe_depth=active_chain.probe_depth + 1,
            root_question_id=active_chain.root_question_id,
            tracker=tracker,
        )

        if generated.termination_reason or generated.repeat_count >= MAX_SEMANTIC_REPEATS:
            active_chain.termination_reason = generated.termination_reason or "semantic_repetition_limit"
            active_chain.repeat_count = max(active_chain.repeat_count, generated.repeat_count)
            memory.upsert_chain(active_chain)
            memory.set_active_chain(None)
            config["followup_memory"] = memory.to_dict()
            return FollowUpDecision(
                decision=Decision.NEXT.value,
                decision_reason="semantic_repetition_limit",
                followup_resolution="terminated",
                semantic_repeat_score=generated.semantic_repeat_score,
                termination_reason=active_chain.termination_reason,
                topic_transition_reason="semantic_repeat_limit",
                config=config,
            )

        active_chain.probe_depth = generated.probe_depth
        active_chain.repeat_count = generated.repeat_count
        active_chain.probe_type = generated.probe_type
        active_chain.last_probe_question = generated.question
        active_chain.last_probe_intent = analysis.followup_reason.lower()
        active_chain.semantic_topic = generated.semantic_topic or active_chain.semantic_topic
        memory.semantic_probes.append(
            ProbeFingerprint(
                question=generated.question,
                probe_type=generated.probe_type,
                semantic_topic=generated.semantic_topic,
                root_question_id=active_chain.root_question_id,
                followup_chain_id=active_chain.followup_chain_id,
            ).to_dict()
        )
        memory.upsert_chain(active_chain)
        config["followup_memory"] = memory.to_dict()

        question_meta = {
            "question_id": f"{active_chain.followup_chain_id}_d{generated.probe_depth}",
            "root_question_id": active_chain.root_question_id,
            "followup_chain_id": active_chain.followup_chain_id,
            "probe_reason": analysis.followup_reason,
            "probe_type": generated.probe_type,
            "probe_depth": generated.probe_depth,
            "semantic_topic": active_chain.semantic_topic,
            "topic": active_chain.semantic_topic or "followup",
            "type": "followup",
        }
        self._logger.info(
            json.dumps(
                {
                    "event": "followup_probe_generated",
                    "followup_chain_id": active_chain.followup_chain_id,
                    "followup_reason": analysis.followup_reason,
                    "probe_type": generated.probe_type,
                    "probe_depth": generated.probe_depth,
                    "semantic_repeat_score": generated.semantic_repeat_score,
                    "semantic_topic": active_chain.semantic_topic,
                }
            )
        )
        return FollowUpDecision(
            decision=Decision.FOLLOWUP.value,
            decision_reason=analysis.followup_reason.lower(),
            question=generated.question,
            probe_type=generated.probe_type,
            probe_depth=generated.probe_depth,
            semantic_repeat_score=generated.semantic_repeat_score,
            config=config,
            question_meta=question_meta,
        )

    def reset_for_next_question(self, config: dict) -> dict:
        config = dict(config or {})
        memory = FollowUpMemory.from_dict(config.get("followup_memory"))
        memory.set_active_chain(None)
        config["followup_memory"] = memory.to_dict()
        return config

    def _analysis_from_evaluation(self, evaluation, contradiction: str | None) -> FollowUpAnalysis:
        return FollowUpAnalysis(
            followup_required=bool(getattr(evaluation, "followup_required", getattr(evaluation, "needs_followup", False))),
            followup_reason=str(getattr(evaluation, "followup_reason", "") or ""),
            followup_priority=str(getattr(evaluation, "followup_priority", "LOW") or "LOW"),
            missing_dimensions=list(getattr(evaluation, "missing_dimensions", []) or []),
            followup_type=str(getattr(evaluation, "followup_type", "") or "clarification_probe"),
            semantic_topic=str(getattr(evaluation, "semantic_topic", "") or "general"),
            contradiction_text=contradiction or "",
        )

    def _resolve_chain(self, chain: FollowUpChain, evaluation, latest: dict, analysis: FollowUpAnalysis) -> str:
        if getattr(evaluation, "score", 0) >= 7 or (getattr(evaluation, "overall_score", 0) or 0) >= 60:
            chain.resolved = True
            chain.resolution_reason = "score_improved"
            return "score_improved"
        if chain.probe_reason == "NO_EXAMPLE" and self._has_example(latest.get("answer", "")):
            chain.resolved = True
            chain.resolution_reason = "example_provided"
            return "example_provided"
        if chain.probe_reason == "MISSING_TRADEOFFS" and self._has_tradeoffs(latest.get("answer", "")):
            chain.resolved = True
            chain.resolution_reason = "tradeoffs_clarified"
            return "tradeoffs_clarified"
        if chain.probe_reason == "CONTRADICTION" and analysis.followup_reason != "CONTRADICTION":
            chain.resolved = True
            chain.resolution_reason = "contradiction_clarified"
            return "contradiction_clarified"
        if chain.probe_depth >= MAX_PROBE_DEPTH:
            chain.termination_reason = "max_probe_depth"
            return "max_probe_depth"
        if chain.repeat_count >= MAX_SEMANTIC_REPEATS:
            chain.termination_reason = "semantic_repetition_limit"
            return "semantic_repetition_limit"
        return "needs_more_depth"

    @staticmethod
    def _root_question_id(history: list, question_number: int, question: str) -> str:
        if isinstance(history, list):
            for entry in reversed(history):
                if isinstance(entry, dict) and entry.get("type") != "followup":
                    question_id = entry.get("question_id")
                    if question_id:
                        return str(question_id)
        return f"q{question_number}_{abs(hash(question)) % 10000}"

    @staticmethod
    def _advance_after_followup(base_decision: str, evaluation) -> str:
        if getattr(evaluation, "score", 0) >= 8:
            return Decision.HARDER.value
        if base_decision == Decision.FOLLOWUP.value:
            return Decision.NEXT.value
        return base_decision

    @staticmethod
    def _has_example(answer: str) -> bool:
        lowered = str(answer or "").lower()
        return any(marker in lowered for marker in ("for example", "for instance", "when i", "we had", "in one project"))

    @staticmethod
    def _has_tradeoffs(answer: str) -> bool:
        lowered = str(answer or "").lower()
        return any(marker in lowered for marker in ("trade-off", "tradeoff", "versus", "balance", "alternative"))
