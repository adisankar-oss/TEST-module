from __future__ import annotations

from difflib import SequenceMatcher
from functools import lru_cache
from typing import Sequence


SIMILARITY_THRESHOLD = 0.85


def is_duplicate_question(
    candidate_question: str,
    previous_questions: Sequence[str],
    threshold: float = SIMILARITY_THRESHOLD,
) -> bool:
    candidate = _normalize(candidate_question)
    if not candidate:
        return True

    prior = [_normalize(question) for question in previous_questions if _normalize(question)]
    if not prior:
        return False

    model = _load_embedding_model()
    if model is not None:
        try:
            embeddings = model.encode([candidate, *prior], normalize_embeddings=True)
            candidate_embedding = embeddings[0]
            for previous_embedding in embeddings[1:]:
                similarity = float((candidate_embedding * previous_embedding).sum())
                if similarity >= threshold:
                    return True
            return False
        except Exception:
            pass

    for question in prior:
        if SequenceMatcher(a=candidate, b=question).ratio() >= threshold:
            return True
    return False


@lru_cache(maxsize=1)
def _load_embedding_model():
    try:
        from sentence_transformers import SentenceTransformer
    except Exception:
        return None

    try:
        return SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        return None


def _normalize(value: str) -> str:
    return " ".join((value or "").strip().lower().split())
