from __future__ import annotations


EARLY_TOPICS = ("background", "behavioural")
MIDDLE_TOPICS = ("technical_skills", "problem_solving")
LATE_TOPICS = ("culture_fit", "wrapup")


def choose_topic(question_no: int, max_questions: int, covered_topics: list[str]) -> str:
    covered = [topic for topic in covered_topics if topic]
    total = max(max_questions, 1)
    current = max(question_no, 1)
    progress = current / total

    if progress <= 0.25:
        candidates = EARLY_TOPICS
    elif progress <= 0.75:
        candidates = MIDDLE_TOPICS
    else:
        candidates = LATE_TOPICS

    for topic in candidates:
        if topic not in covered:
            return topic

    # If the stage topics are exhausted, prefer any topic not already covered.
    for topic in (*EARLY_TOPICS, *MIDDLE_TOPICS, *LATE_TOPICS):
        if topic not in covered:
            return topic

    return candidates[(current - 1) % len(candidates)]
