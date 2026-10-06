"""Answer validation. The final text (template or LLM-polished) must not introduce anything the tools did not produce."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable

from app.core.security import ID_IN_TEXT_RE

_NUM_RE = re.compile(r"(?<![A-Za-z0-9])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
_IDENT_RE = re.compile(r"\b([A-Z]{2,}[A-Z0-9]*(?:-[A-Z0-9]+)+|[A-Z]{2,4}\d{3}[A-Z]?)\b")
_NEG_RE = re.compile(r"\b(not eligible|ineligible|not allowed|not permitted|cannot appear|can't appear|not able to appear|barred|detained|debarred)\b", re.IGNORECASE)
_POS_RE = re.compile(r"\b(eligible|allowed|permitted|may appear|can appear)\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)


def _canon_number(raw: str) -> str:
    raw = raw.replace(",", "")
    try:
        return format(Decimal(raw).normalize(), "f")
    except InvalidOperation:
        return raw


def extract_tokens(text: str) -> set[str]:
    """Numbers (canonical form) and identifiers (rule ids, document ids, course codes) found in a text."""
    out: set[str] = {_canon_number(m.group(1)) for m in _NUM_RE.finditer(text or "")}
    out |= {m.group(1).upper() for m in _IDENT_RE.finditer(text or "")}
    return out


def polarity(text: str) -> str | None:
    if _NEG_RE.search(text or ""):
        return "neg"
    if _POS_RE.search(text or ""):
        return "pos"
    return None


@dataclass
class ValidationResult:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)
    unsupported_identifiers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "issues": self.issues, "unsupported_numbers": self.unsupported_numbers,
                "unsupported_identifiers": self.unsupported_identifiers}


def validate_answer(candidate: str, draft: str, allowed: Iterable[str], question: str = "", max_chars: int = 2500) -> ValidationResult:
    """candidate: text to be returned; draft: the deterministic template; allowed: tokens legitimately available."""
    res = ValidationResult()
    if not candidate or not candidate.strip():
        res.ok, res.issues = False, ["empty answer"]
        return res
    if len(candidate) > max_chars:
        res.issues.append("answer too long")
    allowed_set = {a.upper() if not a.replace(".", "").isdigit() else a for a in allowed}
    allowed_set |= extract_tokens(draft) | extract_tokens(question)
    tokens = extract_tokens(candidate)
    nums = sorted(t for t in tokens if t.replace(".", "").isdigit() and t not in allowed_set)
    idents = sorted(t for t in tokens if not t.replace(".", "").isdigit() and t.upper() not in allowed_set)
    if nums:
        res.unsupported_numbers = nums
        res.issues.append(f"numbers not produced by any tool: {', '.join(nums[:6])}")
    if idents:
        res.unsupported_identifiers = idents
        res.issues.append(f"identifiers not produced by any tool: {', '.join(idents[:6])}")
    if ID_IN_TEXT_RE.search(candidate) and not ID_IN_TEXT_RE.search(draft):
        res.issues.append("student identifier in the answer")
    if _URL_RE.search(candidate) and not _URL_RE.search(draft):
        res.issues.append("link not present in the evidence")
    pd, pc = polarity(draft), polarity(candidate)
    if pd is not None and pc is not None and pd != pc:
        res.issues.append(f"eligibility polarity changed ({pd} -> {pc})")
    if pd is not None and pc is None and "eligib" in draft.lower():
        res.issues.append("eligibility verdict missing")
    res.ok = not res.issues
    return res
