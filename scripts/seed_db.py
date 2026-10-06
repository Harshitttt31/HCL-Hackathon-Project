"""Create the SQLite database, ingest the documents in data/documents (with data/source_register.csv), load rules and students."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.database.seed import seed_database  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--chunking", choices=["structure", "fixed"], default=None)
    ap.add_argument("--no-students", action="store_true", help="documents and rules only")
    a = ap.parse_args()
    configure_logging("WARNING")
    rep = seed_database(Path(a.data), strategy=a.chunking, load_students=not a.no_students)
    for d in rep["documents"]:
        print(f"{d['doc_id']:22s} {d['status']:10s} chunks={d.get('chunks', 0):3d} extracted_rules={d.get('rules_extracted', 0):2d} ocr={d.get('ocr', False)}")
        for w in d.get("warnings", []):
            print(f"    warning: {w}")
    for l in rep["loads"]:
        print(f"{l['table']:14s} loaded {l['rows_loaded']}/{l['rows_read']}" + (f" errors={l['errors'][:3]}" if l["errors"] else ""))
    print(f"done in {rep['seconds']} s, database: {get_settings().sqlite_path}")
