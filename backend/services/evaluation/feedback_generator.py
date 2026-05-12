import os
import asyncio
from typing import List

from .evaluation_models import (
    DimensionScores,
    FollowUpSignal,
    FollowUpReason,
    AnswerLength,
)

# --- FOLLOW-UP SIGNAL ---
def determine_followup(dimensions: DimensionScores, length: AnswerLength) -> FollowUpSignal:
    """Determine whether a follow‑up question is required.
    Evaluated in order; the first matching rule is returned.
    """
    if dimensions.depth < 40:
        return FollowUpSignal(required=True, reason=FollowUpReason.LOW_DEPTH)
    if dimensions.relevance < 45:
        return FollowUpSignal(required=True, reason=FollowUpReason.VAGUE_ANSWER)
    if dimensions.technical < 35:
        # Domain inference omitted – rule applied when technical score is low
        return FollowUpSignal(required=True, reason=FollowUpReason.WEAK_TECHNICAL_REASONING)
    if dimensions.confidence < 40:
        return FollowUpSignal(required=True, reason=FollowUpReason.VAGUE_ANSWER)
    # Note: example‑signal check omitted – not enough context
    return FollowUpSignal(required=False, reason=None)

# --- STRENGTH DETECTOR ---
def identify_strengths(dimensions: DimensionScores, answer: str) -> List[str]:
    """Return up to three concrete strength statements based on score thresholds.
    The *answer* argument is kept for signature compatibility – it is not used.
    """
    strengths: List[str] = []
    if dimensions.relevance > 75:
        strengths.append(
            "Directly addressed the core of the question without drifting."
        )
    if dimensions.depth > 70:
        strengths.append(
            "Demonstrated multi‑step reasoning with clear cause‑and‑effect logic."
        )
    if dimensions.technical > 65:
        strengths.append(
            "Used domain‑specific vocabulary accurately and in correct context."
        )
    if dimensions.confidence > 70:
        strengths.append(
            "Gave specific, decisive answers with concrete details."
        )
    if dimensions.clarity > 70:
        strengths.append(
            "Communicated in a structured, easy‑to‑follow manner."
        )
    return strengths[:3]

# --- WEAKNESS DETECTOR ---
def identify_weaknesses(
    dimensions: DimensionScores,
    length: AnswerLength,
    generic_phrases: List[str],
) -> List[str]:
    """Return up to three concrete weakness statements.
    """
    weaknesses: List[str] = []
    if dimensions.depth < 50:
        weaknesses.append(
            "Described the outcome but did not explain the reasoning or trade‑offs behind the decision."
        )
    if dimensions.technical < 40:
        weaknesses.append(
            "Lacked technical specificity – the answer would benefit from referencing concrete tools, patterns, or metrics."
        )
    if dimensions.clarity < 45:
        weaknesses.append(
            "The response was fragmented and difficult to follow as a coherent argument."
        )
    if generic_phrases:
        sample = ", ".join(generic_phrases[:2])
        weaknesses.append(
            f"Relied on filler language ({sample}) which reduced answer precision."
        )
    if length == AnswerLength.VERY_SHORT:
        weaknesses.append("The answer was too brief to demonstrate reasoning depth.")
    return weaknesses[:3]

# --- FEEDBACK COMPOSER ---
def compose_feedback(
    dimensions: DimensionScores,
    weaknesses: List[str],
    strengths: List[str],
    length: AnswerLength,
) -> str:
    """Create a short (2‑4 sentence) interviewer‑style feedback.
    The lowest‑scoring dimension is called out explicitly.
    """
    # Determine the lowest scoring dimension name
    dim_dict = {
        "relevance": dimensions.relevance,
        "depth": dimensions.depth,
        "clarity": dimensions.clarity,
        "technical": dimensions.technical,
        "confidence": dimensions.confidence,
    }
    lowest_name = min(dim_dict, key=dim_dict.get)
    lowest_score = dim_dict[lowest_name]

    sentences: List[str] = []
    if length == AnswerLength.VERY_SHORT:
        sentences.append("The response is notably brief, limiting the ability to assess depth.")
    if strengths:
        sentences.append(" ".join(strengths))
    if weaknesses:
        sentences.append(" ".join(weaknesses))
    # Explicitly reference the weakest dimension
    sentences.append(
        f"The {lowest_name} score of {lowest_score} indicates room for improvement in that area."
    )
    # Ensure 2‑4 sentences
    feedback = " ".join(sentences[:4])
    return feedback

# --- DIFFICULTY RECOMMENDER ---
def recommend_difficulty(score: int) -> str:
    if score >= 80:
        return "harder"
    if score >= 55:
        return "same"
    return "easier"

# --- LLM REFINEMENT (optional) ---
async def refine_feedback_with_llm(feedback: str, question: str, answer: str) -> str:
    """Attempt to rephrase *feedback* via Claude.
    If the call fails for any reason, the original *feedback* is returned.
    """
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        system_prompt = (
            "You are a senior engineering interviewer. Rephrase this feedback to sound direct and specific. "
            "Do not add new content or soften criticism. Max four sentences."
        )
        # Simple message format – include original feedback
        user_prompt = f"Feedback: {feedback}\nQuestion: {question}\nAnswer: {answer}"
        response = client.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=500,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        refined = response.content[0].text if response.content else feedback
        return refined.strip() or feedback
    except Exception:
        # Safe‑fail: return original feedback unchanged
        return feedback
