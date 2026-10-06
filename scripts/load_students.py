"""Load CSV files in the Annex C schema: python scripts/load_students.py --students s.csv --attendance a.csv --results r.csv --courses c.csv --rules r.csv"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import loader  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    for k in ("courses", "students", "attendance", "results", "rules"):
        ap.add_argument(f"--{k}")
    a = ap.parse_args()
    files = {k: Path(getattr(a, k)).read_bytes() for k in ("courses", "students", "attendance", "results", "rules") if getattr(a, k)}
    reports = loader.load_all(files)
    for r in reports:
        print(f"{r.table:14s} loaded {r.rows_loaded}/{r.rows_read}")
        for e in r.errors[:10]:
            print(f"   line {e['line']}: {e['error']}")
    sys.exit(1 if any(r.errors for r in reports) else 0)
