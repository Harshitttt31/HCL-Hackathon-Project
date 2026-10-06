"""The evaluation baseline is a regression guard: every case must pass on the baseline configuration."""
from pathlib import Path

from app.evaluation.runner import run_config


def test_baseline_has_no_failed_case():
    run = run_config("baseline", {}, Path("data"))
    failed = [(r["id"], r["answer_type"]) for r in run["results"] if not r["passed"]]
    assert not failed, failed
    assert run["summary"]["cases"] >= 100
