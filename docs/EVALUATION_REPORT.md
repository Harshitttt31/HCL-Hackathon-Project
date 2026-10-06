# Evaluation report

Cases: 111 (98 functional, 13 red team). Language model: mock. Embedder: hashing-512. Indexed chunks: 52.

Expected answers are computed by independent arithmetic on the generated data and from the corpus design, never by calling the system under test. See the limits section for what this does and does not prove.

## Baseline result

| Metric | Value |
|---|---|
| Cases passed | 111 |
| Answer correctness | 1.000 |
| Red team pass rate | 1.000 |
| Citation accuracy | 1.000 |
| Abstention accuracy (unanswerable questions) | 1.000 |
| False abstention rate (lower is better) | 0.000 |
| Refusal recall (privacy and injection) | 1.000 |
| False refusal rate (lower is better) | 0.000 |
| Tool result correctness | 1.000 |
| Retrieval hit at 5 | 1.000 |
| Retrieval top 1 | 1.000 |
| Latency median (milliseconds) | 16.7 |
| Latency 95th percentile (milliseconds) | 28.1 |
| Language model calls per question | 0.82 |

## Result by category

| Category | Cases | Passed |
|---|---|---|
| clarification | 3 | 3 |
| injection | 13 | 13 |
| multi_step | 4 | 4 |
| personal_tool | 31 | 31 |
| policy_fact | 19 | 19 |
| privacy | 14 | 14 |
| robustness | 5 | 5 |
| unanswerable | 8 | 8 |
| version_conflict | 14 | 14 |

## Failed cases in the baseline

None.

## Configuration comparison

Same cases, same data, same mock language model. Only the named setting changes.

| Metric | A baseline: structure chunks, hybrid, rerank, top_k 8 | B fixed 500-character chunks | C keyword search only (BM25) | D vector search only | E hybrid without reranker | F top_k 3 |
|---|---|---|---|---|---|---|
| Cases passed | 111 | 92 | 111 | 111 | 109 | 110 |
| Answer correctness | 1.000 | 0.829 | 1.000 | 1.000 | 0.982 | 0.991 |
| Red team pass rate | 1.000 | 0.923 | 1.000 | 1.000 | 1.000 | 1.000 |
| Citation accuracy | 1.000 | 0.842 | 1.000 | 1.000 | 0.946 | 0.973 |
| Abstention accuracy (unanswerable questions) | 1.000 | 0.900 | 1.000 | 1.000 | 1.000 | 1.000 |
| False abstention rate (lower is better) | 0.000 | 0.000 | 0.000 | 0.000 | 0.025 | 0.000 |
| Refusal recall (privacy and injection) | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| False refusal rate (lower is better) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| Tool result correctness | 1.000 | 0.833 | 1.000 | 1.000 | 1.000 | 1.000 |
| Retrieval hit at 5 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Retrieval top 1 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Latency median (milliseconds) | 16.7 | 16.7 | 15.1 | 19.2 | 18.3 | 16.3 |
| Latency 95th percentile (milliseconds) | 28.1 | 32.3 | 26.2 | 31.8 | 34.1 | 28.9 |
| Language model calls per question | 0.82 | 0.82 | 0.82 | 0.82 | 0.82 | 0.82 |

### Cases that differ from the baseline

- B fixed 500-character chunks: 19 case(s) lost (PF01, PF04, PF05, PF10, VC02, VC04, VC07, VC10, VC13, PT10, PT13, PT19...)
- C keyword search only (BM25): 0 case(s) lost
- D vector search only: 0 case(s) lost
- E hybrid without reranker: 2 case(s) lost (PF04, VC05)
- F top_k 3: 1 case(s) lost (VC05)

Configurations that pass every case: A baseline: structure chunks, hybrid, rerank, top_k 8; C keyword search only (BM25); D vector search only.
The structure aware chunking is the setting that matters most here: fixed 500 character chunks cut clauses and tables apart and lose the largest share of cases. Dropping the reranker or lowering top_k costs a few cases. Keyword only and vector only tie with hybrid on this small corpus, so the evidence for keeping both is design reasoning (exact terms such as course codes and section numbers favour BM25, paraphrases favour vectors), not a measured gain here.

## Rule extraction from documents

Gold rules: 15. Extracted: 15. Correct: 15. Precision 1.0. Recall 1.0.

## Limits of this evaluation

- The cases were written by the same author as the system and were used while tuning it. A score of 1.0 on them is a regression guard, not a claim of generalisation. Run the hidden or organiser test set before trusting any number here.
- The mock language model was used, so wording quality is not measured. Facts, numbers and citations come from code and are checked, which is the point of the design.
- The embedder is a hashing fallback because sentence-transformers could not be downloaded in this environment. Dense retrieval quality with the real model is not measured here.
- The corpus has a small number of documents, so retrieval hit rates are easier than on a real university archive.
