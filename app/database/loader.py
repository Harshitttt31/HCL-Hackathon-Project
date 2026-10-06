"""CSV loaders for the Annex C tables (the "test-student loader" required by the guide).

Each loader validates every row, loads the valid ones, and reports the invalid ones with their line number.
Load order matters because of foreign keys: courses, students, attendance, results; rules need their source documents.
Nothing here builds SQL from the data: rows go through the repositories' fixed parameterized statements.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field, ValidationError

from app.core.logging import get_logger
from app.database.models import AttendanceRow, CourseRow, ResultRow, RuleRow, StudentRow
from app.database.repositories import AttendanceRepo, CourseRepo, ResultRepo, RuleRepo, StudentRepo
from app.database.sqlite import init_db
from app.sources.registry import get_register

log = get_logger(__name__)


class LoadReport(BaseModel):
    table: str
    rows_read: int = 0
    rows_loaded: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k is None:
            continue
        key = k.strip().lower().lstrip("﻿")
        val = v.strip() if isinstance(v, str) else v
        out[key] = None if val in ("", None) else val
    return out


def _load(table: str, data: bytes | str, build: Callable[[dict[str, Any]], Any], save: Callable[[Any], None]) -> LoadReport:
    init_db()
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    report = LoadReport(table=table)
    reader = csv.DictReader(io.StringIO(text))
    for line_no, raw in enumerate(reader, start=2):  # line 1 is the header
        report.rows_read += 1
        try:
            row = build(_clean(raw))
            save(row)
            report.rows_loaded += 1
        except ValidationError as exc:
            report.errors.append({"line": line_no, "error": "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())[:300]})
        except Exception as exc:
            report.errors.append({"line": line_no, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    return report


def load_courses(data: bytes | str) -> LoadReport:
    repo = CourseRepo()
    return _load("courses", data, lambda r: CourseRow(**{**r, "course_code": (r.get("course_code") or "").upper()}), repo.upsert)


def load_students(data: bytes | str) -> LoadReport:
    repo = StudentRepo()
    return _load("students", data, lambda r: StudentRow(**{**r, "student_id": (r.get("student_id") or "").upper()}), repo.upsert)


def load_attendance(data: bytes | str) -> LoadReport:
    repo = AttendanceRepo()
    return _load("attendance", data, lambda r: AttendanceRow(**{**r, "student_id": (r.get("student_id") or "").upper(),
                                                                "course_code": (r.get("course_code") or "").upper()}), repo.upsert)


def load_results(data: bytes | str) -> LoadReport:
    repo = ResultRepo()
    return _load("results", data, lambda r: ResultRow(**{**r, "student_id": (r.get("student_id") or "").upper(),
                                                         "course_code": (r.get("course_code") or "").upper(),
                                                         "exam_type": (r.get("exam_type") or "").upper(),
                                                         "result": (r.get("result") or "").upper()}), repo.upsert)


def _rule_from(r: dict[str, Any]) -> RuleRow:
    attrs = r.get("attributes")
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    return RuleRow(**{**r, "attributes": attrs, "scope_programmes": r.get("scope_programmes") or "ALL",
                      "scope_batches": r.get("scope_batches") or "ALL", "unit": r.get("unit") or "", "origin": "curated"})


def load_rules(data: bytes | str) -> LoadReport:
    repo = RuleRepo()
    reg = get_register()

    def save(row: RuleRow) -> None:
        if not reg.exists(row.source_doc_id):
            raise ValueError(f"source_doc_id '{row.source_doc_id}' is not in the Source Register")
        repo.upsert(row)

    return _load("rule_registry", data, _rule_from, save)


def load_all(files: dict[str, bytes | str]) -> list[LoadReport]:
    """files: any of courses, students, attendance, results, rules (keys); loaded in dependency order."""
    order = [("courses", load_courses), ("students", load_students), ("attendance", load_attendance),
             ("results", load_results), ("rules", load_rules)]
    return [fn(files[name]) for name, fn in order if name in files and files[name]]
