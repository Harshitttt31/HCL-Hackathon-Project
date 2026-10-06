"""Optional reranking. Relevance only: authority is applied later by the precedence engine, never here."""
from __future__ import annotations

from typing import Optional, Protocol

from app.core.config import get_settings
from app.core.logging import get_logger
from app.rag.text import expand_terms, tokenize

log = get_logger(__name__)


class Reranker(Protocol):
    name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


class NoReranker:
    name = "none"

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [0.0] * len(texts)


class LexicalReranker:
    """Coverage of query terms (and synonyms) + adjacent-pair (phrase) matches. Deterministic and fast."""

    name = "lexical"

    def score(self, query: str, texts: list[str]) -> list[float]:
        q = tokenize(query)
        if not q:
            return [0.0] * len(texts)
        q_set = set(q)
        q_pairs = set(zip(q, q[1:]))
        out = []
        for text in texts:
            toks = tokenize(text)
            t_set = set(expand_terms(toks)) | set(toks)
            cover = sum(1 for t in q_set if t in t_set or any(v in t_set for v in expand_terms([t]))) / len(q_set)
            pairs = set(zip(toks, toks[1:]))
            phrase = (len(q_pairs & pairs) / len(q_pairs)) if q_pairs else 0.0
            out.append(0.7 * cover + 0.3 * phrase)
        return out


class CrossEncoderReranker:
    def __init__(self, model_name: str):
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name)
        self.name = f"cross-encoder:{model_name}"

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        raw = self._model.predict([(query, t) for t in texts])
        return [float(x) for x in raw]


def get_reranker(backend: Optional[str] = None) -> Reranker:
    s = get_settings()
    backend = backend or s.reranker_backend
    if backend == "none":
        return NoReranker()
    if backend == "cross-encoder":
        try:
            return CrossEncoderReranker(s.cross_encoder_model)
        except Exception as exc:
            log.warning("reranker_fallback", extra={"reason": str(exc)[:200], "using": "lexical"})
    return LexicalReranker()
