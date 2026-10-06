"""Build the synthetic data kit: documents, Source Register CSV, rule registry CSV, student CSVs and the data card."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from app.sources.registry import REGISTER_COLUMNS
from app.synthetic.corpus import DOCS, GOLD_RULES
from app.synthetic.render import write_all
from app.synthetic.students import Dataset, dataset_csvs, generate

RULE_COLUMNS = ["rule_id", "description", "parameter", "operator", "value", "scope_programmes", "scope_batches", "effective_from", "effective_to",
                "source_doc_id", "source_section", "unit"]


def register_csv(retrieved_on: str = "2026-10-05") -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=REGISTER_COLUMNS, lineterminator="\n")
    w.writeheader()
    for d in DOCS:
        w.writerow({"doc_id": d.doc_id, "title": d.title, "issuer": d.issuer, "authority_level": d.authority_level, "doc_type": d.doc_type,
                    "version": d.version, "effective_from": d.effective_from, "effective_to": d.effective_to, "supersedes": d.supersedes,
                    "scope_programmes": d.scope_programmes, "scope_batches": d.scope_batches, "provenance": d.provenance,
                    "retrieved_on": retrieved_on, "synthetic": "Y"})
    return buf.getvalue()


def rules_csv() -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=RULE_COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in GOLD_RULES:
        row = {c: r.get(c, "") for c in RULE_COLUMNS}
        row["scope_programmes"] = row["scope_programmes"] or "ALL"
        row["scope_batches"] = row["scope_batches"] or "ALL"
        w.writerow(row)
    return buf.getvalue()


def data_card(ds: Dataset, seed: int) -> str:
    progs: dict[str, int] = {}
    for s in ds.students:
        progs[s.programme] = progs.get(s.programme, 0) + 1
    fails = sum(1 for r in ds.results if r["result"] != "PASS")
    lines = [
        "# Data card: synthetic university kit", "",
        "## What this is",
        "A fictional institute (Meridian Institute of Technology) with 10 documents, 80 courses and 60 students, built to exercise the decision engine.",
        "Every document is marked `synthetic = Y` in the Source Register. None is a real university document and none was copied from one.", "",
        "## How it was produced",
        f"- Documents: template text in `app/synthetic/corpus.py`, rendered by `app/synthetic/render.py` (PDF, DOCX, Markdown, one image-only scanned PDF).",
        f"- Students, attendance and results: `app/synthetic/students.py`, seeded random generator (seed {seed}); the same seed gives identical files.",
        "- No language model was used to generate this data. `prompts/synthetic_students_prompt.txt` documents the prompt to use if the data is regenerated with an LLM instead.",
        "- Validation: `python scripts/validate_synthetic.py` checks referential integrity, mark sums, pass/fail consistency, backlog counts and CGPA recomputation.", "",
        "## Contents",
        f"- Students: {len(ds.students)} ({', '.join(f'{k}: {v}' for k, v in sorted(progs.items()))}); courses: {len(ds.courses)}; attendance rows: {len(ds.attendance)}; result rows: {len(ds.results)} ({fails} not PASS).",
        "- Designed edge-case students (IDs S1001 to S1010, see `data/designed_students.json`) sit exactly on decision boundaries.", "",
        "## Known limitations",
        "- Names are drawn from a fixed list; they are fictional and may coincide with real names by chance.",
        "- Grade, attendance and marks distributions are plausible, not calibrated to any real cohort.",
        "- The policy corpus is small and tidy. Real documents are longer, messier and contain scanned tables; swap them in using the same Source Register columns.",
        "- Rules in `data/rule_registry.csv` were written by hand to match the documents; the evaluation also measures how many the automatic extractor recovers.",
    ]
    return "\n".join(lines) + "\n"


def build_all(out: Path, seed: int = 20261006) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    paths = write_all(DOCS, out / "documents")
    (out / "source_register.csv").write_text(register_csv(), encoding="utf-8")
    (out / "rule_registry.csv").write_text(rules_csv(), encoding="utf-8")
    ds = generate(seed)
    for name, text in dataset_csvs(ds).items():
        (out / name).write_text(text, encoding="utf-8")
    (out / "designed_students.json").write_text(json.dumps(
        {sid: {"scenario": note, "record_only_cgpa": sid in ds.cgpa_record_only} for sid, note in ds.designed.items()}, indent=2), encoding="utf-8")
    (out / "DATA_CARD.md").write_text(data_card(ds, seed), encoding="utf-8")
    return {"documents": [p.name for p in paths], "students": len(ds.students), "courses": len(ds.courses),
            "attendance_rows": len(ds.attendance), "result_rows": len(ds.results)}
