"""Programme and batch applicability (Annex A step 1: "programme and batch scope covers the student").

Scope strings come from the Source Register / rule registry:
  scope_programmes:  "ALL" | "B.Tech" | "B.Tech CSE; M.Tech"
  scope_batches:     "ALL" | "2023+" | "2021-2023" | "2024" | "<=2022" | "2021; 2023+"

Matching is tri-state because the context may be incomplete (e.g. an anonymous policy question):
  "yes"      the scope covers the context
  "no"       the scope definitely excludes the context
  "unknown"  the context lacks the information to decide (or the scope is narrower than the context)
"""
from __future__ import annotations

import re
from typing import Optional

INF = 10**9
_SPLIT_RE = re.compile(r"[;,]")
_ALL = {"all", "*", "any", ""}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def split_programmes(spec: str) -> list[str]:
    return [p.strip() for p in _SPLIT_RE.split(spec or "") if p.strip()]


def is_all(spec: Optional[str]) -> bool:
    return (spec or "").strip().lower() in _ALL


def programme_match(spec: Optional[str], programme: Optional[str]) -> str:
    """Tri-state programme match.

    A scope token matches when the student's programme starts with it ("B.Tech" covers "B.Tech CSE").
    If the *context* is the broader term ("B.Tech") and the scope token is narrower ("B.Tech CSE"),
    the result is "unknown": the document applies to only some B.Tech students.
    """
    if is_all(spec):
        return "yes"
    if not programme:
        return "unknown"
    p = _norm(programme)
    saw_narrower = False
    for token in split_programmes(spec or ""):
        t = _norm(token)
        if not t:
            continue
        if t in ("all", "any"):
            return "yes"
        if p.startswith(t):
            return "yes"
        if t.startswith(p):
            saw_narrower = True
    return "unknown" if saw_narrower else "no"


def parse_batch_spec(spec: Optional[str]) -> list[tuple[int, int]]:
    """Parse a batch scope into inclusive (low, high) ranges. Raises ValueError on malformed input."""
    if is_all(spec):
        return [(-INF, INF)]
    ranges: list[tuple[int, int]] = []
    for raw in _SPLIT_RE.split(spec or ""):
        token = raw.strip().replace("–", "-").replace("\u2014", "-")
        if not token:
            continue
        low_open = re.fullmatch(r"(\d{4})\s*\+", token)
        upto = re.fullmatch(r"(?:<=|<|up\s*to\s*)\s*(\d{4})", token, re.IGNORECASE)
        trail = re.fullmatch(r"(\d{4})\s*-", token)
        rng = re.fullmatch(r"(\d{4})\s*-\s*(\d{4})", token)
        single = re.fullmatch(r"(\d{4})", token)
        if token.lower() in _ALL or token.lower() == "all":
            ranges.append((-INF, INF))
        elif low_open:
            ranges.append((int(low_open.group(1)), INF))
        elif upto:
            bound = int(upto.group(1))
            ranges.append((-INF, bound - 1 if token.startswith("<") and not token.startswith("<=") else bound))
        elif trail:
            ranges.append((-INF, int(trail.group(1))))
        elif rng:
            lo, hi = int(rng.group(1)), int(rng.group(2))
            if lo > hi:
                raise ValueError(f"batch range '{token}' is reversed")
            ranges.append((lo, hi))
        elif single:
            ranges.append((int(single.group(1)), int(single.group(1))))
        else:
            raise ValueError(f"unrecognised batch scope '{token}' (use ALL, 2023, 2023+, 2021-2023, <=2022)")
    if not ranges:
        raise ValueError("empty batch scope")
    return ranges


def batch_match(spec: Optional[str], batch_year: Optional[int]) -> str:
    if is_all(spec):
        return "yes"
    if batch_year is None:
        return "unknown"
    try:
        ranges = parse_batch_spec(spec)
    except ValueError:
        return "unknown"
    return "yes" if any(lo <= batch_year <= hi for lo, hi in ranges) else "no"


def scope_match(scope_programmes: Optional[str], scope_batches: Optional[str],
                programme: Optional[str], batch_year: Optional[int]) -> str:
    """Combine programme and batch results: any "no" excludes; any "unknown" makes the result uncertain."""
    a = programme_match(scope_programmes, programme)
    b = batch_match(scope_batches, batch_year)
    if "no" in (a, b):
        return "no"
    if "unknown" in (a, b):
        return "unknown"
    return "yes"


def describe_scope(scope_programmes: Optional[str], scope_batches: Optional[str]) -> str:
    prog = "all programmes" if is_all(scope_programmes) else (scope_programmes or "").strip()
    batch = "all batches" if is_all(scope_batches) else f"batches {(scope_batches or '').strip()}"
    return f"{prog}, {batch}"


_GENERIC_PROGRAMMES = [
    (r"\bb\.?\s?tech\b", "B.Tech"), (r"\bm\.?\s?tech\b", "M.Tech"), (r"\bb\.?\s?sc\b", "B.Sc"),
    (r"\bm\.?\s?sc\b", "M.Sc"), (r"\bmba\b", "MBA"), (r"\bbba\b", "BBA"), (r"\bmca\b", "MCA"),
    (r"\bbca\b", "BCA"), (r"\bph\.?\s?d\b", "Ph.D"),
]


def extract_programme_from_text(text: str, known_programmes: list[str]) -> Optional[str]:
    """Find a programme mention in a (public) question, e.g. 'for B.Tech' -> 'B.Tech'.

    A known full programme name ("B.Tech CSE") wins over a generic degree name ("B.Tech").
    """
    squashed = _norm(text)
    for prog in sorted(set(known_programmes), key=len, reverse=True):
        key = _norm(prog)
        if len(key) >= 6 and key in squashed:
            return prog
    low = text.lower()
    for pattern, name in _GENERIC_PROGRAMMES:
        if re.search(pattern, low):
            return name
    return None


def extract_batch_from_text(text: str) -> Optional[int]:
    """'2024 batch', 'batch of 2023', 'admitted in 2022' -> year. Only explicit mentions."""
    for pat in (r"\b(20\d{2})\s*(?:batch|admission|intake|cohort)\b",
                r"\b(?:batch|class|cohort)\s*(?:of|:)?\s*(20\d{2})\b",
                r"\badmitted\s+(?:in\s+)?(20\d{2})\b",
                r"\bjoined\s+(?:in\s+)?(20\d{2})\b"):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None
