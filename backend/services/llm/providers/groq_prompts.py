from __future__ import annotations

import json
from typing import Any


def build_realtime_evaluation_prompts(
    *,
    question: str,
    answer: str,
    keywords: list[str],
    role_level: str,
    context: list[dict[str, Any]],
) -> tuple[str, str]:
    system_prompt = (
        "You are a recruiter-grade interview evaluator.\n"
        "Score the candidate's latest answer only.\n"
        "Do not invent earlier discussion that is not in the supplied context.\n"
        "Return strict JSON only with these keys:\n"
        "{"
        '"score": 0,'
        '"strengths": [],'
        '"weaknesses": [],'
        '"followup_required": true,'
        '"followup_reason": "",'
        '"confidence_score": 0.0,'
        '"decision": "",'
        '"feedback": "",'
        '"semantic_topic": ""'
        "}\n"
        "Rules:\n"
        "- score must be 0-10.\n"
        "- confidence_score must be 0.0-1.0.\n"
        "- decision must be one of followup, next, harder, wrap.\n"
        "- weaknesses should reflect missing substance, not generic filler.\n"
        "- followup_required must be true when the answer lacks depth, specificity, technical reasoning, or consistency."
    )
    user_prompt = (
        f"Role level: {role_level}\n"
        f"Question: {question}\n"
        f"Candidate answer: {answer}\n"
        f"Expected keywords: {json.dumps(keywords, ensure_ascii=True)}\n"
        f"Recent context: {json.dumps(context[-3:], ensure_ascii=True)}\n"
        "Assess whether a recruiter should probe deeper or move on."
    )
    return system_prompt, user_prompt


def build_followup_generation_prompts(
    *,
    original_question: str,
    candidate_answer: str,
    evaluation_feedback: str,
    followup_reason: str,
    session_memory: dict[str, Any],
    contradiction: str | None,
    recent_followups: list[str],
) -> tuple[str, str]:
    system_prompt = (
        "You are an expert technical interviewer.\n"
        "Write exactly one recruiter-like follow-up question.\n"
        "Ground it in the candidate's latest answer and the evaluator signal.\n"
        "Avoid robotic phrasing, repeated probes, generic clarification loops, or abrupt topic changes.\n"
        "Return only the question text."
    )
    user_prompt = (
        f"Original question: {original_question}\n"
        f"Candidate answer: {candidate_answer}\n"
        f"Evaluator analysis: {evaluation_feedback}\n"
        f"Follow-up reason: {followup_reason}\n"
        f"Contradiction to clarify: {contradiction or ''}\n"
        f"Recent follow-ups: {json.dumps(recent_followups[-4:], ensure_ascii=True)}\n"
        f"Session memory: {json.dumps(session_memory, ensure_ascii=True)}\n"
        "Write one natural, specific, context-aware follow-up question."
    )
    return system_prompt, user_prompt
