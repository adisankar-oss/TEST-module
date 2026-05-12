from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.fsm.decision import Decision
from backend.services.evaluation_service import EvaluationResult
from backend.services.followups.followup_classifier import FollowUpClassifier
from backend.services.followups.followup_engine import FollowUpEngine


def make_config(question: str, *, question_id: str = "q1", topic: str = "behavioral") -> dict:
    return {
        "max_questions": 5,
        "question_history": [
            {
                "question": question,
                "answer": "",
                "score": None,
                "question_id": question_id,
                "type": "new",
                "topic": topic,
            }
        ],
        "followup_memory": {},
    }


def make_eval(
    *,
    score: int,
    reason: str,
    probe_type: str,
    priority: str = "HIGH",
    missing_dimensions: list[str] | None = None,
    semantic_topic: str = "general",
    overall_score: int | None = None,
    needs_followup: bool = True,
) -> EvaluationResult:
    return EvaluationResult(
        score=score,
        feedback="needs work",
        overall_score=overall_score if overall_score is not None else score * 8,
        relevance_score=8,
        depth_score=6,
        technical_score=6,
        communication_score=5,
        red_flags=[],
        needs_followup=needs_followup,
        followup_required=needs_followup,
        followup_reason=reason,
        followup_priority=priority,
        missing_dimensions=missing_dimensions or ["depth"],
        followup_type=probe_type,
        semantic_topic=semantic_topic,
    )


def append_followup_question(config: dict, *, question: str, meta: dict, answer: str = "") -> dict:
    updated = dict(config)
    history = list(updated.get("question_history", []))
    history.append(
        {
            "question": question,
            "answer": answer,
            "score": None,
            "question_id": meta["question_id"],
            "type": "followup",
            "topic": meta.get("topic", meta.get("semantic_topic", "followup")),
            "root_question_id": meta.get("root_question_id"),
            "followup_chain_id": meta.get("followup_chain_id"),
            "probe_reason": meta.get("probe_reason"),
            "probe_type": meta.get("probe_type"),
            "probe_depth": meta.get("probe_depth"),
            "semantic_topic": meta.get("semantic_topic"),
        }
    )
    updated["question_history"] = history
    return updated


def test_one_word_answer_generates_targeted_followup():
    engine = FollowUpEngine()
    config = make_config("Provide a concrete example of identifying a risk and communicating it effectively.")
    evaluation = make_eval(
        score=1,
        reason="NO_EXAMPLE",
        probe_type="example_probe",
        missing_dimensions=["specific_example"],
        semantic_topic="risk communication",
    )

    decision = engine.build_followup(
        config=config,
        question_number=1,
        original_question=config["question_history"][-1]["question"],
        candidate_answer="ok",
        evaluation=evaluation,
    )

    assert decision.decision == Decision.FOLLOWUP.value
    assert "specific example" in decision.question.lower() or "concrete situation" in decision.question.lower()
    assert "challenging system" not in decision.question.lower()


def test_vague_answer_classification():
    classifier = FollowUpClassifier()
    result = classifier.classify(
        question="How did you debug the issue?",
        answer="It was kind of tricky and we handled it somehow.",
        relevance_score=10,
        depth_score=7,
        technical_score=7,
        communication_score=6,
    )

    assert result.followup_required is True
    assert result.followup_reason in {"VAGUE_RESPONSE", "LOW_DEPTH", "INCOMPLETE_EXPLANATION"}


def test_contradiction_clarification_probe():
    engine = FollowUpEngine()
    config = make_config("How did you handle scalability in that system?", topic="technical_skills")
    evaluation = make_eval(
        score=3,
        reason="CONTRADICTION",
        probe_type="contradiction_probe",
        semantic_topic="scalability",
    )

    decision = engine.build_followup(
        config=config,
        question_number=1,
        original_question=config["question_history"][-1]["question"],
        candidate_answer="We used caching.",
        evaluation=evaluation,
        contradiction="You mentioned earlier that caching handled scalability, but now you're describing a synchronous flow.",
    )

    assert decision.decision == Decision.FOLLOWUP.value
    assert "clarify" in decision.question.lower()


def test_repeated_weak_answers_terminate_followup_chain():
    engine = FollowUpEngine()
    root_question = "Walk me through the trade-offs you considered in that design."
    config = make_config(root_question, topic="technical_skills")
    initial = make_eval(
        score=2,
        reason="MISSING_TRADEOFFS",
        probe_type="tradeoff_probe",
        semantic_topic="tradeoffs design",
    )

    first = engine.build_followup(
        config=config,
        question_number=1,
        original_question=root_question,
        candidate_answer="It depended.",
        evaluation=initial,
    )
    config = append_followup_question(first.config, question=first.question, meta=first.question_meta, answer="still depends")
    config = engine.sync_memory_after_answer(config=config, evaluation=initial)

    second = engine.build_followup(
        config=config,
        question_number=1,
        original_question=first.question,
        candidate_answer="still depends",
        evaluation=initial,
    )
    config = append_followup_question(second.config, question=second.question, meta=second.question_meta, answer="same idea")
    config = engine.sync_memory_after_answer(config=config, evaluation=initial)

    decision, reason = engine.determine_next_action(
        base_decision=Decision.FOLLOWUP.value,
        config=config,
        evaluation=initial,
        question_number=1,
        max_questions=5,
    )

    assert decision != Decision.FOLLOWUP.value
    assert reason in {"max_probe_depth", "semantic_repetition_limit"}


