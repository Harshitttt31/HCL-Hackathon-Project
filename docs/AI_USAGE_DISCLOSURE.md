# AI usage disclosure

## Where AI is part of the running product
- A language model (Ollama, default `llama3.1:8b`) is used for two narrow jobs: helping to read the intent of an unusual question, and
  rewording an already computed answer so it reads naturally. It is never the source of a fact, a number, a date or a rule.
- Every reworded answer is checked by `app/agent/validator.py`: any number, date, rule identifier or document identifier in the wording
  that is not present in the tool outputs or cited evidence is rejected and the plain template answer is used instead.
- If the model is unavailable or times out, the system keeps working with template answers (`/health` reports this).
- Embeddings: `sentence-transformers/all-MiniLM-L6-v2` when installed, with a hashing fallback. Reranking is lexical by default.

## Where AI was used to build the project
- The design, code, tests, synthetic data generator, evaluation cases and documents in this repository were written with Claude
  (Anthropic) acting as a coding assistant, working from the hackathon problem statement and the participant guide.
- The AI proposed the architecture and wrote the code. The participant directed the work, reviewed results and decides what is submitted.
- Synthetic data was produced by seeded Python code, not by a language model (see `data/DATA_CARD.md`).

## What the participant must confirm before submitting
- That they have read and can explain the code, the source precedence rules and the evaluation results.
- Their own names and the split of work in `docs/CONTRIBUTION_STATEMENT.md`.

## Known weaknesses of AI-built work
- The evaluation cases were written by the same assistant that wrote the system, so they can share its blind spots.
- Docker, Ollama and the real embedding model could not be run in the build environment. See the README section "What was and was not verified".
