"""Validation of the generated kit. Exit status 0 only if every check passes."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.synthetic.students import cgpa_from, grade_points


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def validate_dir(data_dir: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    students = {r["student_id"]: r for r in _rows(data_dir / "students.csv")}
    courses = {r["course_code"]: r for r in _rows(data_dir / "courses.csv")}
    attendance = _rows(data_dir / "attendance.csv")
    results = _rows(data_dir / "results.csv")
    designed = json.loads((data_dir / "designed_students.json").read_text())

    check("student ids are S plus four digits and unique", all(len(k) == 5 and k[0] == "S" and k[1:].isdigit() for k in students) and len(students) == len(_rows(data_dir / "students.csv")))
    check("every attendance row references a student and a course", all(a["student_id"] in students and a["course_code"] in courses for a in attendance))
    check("every result row references a student and a course", all(r["student_id"] in students and r["course_code"] in courses for r in results))
    bad_att = [a for a in attendance if not (0 <= int(a["classes_attended"]) <= int(a["classes_held"]) and int(a["classes_held"]) > 0)]
    check("0 <= classes_attended <= classes_held, classes_held > 0", not bad_att, f"{len(bad_att)} bad rows")
    bad_sum = [r for r in results if r["total_marks"] and r["internal_marks"] and r["external_marks"] and int(r["total_marks"]) != int(r["internal_marks"]) + int(r["external_marks"])]
    check("total_marks = internal_marks + external_marks", not bad_sum, f"{len(bad_sum)} bad rows")
    bad_pf = [r for r in results if r["total_marks"] and ((int(r["total_marks"]) >= 40) != (r["result"] == "PASS"))]
    check("PASS exactly when total marks >= 40 (pass mark 40%)", not bad_pf, f"{len(bad_pf)} bad rows")
    check("result values are allowed", all(r["result"] in ("PASS", "FAIL", "ABSENT", "DETAINED") for r in results))
    keys = [(r["student_id"], r["course_code"], r["exam_session"], r["exam_type"]) for r in results]
    check("results primary key is unique", len(keys) == len(set(keys)))
    att_keys = [(a["student_id"], a["course_code"]) for a in attendance]
    check("attendance primary key is unique", len(att_keys) == len(set(att_keys)))

    latest: dict[tuple[str, str], tuple] = {}
    mon = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
    for r in results:
        y, m = int(r["exam_session"][:4]), mon[r["exam_session"][5:8]]
        k = (y, m, 1 if r["exam_type"] == "SUPPLEMENTARY" else 0)
        cur = latest.get((r["student_id"], r["course_code"]))
        if cur is None or k > cur[0]:
            latest[(r["student_id"], r["course_code"])] = (k, r)
    backlog_bad, cgpa_bad = [], []
    entries: dict[str, list[tuple[int, int]]] = defaultdict(list)
    backlogs: dict[str, int] = defaultdict(int)
    for (sid, code), (_, r) in latest.items():
        credits = int(courses[code]["credits"])
        passed = r["result"] == "PASS"
        entries[sid].append((credits, grade_points(int(r["total_marks"])) if passed else 0))
        backlogs[sid] += 0 if passed else 1
    for sid, s in students.items():
        if int(s["active_backlogs"]) != backlogs.get(sid, 0):
            backlog_bad.append(sid)
        exp = cgpa_from(entries.get(sid, []))
        stored = float(s["cgpa"]) if s["cgpa"] else None
        if sid in designed and designed[sid]["record_only_cgpa"]:
            continue
        if (exp is None) != (stored is None) or (exp is not None and abs(exp - stored) > 0.0051):
            cgpa_bad.append(sid)
    check("active_backlogs equals the number of courses whose latest attempt is not PASS", not backlog_bad, str(backlog_bad[:5]))
    check("stored CGPA equals the recomputed credit-weighted CGPA (except record-only students)", not cgpa_bad, str(cgpa_bad[:5]))

    def pct(sid: str, code: str) -> float:
        a = next(a for a in attendance if a["student_id"] == sid and a["course_code"] == code)
        return int(a["classes_attended"]) * 100 / int(a["classes_held"])

    check("boundary: S1001 CS301 = 77.5, S1001 CS302 = 80 exactly", pct("S1001", "CS301") == 77.5 and pct("S1001", "CS302") == 80.0)
    check("boundary: S1002 CS302 = 65 exactly", pct("S1002", "CS302") == 65.0)
    check("boundary: S1003 EC401 = 75 exactly", pct("S1003", "EC401") == 75.0)
    check("boundary: S1004 CS201 = 44/59 (74.576...)", round(pct("S1004", "CS201"), 3) == 74.576)
    check("boundary: S1005 MC201 = 75 exactly", pct("S1005", "MC201") == 75.0)
    check("boundary: S1008 CS301 = 60", pct("S1008", "CS301") == 60.0)
    check("boundary: S1002 has CGPA 6.5 and exactly 1 active backlog", float(students["S1002"]["cgpa"]) == 6.5 and int(students["S1002"]["active_backlogs"]) == 1)
    s7 = [r for r in results if r["student_id"] == "S1007" and r["course_code"] == "CS204"]
    check("boundary: S1007 CS204 has 3 attempts, all FAIL", len(s7) == 3 and all(r["result"] == "FAIL" for r in s7))
    check("boundary: S1006 has 2 designed backlogs among its active backlogs", int(students["S1006"]["active_backlogs"]) >= 2)

    reg = _rows(data_dir / "source_register.csv")
    check("source register has 10 rows with synthetic = Y", len(reg) == 10 and all(r["synthetic"] == "Y" for r in reg))
    ids = {r["doc_id"] for r in reg}
    rules = _rows(data_dir / "rule_registry.csv")
    check("every rule points at a registered document", all(r["source_doc_id"] in ids for r in rules))
    check("every document file exists", all(any(p.stem == i for p in (data_dir / "documents").iterdir()) for i in ids))
    ok = all(c["passed"] for c in checks)
    return {"passed": ok, "checks_run": len(checks), "checks_failed": sum(1 for c in checks if not c["passed"]), "checks": checks}
