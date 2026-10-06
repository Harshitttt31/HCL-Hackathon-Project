"""Run the evaluation. Writes data/evaluation/results.json and docs/EVALUATION_REPORT.md.

  python scripts/evaluate.py              baseline configuration only
  python scripts/evaluate.py --compare    all configurations in the comparison grid
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.report import write_report  # noqa: E402
from app.evaluation.runner import CONFIG_GRID, run_config  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--out", default="data/evaluation/results.json")
    ap.add_argument("--report", default="docs/EVALUATION_REPORT.md")
    a = ap.parse_args()
    grid = CONFIG_GRID if a.compare else {k: v for k, v in list(CONFIG_GRID.items())[:1]}
    runs = []
    for name, cfg in grid.items():
        print(f"running: {name}", flush=True)
        run = run_config(name, cfg, Path(a.data))
        s = run["summary"]
        print(f"   passed {s['passed']}/{s['cases']}  p50 {s['latency_p50_ms']} ms  p95 {s['latency_p95_ms']} ms", flush=True)
        runs.append(run)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(runs, indent=1), encoding="utf-8")
    write_report(runs, Path(a.report))
    print(f"wrote {a.out} and {a.report}")
