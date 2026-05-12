TECH_TERMS = {
  "redis","kafka","postgres","docker","kubernetes","nginx","grpc",
  "rest","graphql","lambda","dynamo","elasticsearch","cassandra",
  "token bucket","circuit breaker","load balancer","cache","queue",
  "shard","index","replication"
}
REASONING_CONNECTORS = {
  "because","therefore","which means","so that",
  "in order to","this allowed","as a result"
}
GENERIC_PHRASES = {"basically","kind of","something like","i just","somehow"}

def extract_answer_anchor(answer: str) -> str:
  sentences = [s.strip() for s in answer.replace(".", ". ").split(". ")
               if len(s.strip()) > 10]
  if not sentences:
    return answer[:100]

  def score(s: str) -> int:
    sl = s.lower()
    n  = sum(2 for t in TECH_TERMS if t in sl)
    n += sum(1 for rc in REASONING_CONNECTORS if rc in sl)
    n += sum(1 for w in s.split() if any(c.isdigit() for c in w))
    n -= sum(1 for gp in GENERIC_PHRASES if gp in sl)
    return n

  return sorted(sentences, key=score, reverse=True)[0][:100]

FOLLOWUP_TEMPLATES: dict[str, list[str]] = {
  "low_depth": [
    'You mentioned "{anchor}" — what was the reasoning behind that decision?',
    'You described "{anchor}" — what trade‑offs did you consider?',
    'When you said "{anchor}", what alternatives did you evaluate?',
  ],
  "weak_technical_reasoning": [
    'You said "{anchor}" — which specific tools or patterns did you use?',
    'Regarding "{anchor}" — how did you validate the approach?',
    'When you mentioned "{anchor}", how did you handle failure scenarios?',
  ],
  "vague_answer": [
    'You mentioned "{anchor}" — can you walk me through a concrete example?',
    'When you said "{anchor}", what did that look like in practice?',
  ],
  "contradiction": ['{challenge}'],
  "insufficient_example": [
    'You mentioned "{anchor}" — can you describe a specific situation?',
    'Regarding "{anchor}" — what was the actual outcome?',
  ],
  "depth_probe": [
    'You brought up "{anchor}" — how would your approach change at 10x scale?',
    'Building on "{anchor}" — what would break first under load?',
  ],
  "strength_push": [
    'Since you have experience with "{anchor}", how would you extend that multi‑region?',
    'Given "{anchor}", how would you mentor a junior engineer on that decision?',
  ],
}

def select_template(reason: str, anchor: str, challenge: str = "") -> str:
    templates = FOLLOWUP_TEMPLATES.get(reason, FOLLOWUP_TEMPLATES["low_depth"])
    idx = hash(anchor) % len(templates)
    template = templates[idx]
    # Use SafePromptRenderer for rendering
    result = _renderer.render(
        template=template,
        variables={"anchor": anchor, "challenge": challenge},
        template_id=f"followup_{reason}",
        session_id="unknown",
    )
    if result.fallback_used:
        # Log a warning but continue with the rendered (fallback) prompt
        import logging
        logging.getLogger(__name__).warning(
            "PromptRenderFallbackUsed",
            extra={"template_id": f"followup_{reason}", "session_id": "unknown", "missing_vars": result.missing_vars},
        )
    return result.prompt

def _extract_topic_label(anchor: str) -> str:
  stopwords = {"i","we","the","a","an","to","of","in","and","or","that"}
  words = [w.strip(".,") for w in anchor.lower().split() if w not in stopwords]
  return " ".join(words[:3]) if words else "general"

# ---------------------------------------------------------------------------
# Follow-up question generation class
# ---------------------------------------------------------------------------
from intent_registry import QuestionRecord, classify_question_intent
from backend.services.evaluation.evaluation_models import EvaluationResult
from backend.services.interview.candidate_memory import CandidateMemory

# Import SafePromptRenderer for safe prompt rendering
from backend.services.llm.safe_prompt_renderer import SafePromptRenderer

_renderer = SafePromptRenderer()

class FollowUpGenerator:

  def __init__(self, llm_client=None):
    self.llm_client = llm_client

  def generate(
    self,
    answer: str,
    evaluation: EvaluationResult,
    memory: CandidateMemory,
    previous_question: str
  ) -> QuestionRecord:

    anchor = extract_answer_anchor(answer)
    ctx    = memory.get_strategy_context()

    if ctx.get("has_unresolved_contradictions"):
      reason   = "contradiction"
      q_text   = select_template("contradiction", anchor,
                   ctx.get("contradiction_challenge", ""))

    elif evaluation.followup.reason:
      reason = evaluation.followup.reason.value
      q_text = select_template(reason, anchor)

    elif ctx.get("recommended_difficulty_shift") == "increase":
      reason = "strength_push"
      q_text = select_template("strength_push", anchor)

    else:
      reason = "depth_probe"
      q_text = select_template("depth_probe", anchor)

    continuity = ctx.get("continuity_reference")
    if continuity and reason != "contradiction":
      q_text = continuity + q_text

    if self.llm_client:
      q_text = self._refine_with_llm(q_text, answer, previous_question)

    return QuestionRecord(
      question   = q_text,
      topic      = _extract_topic_label(anchor),
      intent     = reason,
      difficulty = self._infer_difficulty(evaluation, ctx),
      followup_to= classify_question_intent(previous_question)
    )

  def _infer_difficulty(self, evaluation: EvaluationResult, ctx: dict) -> str:
    shift = ctx.get("recommended_difficulty_shift", "maintain")
    base  = evaluation.difficulty_recommendation
    if shift == "increase" or base == "harder":  return "hard"
    if shift == "decrease" or base == "easier":  return "easy"
    return "medium"

  def _refine_with_llm(self, q_text: str, answer: str, prev_q: str) -> str:
    try:
      import anthropic
      r = anthropic.Anthropic().messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=100,
        messages=[{"role": "user", "content": (
          f"Senior technical interviewer. Candidate said: \"{answer[:200]}\"\n"
          f"Rephrase this follow-up naturally, under 40 words, same intent:\n\"{q_text}\"\n"
          f"Return only the rephrased question."
        )}]
      )
      refined = r.content[0].text.strip()
      return refined if len(refined) > 10 else q_text
    except Exception:
      return q_text