def test_followup_resolution_after_improved_answer():
    engine = FollowUpEngine()
    root_question = "Could you walk me through a specific incident and the trade-offs you made?"
    config = make_config(root_question)
    weak = make_eval(
        score=2,
        reason="NO_EXAMPLE",
        probe_type="example_probe",
        semantic_topic="incident tradeoffs",
    )
    followup = engine.build_followup(
        config=config,
        question_number=1,
        original_question=root_question,
        candidate_answer="not much to say",
        evaluation=weak,
    )

    strong = make_eval(
        score=8,
        reason="",
        probe_type="example_probe",
        priority="LOW",
        missing_dimensions=[],
        semantic_topic="incident tradeoffs",
        overall_score=70,
        needs_followup=False,
    )
    config = append_followup_question(
        followup.config,
        question=followup.question,
        meta=followup.question_meta,
        answer="For example, during a production incident I chose rollback over patching because risk was lower.",
    )
    config = engine.sync_memory_after_answer(config=config, evaluation=strong)
    decision, _ = engine.determine_next_action(
        base_decision=Decision.NEXT.value,
        config=config,
        evaluation=strong,
        question_number=1,
        max_questions=5,
    )

    assert decision in {Decision.NEXT.value, Decision.HARDER.value}


def test_semantic_repetition_prevention():
    engine = FollowUpEngine()
    config = make_config("How did you reason about that design choice?", topic="technical_skills")
    config["followup_memory"] = {
        "active_chain_id": "fu_repeat",
        "chains": [
            {
                "root_question_id": "q1",
                "followup_chain_id": "fu_repeat",
                "probe_reason": "LOW_DEPTH",
                "probe_depth": 1,
                "resolved": False,
                "semantic_topic": "design choice",
                "repeat_count": 1,
                "probe_type": "clarification_probe",
                "last_probe_question": "Can you clarify what you mean by that design choice?",
                "last_probe_intent": "low_depth",
                "last_score": 2,
                "resolution_reason": "",
                "termination_reason": "",
            }
        ],
        "semantic_probes": [
            {
                "question": "Can you clarify what you mean by that design choice?",
                "probe_type": "clarification_probe",
                "semantic_topic": "design choice",
                "root_question_id": "q1",
                "followup_chain_id": "fu_repeat",
            },
            {
                "question": "Could you explain that design choice in more detail?",
                "probe_type": "clarification_probe",
                "semantic_topic": "design choice",
                "root_question_id": "q1",
                "followup_chain_id": "fu_repeat",
            },
        ],
    }
    evaluation = make_eval(score=2, reason="LOW_DEPTH", probe_type="clarification_probe", semantic_topic="design choice")

    decision = engine.build_followup(
        config=config,
        question_number=1,
        original_question=config["question_history"][-1]["question"],
        candidate_answer="same answer",
        evaluation=evaluation,
    )

    assert decision.decision == Decision.NEXT.value
    assert decision.termination_reason == "semantic_repetition_limit"


def test_strong_answer_keeps_harder_path():
    engine = FollowUpEngine()
    config = make_config("How would you scale that service?", topic="technical_skills")
    evaluation = make_eval(
        score=9,
        reason="",
        probe_type="technical_probe",
        priority="LOW",
        missing_dimensions=[],
        semantic_topic="scale service",
        overall_score=75,
        needs_followup=False,
    )

    decision, reason = engine.determine_next_action(
        base_decision=Decision.HARDER.value,
        config=config,
        evaluation=evaluation,
        question_number=1,
        max_questions=5,
    )

    assert decision == Decision.HARDER.value
    assert reason == "base_decision"


def test_technical_probe_selection():
    classifier = FollowUpClassifier()
    result = classifier.classify(
        question="How did you design the cache invalidation strategy for that API?",
        answer="We used a cache.",
        relevance_score=10,
        depth_score=7,
        technical_score=5,
        communication_score=8,
    )

    assert result.followup_required is True
    assert result.followup_type in {"technical_probe", "scalability_probe"}


def test_behavioral_probe_selection():
    classifier = FollowUpClassifier()
    result = classifier.classify(
        question="Describe a time you had to communicate a risk to stakeholders.",
        answer="ok",
        relevance_score=5,
        depth_score=3,
        technical_score=3,
        communication_score=4,
    )

    assert result.followup_required is True
    assert result.followup_type in {"example_probe", "behavioral_probe", "clarification_probe"}
