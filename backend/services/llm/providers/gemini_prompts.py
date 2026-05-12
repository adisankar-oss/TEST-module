from __future__ import annotations

import json
from typing import Any


def build_question_generation_prompts(
    *,
    role: str,
    difficulty: str,
    question_number: int,
    max_questions: int,
    selected_topic: str,
    previous_questions: list[str],
    topic_progression: list[str],
    confidence_scores: list[float],
    followup_history: list[dict[str, Any]],
    candidate_performance: dict[str, Any],
    session_memory: dict[str, Any],
    fallback_question: str = "",
) -> tuple[str, str]:
    system_prompt = (
        "You are a professional interviewer conducting a real hiring interview.\n"
        "Your goal is to evaluate the candidate naturally, like a real recruiter would.\n"
        "Generate questions that feel conversational, relevant to the role, and probe actual competency.\n"
        "Avoid: generic filler questions, robotic phrasing, obvious 'test' questions, abrupt topic jumps.\n"
        "Return strict JSON only with keys: question, type, topic, reasoning.\n"
        "- question: Natural interview question ending with '?', sounds like a real recruiter asks.\n"
        "- type: one of new, harder, easier, followup.\n"
        "- topic: stays aligned with interview flow.\n"
        "- reasoning: brief factual note on why this question fits now."
    )
    user_prompt = (
        f"Role: {role}\n"
        f"Target difficulty: {difficulty}\n"
        f"Position in interview: question {question_number} of {max_questions}\n"
        f"Current topic focus: {selected_topic}\n"
        f"Previous questions asked: {json.dumps(previous_questions[-8:], ensure_ascii=True)}\n"
        f"Topics covered so far: {json.dumps(topic_progression[-6:], ensure_ascii=True)}\n"
        f"Candidate response quality (recent): {json.dumps(confidence_scores[-4:], ensure_ascii=True)}\n"
        f"Follow-up depth: {json.dumps(followup_history[-3:], ensure_ascii=True)}\n"
        f"Candidate performance profile: {json.dumps(candidate_performance, ensure_ascii=True)}\n"
        f"Conversation context: {json.dumps(session_memory, ensure_ascii=True)}\n"
        "Generate a natural, recruiter-grade question that continues the interview meaningfully."
    )
    return system_prompt, user_prompt


def build_final_summary_prompts(
    *,
    candidate_name: str,
    role: str,
    session_history: list[dict[str, Any]],
    summary_context: dict[str, Any],
) -> tuple[str, str]:
    system_prompt = (
        "You are an experienced hiring manager writing a post-interview assessment.\n"
        "Write a concise, evidence-based summary suitable for a hiring review panel.\n"
        "Base all findings strictly on the session transcript evidence.\n"
        "Do NOT invent facts, assume competencies not demonstrated, or extrapolate beyond evidence.\n"
        "Return plain text in professional bullet format:\n"
        "- Key strengths observed\n"
        "- Areas of concern with evidence\n"
        "- Communication style assessment\n"
        "- Recommendation with rationale\n"
    )
    user_prompt = (
        f"Candidate: {candidate_name}\n"
        f"Role: {role}\n"
        f"Interview transcript: {json.dumps(session_history, ensure_ascii=True)}\n"
        f"Additional context: {json.dumps(summary_context, ensure_ascii=True)}\n"
        "Write a comprehensive hiring assessment based ONLY on the observed interview content."
    )
    return system_prompt, user_prompt


def build_greeting_prompts(*, candidate_name: str, role: str) -> tuple[str, str]:
    system_prompt = (
        "You are a professional interviewer starting a real hiring conversation.\n"
        "Your tone should be: warm but professional, conversational but purposeful, brief but welcoming.\n"
        "Avoid: robotic openings, obvious AI phrasing, generic templates, overly formal language.\n"
        "Sound like an experienced recruiter conducting a real interview.\n"
        "End with an natural opening question that invites the candidate to share something meaningful.\n"
        "Return plain text only."
    )
    user_prompt = (
        f"Candidate: {candidate_name}\n"
        f"Role applied for: {role}\n"
        "Give a warm, natural opening that introduces yourself, sets expectations for the conversation, "
        "and asks an opening question. Keep it under 3 sentences. Make it feel like a real interview start."
    )
    return system_prompt, user_prompt
