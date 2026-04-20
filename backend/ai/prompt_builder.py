from __future__ import annotations

import json
from typing import Any


def build_question_prompt(
    *,
    job_id: str,
    question_number: int,
    max_questions: int,
    selected_topic: str,
    previous_questions: list[str],
    transcript_entries: list[dict[str, Any]],
    last_score: int | None,
    covered_topics: list[str],
) -> str:
    recent_context = transcript_entries[-3:]
    previous_questions_list = previous_questions[-10:]
    if question_number <= max(max_questions, 1) * 0.25:
        stage = "warmup/background"
    elif question_number <= max(max_questions, 1) * 0.75:
        stage = "technical depth"
    else:
        stage = "behavioural/culture"

    return (
        "SYSTEM:\n"
        "You are a highly experienced technical interviewer conducting a real interview.\n"
        "Your job is NOT just to generate a question.\n"
        "Your job is to DECIDE the most appropriate next question based on:\n"
        "* What has already been asked\n"
        "* How the candidate answered\n"
        "* What topics are covered vs missing\n"
        "* The candidate's apparent skill level\n"
        "You must behave like a human interviewer:\n"
        "* Ask follow-up questions when answers contain useful signals\n"
        "* Change topic if the candidate is struggling\n"
        "* Increase difficulty if the candidate is strong\n"
        "* Avoid repeating concepts even if phrased differently\n"
        "NEVER ask generic or disconnected questions.\n\n"
        "USER:\n"
        "INTERVIEW STATE:\n"
        f"Question Number: {question_number} / {max_questions}\n\n"
        "Current Stage:\n"
        f"* {stage}\n\n"
        "PREVIOUS QUESTIONS:\n"
        f"{json.dumps(previous_questions_list, ensure_ascii=True)}\n\n"
        "LAST 3 Q/A TRANSCRIPT:\n"
        f"{json.dumps(recent_context, ensure_ascii=True)}\n\n"
        "LAST ANSWER SCORE:\n"
        f"{json.dumps(last_score)}\n\n"
        "COVERED TOPICS:\n"
        f"{json.dumps(covered_topics, ensure_ascii=True)}\n\n"
        "ADDITIONAL ROLE CONTEXT:\n"
        f"Job ID: {job_id}\n"
        f"Suggested Topic: {selected_topic}\n\n"
        "INSTRUCTIONS:\n"
        "1. First think internally about whether to follow up, switch topic, simplify, or go deeper.\n"
        "2. Then choose ONE action: followup, new, easier, or harder.\n"
        "3. The question must be specific, contextual, open-ended, and end with '?'.\n"
        "4. Do not repeat any previous question or concept.\n"
        "5. Priority order: follow-up if meaningful, uncovered topic, then difficulty adjustment.\n\n"
        "OUTPUT FORMAT (STRICT JSON ONLY):\n"
        "{\n"
        '  "question": "string",\n'
        '  "topic": "string",\n'
        '  "type": "followup | new | easier | harder",\n'
        '  "reasoning": "string",\n'
        '  "expected_keywords": ["string"],\n'
        '  "follow_up_hint": "string"\n'
        "}"
    )
