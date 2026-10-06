"""Temporal validity (Annex A step 1).

A document/rule is effective on as_of when:  effective_from <= as_of  AND  (effective_to is empty OR effective_to >= as_of)
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional, Union

DateLike = Union[str, date, None]
Window = Literal["effective", "upcoming", "expired"]


def to_date(value: DateLike) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text)


def window_status(effective_from: DateLike, effective_to: DateLike, as_of: date) -> Window:
    start = to_date(effective_from)
    end = to_date(effective_to)
    if start is not None and start > as_of:
        return "upcoming"
    if end is not None and end < as_of:
        return "expired"
    return "effective"


def is_effective(effective_from: DateLike, effective_to: DateLike, as_of: date) -> bool:
    return window_status(effective_from, effective_to, as_of) == "effective"


def intersect_windows(a_from: DateLike, a_to: DateLike, b_from: DateLike, b_to: DateLike) -> tuple[Optional[date], Optional[date]]:
    """Intersection of two validity windows (a rule is valid only while its document is valid)."""
    starts = [d for d in (to_date(a_from), to_date(b_from)) if d is not None]
    ends = [d for d in (to_date(a_to), to_date(b_to)) if d is not None]
    return (max(starts) if starts else None, min(ends) if ends else None)
