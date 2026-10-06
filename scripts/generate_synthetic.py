"""Build the synthetic data kit into data/ (documents, CSV files, data card) and validate it."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.synthetic.build import build_all  # noqa: E402
from app.synthetic.validate import validate_dir  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--seed", type=int, default=20261006)
    a = ap.parse_args()
    out = Path(a.out)
    print(json.dumps(build_all(out, a.seed), indent=2))
    report = validate_dir(out)
    (out / "validation_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"validation: {report['checks_run'] - report['checks_failed']}/{report['checks_run']} checks passed")
    sys.exit(0 if report["passed"] else 1)
