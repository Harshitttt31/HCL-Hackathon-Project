"""Ingest one document from the command line: python scripts/ingest.py FILE --meta metadata.json"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import configure_logging  # noqa: E402
from app.database.sqlite import init_db  # noqa: E402
from app.rag.ingestion import IngestionService  # noqa: E402
from app.rag.retriever import HybridRetriever  # noqa: E402
from app.sources.models import SourceMetadata  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--meta", required=True, help="JSON file with the Source Register fields (Annex B)")
    a = ap.parse_args()
    configure_logging("WARNING")
    init_db()
    meta = SourceMetadata(**json.loads(Path(a.meta).read_text(encoding="utf-8")))
    p = Path(a.file)
    res = IngestionService(HybridRetriever()).ingest(p.read_bytes(), p.name, meta)
    print(json.dumps(res.model_dump(), indent=2))
