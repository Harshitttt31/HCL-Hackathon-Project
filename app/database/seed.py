"""Seed a database from a data directory: ingest every document with its Source Register row, then load rules and student CSVs.

Used by scripts/seed_db.py, the Docker entrypoint, the evaluation harness and the tests.
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any, Optional

from app.core.logging import get_logger
from app.database import loader
from app.database.sqlite import init_db
from app.rag.ingestion import IngestionService
from app.rag.retriever import HybridRetriever
from app.sources.models import SourceMetadata

log = get_logger(__name__)


def seed_database(data_dir: Path, retriever: Optional[HybridRetriever] = None, strategy: Optional[str] = None,
                  load_students: bool = True) -> dict[str, Any]:
    init_db()
    retriever = retriever or HybridRetriever()
    ingestion = IngestionService(retriever)
    report: dict[str, Any] = {"documents": [], "loads": []}
    t0 = time.perf_counter()
    with open(data_dir / "source_register.csv", newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        meta = SourceMetadata(**{k: v for k, v in row.items() if k})
        path = next((p for p in (data_dir / "documents").iterdir() if p.stem == meta.doc_id), None)
        if path is None:
            report["documents"].append({"doc_id": meta.doc_id, "status": "missing_file"})
            continue
        res = ingestion.ingest(path.read_bytes(), path.name, meta, strategy)
        report["documents"].append({"doc_id": meta.doc_id, "status": res.status, "chunks": res.chunks_indexed, "rules_extracted": res.rules_registered,
                                    "ocr": res.ocr_used, "warnings": res.warnings})
    files: dict[str, bytes] = {}
    names = {"courses": "courses.csv", "students": "students.csv", "attendance": "attendance.csv", "results": "results.csv", "rules": "rule_registry.csv"}
    for key, fname in names.items():
        if key in ("students", "attendance", "results", "courses") and not load_students:
            continue
        p = data_dir / fname
        if p.exists():
            files[key] = p.read_bytes()
    report["loads"] = [r.model_dump() for r in loader.load_all(files)]
    retriever.reload()
    report["seconds"] = round(time.perf_counter() - t0, 1)
    return report
