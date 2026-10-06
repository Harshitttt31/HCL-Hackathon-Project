from fractions import Fraction

import pytest

from app.tools.calculator import (
    calculate_attendance, calculate_cgpa, classes_can_miss, classes_needed_to_reach, compare, grade_point_for, marks_percentage,
    round_half_up,
)


def test_attendance_matches_guide_example():
    r = calculate_attendance(31, 40)
    assert r.value == 77.5 and r.tool == "attendance_calculator"
    assert r.formula == "classes_attended / classes_held * 100"
    assert r.inputs == {"classes_attended": 31, "classes_held": 40}


@pytest.mark.parametrize("a,h", [(-1, 10), (11, 10), (5, 0), (5, -3)])
def test_attendance_rejects_impossible_inputs(a, h):
    with pytest.raises(ValueError):
        calculate_attendance(a, h)


def test_comparison_is_exact_not_rounded():
    # 7500/10001 = 74.9925...% displays as 74.99 and must be BELOW 75, never rounded up to 75.0
    r = calculate_attendance(7500, 10001)
    assert r.value == 74.99
    assert compare(Fraction(r.exact), ">=", 75) is False
    # 2999/4000 = 74.975 displays as 74.98 (half-up) but is still below 75
    r2 = calculate_attendance(2999, 4000)
    assert r2.value == 74.97 or r2.value == 74.98
    assert compare(Fraction(r2.exact), ">=", 75) is False


def test_exactly_at_threshold_passes_for_ge_and_fails_for_gt():
    pct = Fraction(30 * 100, 40)
    assert compare(pct, ">=", 75) and not compare(pct, ">", 75)
    assert compare(80, "<=", "80") and not compare(80, "<", "80")


def test_compare_between_and_equality_and_errors():
    assert compare(70, "between", "65-75") and not compare(76, "between", "65-75")
    assert compare("7.0", "==", 7)
    with pytest.raises(ValueError):
        compare(1, "~~", 2)


def test_classes_needed_to_reach_threshold():
    # 31/40, need 80%: (31+n)/(40+n) >= 0.8  ->  n >= 5
    r = classes_needed_to_reach(31, 40, 80)
    assert r.value == 5
    a, h, n = 31, 40, r.value
    assert (a + n) * 100 >= 80 * (h + n) and (a + n - 1) * 100 < 80 * (h + n - 1)
    assert classes_needed_to_reach(40, 50, 80).value == 0
    assert classes_needed_to_reach(9, 10, 100).value is None


def test_classes_can_miss():
    r = classes_can_miss(40, 40, 75, future_classes=20)  # (40+20-m)*100 >= 75*60  ->  m <= 15
    assert r.value == 15
    assert classes_can_miss(30, 40, 75, future_classes=20).value == 5  # (50-m)*100 >= 4500 -> m <= 5
    assert classes_can_miss(10, 40, 75, future_classes=5).value is None


def test_cgpa_credit_weighted_and_half_up_rounding():
    entries = [("A", 4, Fraction(8)), ("B", 3, Fraction(9)), ("C", 3, Fraction(7))]
    r = calculate_cgpa(entries)
    assert r.value == round_half_up(Fraction(4 * 8 + 3 * 9 + 3 * 7, 10)) == 8.0
    assert r.formula == "sum(credits * grade_points) / sum(credits)"
    assert calculate_cgpa([("A", 1, Fraction(1)), ("B", 2, Fraction(1, 1))]).value == 1.0
    assert round_half_up(Fraction(1, 8)) == 0.13  # 0.125 -> 0.13 (half-up, not banker's)
    with pytest.raises(ValueError):
        calculate_cgpa([])


def test_grade_bands():
    bands = [{"grade": "O", "min": 90, "max": 100, "points": 10}, {"grade": "A", "min": 80, "max": 90, "points": 9},
             {"grade": "B", "min": 70, "max": 80, "points": 8}]
    assert grade_point_for(marks_percentage(90, 100), bands) == ("O", Fraction(10))  # boundary goes to the higher band
    assert grade_point_for(marks_percentage(79, 100), bands) == ("B", Fraction(8))
    assert grade_point_for(Fraction(10), bands) is None
