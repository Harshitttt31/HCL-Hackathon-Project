# Data card: synthetic university kit

## What this is
A fictional institute (Meridian Institute of Technology) with 10 documents, 80 courses and 60 students, built to exercise the decision engine.
Every document is marked `synthetic = Y` in the Source Register. None is a real university document and none was copied from one.

## How it was produced
- Documents: template text in `app/synthetic/corpus.py`, rendered by `app/synthetic/render.py` (PDF, DOCX, Markdown, one image-only scanned PDF).
- Students, attendance and results: `app/synthetic/students.py`, seeded random generator (seed 20261006); the same seed gives identical files.
- No language model was used to generate this data. `prompts/synthetic_students_prompt.txt` documents the prompt to use if the data is regenerated with an LLM instead.
- Validation: `python scripts/validate_synthetic.py` checks referential integrity, mark sums, pass/fail consistency, backlog counts and CGPA recomputation.

## Contents
- Students: 60 (B.Tech CSE: 42, B.Tech ECE: 12, M.Tech CSE: 6); courses: 80; attendance rows: 952; result rows: 738 (55 not PASS).
- Designed edge-case students (IDs S1001 to S1010, see `data/designed_students.json`) sit exactly on decision boundaries.

## Known limitations
- Names are drawn from a fixed list; they are fictional and may coincide with real names by chance.
- Grade, attendance and marks distributions are plausible, not calibrated to any real cohort.
- The policy corpus is small and tidy. Real documents are longer, messier and contain scanned tables; swap them in using the same Source Register columns.
- Rules in `data/rule_registry.csv` were written by hand to match the documents; the evaluation also measures how many the automatic extractor recovers.
