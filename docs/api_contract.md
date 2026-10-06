# API contract (from the participant guide, section 6)

POST /ask: header X-Student-Id (optional for general questions). Body: question, optional as_of_date (YYYY-MM-DD, defaults to today).
Response: trace_id, answer, answer_type, citations[{doc_id,title,section,page,version,effective_from}],
tools_invoked[{tool,input,output}], applied_rules[{rule_id,value,source_doc_id}], conflicts_detected[], explanation, as_of_date.
answer_type: retrieved_fact | calculated | not_found | clarification_needed | refused | conflict_flagged
POST /ingest: multipart, file plus metadata JSON (Source Register fields). Returns doc_id, chunks_indexed, status.
GET /health: status of API, vector store, SQLite and LLM. GET /audit/{trace_id}. GET /sources. Admin: load test students from CSV.
