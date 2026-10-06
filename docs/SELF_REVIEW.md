# Self-review

Ten questions, answered against the evidence in this repository. The wording of the questions is mine, not copied from the guide.
Each answer says what is proven and what is not.

1. **Does every factual statement come from a tool or a cited document?**
   Yes by construction: rules come from `get_rule` or retrieved clauses, numbers from the calculator tools. The validator
   (`app/agent/validator.py`) rejects any reworded answer that adds a number, date or identifier not found in tool output or evidence.
   Evidence: tool result correctness 68 of 68 checks, citation accuracy 1.0 on 37 cases. Not proven: the validator against a real Ollama model.

2. **When documents disagree, is the rule from the guide applied and shown?**
   Yes. Applicability, informational level, supersession, authority, recency, then `conflict_flagged`. Demo scenarios 1 and 2 show a circular
   beating the department FAQ and the older rule staying in force before its replacement date. 14 version-conflict cases pass.

3. **Does the system abstain instead of guessing?**
   Yes. Coverage gate on query terms, subject-term check, and `not_found` with the closest text. Abstention accuracy 1.0 on 10 cases,
   false abstention 0.0 on 81 answerable cases. Weakness: the thresholds were tuned on this corpus.

4. **Can a student see someone else's data?**
   Blocked by identifier, name, relation word, bulk wording and missing identity, and the identity comes only from the header. 20 of 20
   refusal cases pass and refusals invoke no tool. Found and fixed during final checks: "show me the attendance of every student" slipped
   through; there is now a regression test. Not proven: unusual phrasings, other languages.

5. **Is prompt injection handled?**
   Instruction-like text in a question or in a retrieved chunk is flagged and never obeyed; poisoned chunks are excluded from evidence.
   13 of 13 red team cases pass. Not proven against a strong adaptive attacker.

6. **Are calculations exact?**
   Percentages and projections use exact fractions with half-up rounding; boundary students (exactly 80.00%, 79.99%, CGPA 7.00 and 6.99)
   are in the evaluation. Expected values in the cases are computed independently of the system.

7. **Is every answer explainable and auditable?**
   Each response carries citations, tools invoked, applied rules, conflicts and an explanation. Each has an audit record retrievable at
   `/audit/{trace_id}` by its owner or an administrator. Three real records are in `docs/sample_audits/`.

8. **Does it degrade safely?**
   No model: template answers. No vector store: keyword search. No embedder: hashing fallback. `/health` reports each. Tested with the mock model and the
   hashing embedder only; the Ollama and Chroma-server failure paths were not exercised against real outages.

9. **How good is retrieval, and how was it compared?**
   Hit at 5 and top 1 are 1.0 on 19 cases. Six configurations were compared (docs/EVALUATION_REPORT.md): fixed 500 character chunks lose
   19 cases, no reranker loses 2, top_k 3 loses 1. Keyword only and vector only tie with hybrid here, so the gain from hybrid is argued, not
   measured. The corpus has 52 chunks, which makes retrieval easy.

10. **What would I distrust before the demo?**
    The evaluation was written by the same author as the system and used while tuning, so 111 of 111 is a regression guard, not an
    accuracy estimate. Docker was never built, Ollama never run, real sentence-transformers never loaded, and only synthetic documents were
    tried. Run the stack once end to end on the demo machine and try a few real documents before presenting.
