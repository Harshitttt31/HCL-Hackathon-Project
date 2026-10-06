"""LLM-backed helpers. Both are optional, validated, and have a deterministic fallback."""
from __future__ import annotations

from typing import Optional

from app.agent.llm import LLMClient
from app.agent.prompts import load_prompt
from app.agent.state import INTENTS
from app.agent.validator import ValidationResult, validate_answer


def polish_answer(llm: LLMClient, question: str, draft: str, allowed_facts: list[str]) -> tuple[str, bool, Optional[ValidationResult]]:
    """Return (final_text, llm_text_used, validation). The draft is returned unless the LLM text passes validation."""
    user = f"<question>{question[:600]}</question>\n<draft>{draft}</draft>"
    obj = llm.complete_json(load_prompt("polish_system.txt"), user)
    if not obj or not isinstance(obj.get("answer"), str):
        return draft, False, None
    candidate = obj["answer"].strip()
    if candidate == draft.strip():
        return draft, False, ValidationResult()
    result = validate_answer(candidate, draft, allowed_facts, question)
    if result.ok:
        return candidate, True, result
    return draft, False, result


def llm_intent(llm: LLMClient, question: str) -> Optional[tuple[str, float]]:
    obj = llm.complete_json(load_prompt("intent_system.txt"), f"<question>{question[:600]}</question>")
    if not obj:
        return None
    intent = obj.get("intent")
    try:
        conf = float(obj.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    if intent in INTENTS and 0.0 <= conf <= 1.0:
        return str(intent), conf
    return None
