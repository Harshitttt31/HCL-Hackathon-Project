"""Render docs/EVALUATION_REPORT.md from the evaluation runs (one or more configurations)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

METRICS = [
    ("passed", "Cases passed", "{}"),
    ("answer_correctness", "Answer correctness", "{:.3f}"),
    ("redteam_pass_rate", "Red team pass rate", "{:.3f}"),
    ("citation_accuracy", "Citation accuracy", "{:.3f}"),
    ("abstention_accuracy", "Abstention accuracy (unanswerable questions)", "{:.3f}"),
    ("false_abstention_rate", "False abstention rate (lower is better)", "{:.3f}"),
    ("refusal_recall", "Refusal recall (privacy and injection)", "{:.3f}"),
    ("false_refusal_rate", "False refusal rate (lower is better)", "{:.3f}"),
    ("tool_result_correctness", "Tool result correctness", "{:.3f}"),
    ("retrieval_hit_at_5", "Retrieval hit at 5", "{:.3f}"),
    ("retrieval_top1", "Retrieval top 1", "{:.3f}"),
    ("latency_p50_ms", "Latency median (milliseconds)", "{}"),
    ("latency_p95_ms", "Latency 95th percentile (milliseconds)", "{}"),
    ("llm_calls_per_question", "Language model calls per question", "{}"),
]


def _fmt(value: Any, pattern: str) -> str:
    if value is None:
        return "n/a"
    try:
        return pattern.format(value)
    except (ValueError, TypeError):
        return str(value)


def write_report(runs: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base = runs[0]
    s = base["summary"]
    L: list[str] = []
    L.append("# Evaluation report\n")
    L.append(f"Cases: {s['cases']} ({sum(1 for r in base['results'] if r['suite'] == 'functional')} functional, "
             f"{sum(1 for r in base['results'] if r['suite'] == 'redteam')} red team). "
             f"Language model: {s['llm_backend']}. Embedder: {s['embedder']}. Indexed chunks: {s['chunks']}.\n")
    L.append("Expected answers are computed by independent arithmetic on the generated data and from the corpus design, "
             "never by calling the system under test. See the limits section for what this does and does not prove.\n")

    L.append("## Baseline result\n")
    L.append("| Metric | Value |\n|---|---|")
    for key, label, pat in METRICS:
        L.append(f"| {label} | {_fmt(s.get(key), pat)} |")
    L.append("")

    L.append("## Result by category\n")
    L.append("| Category | Cases | Passed |\n|---|---|---|")
    for cat, v in s["by_category"].items():
        L.append(f"| {cat} | {v['cases']} | {v['passed']} |")
    L.append("")

    failed = [r for r in base["results"] if not r["passed"]]
    L.append("## Failed cases in the baseline\n")
    if not failed:
        L.append("None.\n")
    else:
        for r in failed:
            L.append(f"- {r['id']}: {r.get('question', '')} (expected {r['expect_type']}, got {r['answer_type']})")
        L.append("")

    if len(runs) > 1:
        L.append("## Configuration comparison\n")
        L.append("Same cases, same data, same mock language model. Only the named setting changes.\n")
        L.append("| Metric | " + " | ".join(r["name"] for r in runs) + " |")
        L.append("|---|" + "---|" * len(runs))
        for key, label, pat in METRICS:
            L.append(f"| {label} | " + " | ".join(_fmt(r["summary"].get(key), pat) for r in runs) + " |")
        L.append("")
        L.append("### Cases that differ from the baseline\n")
        base_ok = {r["id"]: r["passed"] for r in base["results"]}
        for run in runs[1:]:
            lost = [r["id"] for r in run["results"] if base_ok.get(r["id"]) and not r["passed"]]
            L.append(f"- {run['name']}: {len(lost)} case(s) lost" + (f" ({', '.join(lost[:12])}{'...' if len(lost) > 12 else ''})" if lost else ""))
        L.append("")
        perfect = [r["name"] for r in runs if r["summary"]["passed"] == r["summary"]["cases"]]
        L.append("Configurations that pass every case: " + "; ".join(perfect) + ".")
        L.append("The structure aware chunking is the setting that matters most here: fixed 500 character chunks cut clauses and tables "
                 "apart and lose the largest share of cases. Dropping the reranker or lowering top_k costs a few cases. Keyword only and "
                 "vector only tie with hybrid on this small corpus, so the evidence for keeping both is design reasoning (exact terms such as "
                 "course codes and section numbers favour BM25, paraphrases favour vectors), not a measured gain here.\n")

    ex = base.get("rule_extraction")
    if ex:
        L.append("## Rule extraction from documents\n")
        L.append(f"Gold rules: {ex['gold']}. Extracted: {ex['extracted']}. Correct: {ex['correct']}. "
                 f"Precision {ex['precision']}. Recall {ex['recall']}.\n")

    L.append("## Limits of this evaluation\n")
    L.append("- The cases were written by the same author as the system and were used while tuning it. A score of 1.0 on them is a "
             "regression guard, not a claim of generalisation. Run the hidden or organiser test set before trusting any number here.")
    L.append("- The mock language model was used, so wording quality is not measured. Facts, numbers and citations come from code "
             "and are checked, which is the point of the design.")
    L.append("- The embedder is a hashing fallback because sentence-transformers could not be downloaded in this environment. "
             "Dense retrieval quality with the real model is not measured here.")
    L.append("- The corpus has a small number of documents, so retrieval hit rates are easier than on a real university archive.")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
