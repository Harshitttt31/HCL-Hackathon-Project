"""Deterministic calculation engine.

ALL arithmetic in the system happens here (or in the eligibility tools that call it).
The LLM never computes a number. Comparisons are EXACT (fractions), never done on rounded values:
2999/4000 is 74.975%, displayed as 74.98 but still below a 75% threshold.
"""
from __future__ import annotations

import math
import operator as _op
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any, Iterable, Optional, Sequence

from pydantic import BaseModel, ConfigDict


class CalcResult(BaseModel):
    """Uniform shape for any calculation: value + formula + inputs + tool (guide R5, spec section 11)."""

    model_config = ConfigDict(extra="forbid")

    value: Any
    formula: str
    inputs: dict[str, Any]
    tool: str
    exact: Optional[str] = None  # exact rational value, e.g. "3/4"
    note: Optional[str] = None


def to_fraction(value: Any) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if isinstance(value, float):
        return Fraction(str(value))  # decimal representation, avoids binary float artefacts
    return Fraction(str(value).strip())


def round_half_up(value: Fraction | Decimal | float, places: int = 2) -> float:
    q = Decimal(1).scaleb(-places)
    d = Decimal(value.numerator) / Decimal(value.denominator) if isinstance(value, Fraction) else Decimal(str(value))
    return float(d.quantize(q, rounding=ROUND_HALF_UP))


# ------------------------------------------------------------------------------------------------
# Attendance
# ------------------------------------------------------------------------------------------------
def attendance_fraction(classes_attended: int, classes_held: int) -> Fraction:
    if classes_held <= 0:
        raise ValueError("classes_held must be greater than 0")
    if classes_attended < 0 or classes_attended > classes_held:
        raise ValueError("classes_attended must satisfy 0 <= attended <= held")
    return Fraction(classes_attended * 100, classes_held)


def calculate_attendance(classes_attended: int, classes_held: int) -> CalcResult:
    exact = attendance_fraction(classes_attended, classes_held)
    return CalcResult(
        value=round_half_up(exact, 2),
        formula="classes_attended / classes_held * 100",
        inputs={"classes_attended": classes_attended, "classes_held": classes_held},
        tool="attendance_calculator",
        exact=str(exact),
    )


def classes_needed_to_reach(classes_attended: int, classes_held: int, threshold_pct: Any) -> CalcResult:
    """Smallest n >= 0 such that (attended + n) / (held + n) * 100 >= threshold, attending every one of the next n classes."""
    t = to_fraction(threshold_pct)
    a, h = classes_attended, classes_held
    formula = "smallest n >= 0 with (classes_attended + n) * 100 >= threshold_pct * (classes_held + n)"
    inputs = {"classes_attended": a, "classes_held": h, "threshold_pct": float(t)}
    if a * 100 >= t * h:
        return CalcResult(value=0, formula=formula, inputs=inputs, tool="attendance_projection", note="already at or above the threshold")
    if t >= 100:
        return CalcResult(value=None, formula=formula, inputs=inputs, tool="attendance_projection",
                          note="a threshold of 100% cannot be reached once a class has been missed")
    n = math.ceil((t * h - 100 * a) / (100 - t))
    return CalcResult(value=int(n), formula=formula, inputs=inputs, tool="attendance_projection")


def classes_can_miss(classes_attended: int, classes_held: int, threshold_pct: Any, future_classes: int = 0) -> CalcResult:
    """Largest m >= 0 of the next `future_classes` classes that can be missed while staying at/above the threshold."""
    t = to_fraction(threshold_pct)
    a, h, f = classes_attended, classes_held, max(0, future_classes)
    # attended stays a + (f - m); held is h + f; need (a + f - m) * 100 >= t * (h + f)
    slack = Fraction((a + f) * 100) - t * (h + f)
    m = math.floor(slack / 100) if slack >= 0 else None
    m = min(m, f) if m is not None else None
    return CalcResult(
        value=m, formula="largest m with (classes_attended + future - m) * 100 >= threshold_pct * (classes_held + future)",
        inputs={"classes_attended": a, "classes_held": h, "future_classes": f, "threshold_pct": float(t)},
        tool="attendance_projection",
    )


# ------------------------------------------------------------------------------------------------
# Threshold comparison (exact)
# ------------------------------------------------------------------------------------------------
_OPS = {">=": _op.ge, ">": _op.gt, "<=": _op.le, "<": _op.lt, "==": _op.eq, "=": _op.eq, "!=": _op.ne}


def compare(actual: Any, operator: str, threshold: Any) -> bool:
    """Exact comparison. `between` takes threshold as 'low-high' or (low, high) and is inclusive."""
    op = operator.strip().lower()
    a = to_fraction(actual)
    if op == "between":
        if isinstance(threshold, str):
            low, high = [to_fraction(x) for x in threshold.replace("to", "-").split("-", 1)]
        else:
            low, high = (to_fraction(threshold[0]), to_fraction(threshold[1]))
        return low <= a <= high
    if op not in _OPS:
        raise ValueError(f"unsupported operator '{operator}'")
    return bool(_OPS[op](a, to_fraction(threshold)))


# ------------------------------------------------------------------------------------------------
# Marks, grades, CGPA
# ------------------------------------------------------------------------------------------------
def marks_percentage(total_marks: int, max_marks: int) -> Fraction:
    if max_marks <= 0:
        raise ValueError("max_marks must be greater than 0")
    return Fraction(total_marks * 100, max_marks)


def grade_point_for(percentage: Fraction, bands: Sequence[dict]) -> Optional[tuple[str, Fraction]]:
    """bands: [{'grade': 'O', 'min': 90, 'max': 100, 'points': 10}, ...]

    Bands are defined by their lower bound (inclusive): the highest band whose min <= percentage applies.
    This avoids gaps such as 89.5 falling between "80-89" and "90-100".
    """
    for band in sorted(bands, key=lambda b: -float(b["min"])):
        if percentage >= to_fraction(band["min"]):
            return str(band["grade"]), to_fraction(band["points"])
    return None


def calculate_cgpa(entries: Iterable[tuple[str, int, Fraction]]) -> CalcResult:
    """CGPA = sum(credits * grade_points) / sum(credits), rounded half-up to 2 decimals.

    entries: (course_code, credits, grade_points)
    """
    rows = list(entries)
    total_credits = sum(c for _, c, _ in rows)
    if not rows or total_credits <= 0:
        raise ValueError("no credit-bearing results to compute a CGPA from")
    weighted = sum(Fraction(c) * g for _, c, g in rows)
    exact = weighted / total_credits
    return CalcResult(
        value=round_half_up(exact, 2),
        formula="sum(credits * grade_points) / sum(credits)",
        inputs={"courses": [{"course_code": code, "credits": c, "grade_points": float(g)} for code, c, g in rows],
                "total_credits": total_credits},
        tool="cgpa_calculator",
        exact=str(exact),
    )
