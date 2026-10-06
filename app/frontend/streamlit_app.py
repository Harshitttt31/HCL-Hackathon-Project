"""Streamlit front end. It talks to the API over HTTP only: it holds no business logic and no data.

  streamlit run app/frontend/streamlit_app.py
Environment: API_URL (default http://localhost:8000), ADMIN_TOKEN (only needed for the upload tab).
"""
from __future__ import annotations

import json
import os
from datetime import date

import requests
import streamlit as st

API = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
API_TIMEOUT = float(os.environ.get("API_TIMEOUT_SECONDS", "90"))  # raise for a CPU-only local model
TYPE_LABEL = {
    "retrieved_fact": "Policy fact from a document", "calculated": "Calculated from your records", "not_found": "Not found in the documents",
    "clarification_needed": "I need one more detail", "refused": "Refused for privacy or safety", "conflict_flagged": "Sources conflict, needs a human",
}

st.set_page_config(page_title="University Academic Assistant", page_icon=None, layout="wide")


def call(method: str, path: str, **kw):
    try:
        r = requests.request(method, API + path, timeout=API_TIMEOUT, **kw)
        return r.status_code, (r.json() if r.content else {})
    except requests.RequestException as exc:
        return 0, {"detail": f"cannot reach the API at {API}: {exc}"}


st.title("University Academic Assistant")
st.caption("Answers come from the university's authorised documents and your own records. The language model only words the answer; "
           "every number and rule is computed or looked up by code and cited.")

with st.sidebar:
    st.header("Who is asking")
    student_id = st.text_input("Student identifier", value="S1001", help="Sent as the X-Student-Id header. Leave blank to ask general policy questions only.")
    use_date = st.checkbox("Ask as of a specific date", value=False)
    as_of = st.date_input("Date", value=date.today()) if use_date else None
    st.divider()
    code, h = call("GET", "/health")
    if code == 200:
        st.success(f"API {h.get('status')}")
        st.write(f"Language model: {h.get('llm_backend')} ({h.get('llm_model')})")
        st.write(f"Embedder: {h.get('embedder')}")
        st.write(f"Documents: {h.get('documents')}, chunks: {h.get('chunks_indexed')}")
    else:
        st.error(h.get("detail", "API unreachable"))

tab_ask, tab_sources, tab_audit, tab_upload = st.tabs(["Ask", "Source register", "Audit trail", "Add a document"])

with tab_ask:
    examples = [
        "What is the minimum attendance for B.Tech CSE students?",
        "What is my attendance in CS301?",
        "Am I eligible to appear in the exam for CS201 and if not, how many classes do I need to attend?",
        "What is my CGPA and is it enough for campus placements?",
        "What is the last date and fee for the supplementary exam?",
    ]
    pick = st.selectbox("Try an example", ["(type your own)"] + examples)
    question = st.text_area("Your question", value="" if pick.startswith("(") else pick, height=90)
    if st.button("Ask", type="primary", disabled=not question.strip()):
        headers = {"X-Student-Id": student_id.strip()} if student_id.strip() else {}
        body = {"question": question}
        if as_of:
            body["as_of_date"] = as_of.isoformat()
        code, res = call("POST", "/ask", json=body, headers=headers)
        if code != 200:
            st.error(res.get("detail", res))
        else:
            st.session_state["last"] = res
    res = st.session_state.get("last")
    if res:
        st.subheader(TYPE_LABEL.get(res["answer_type"], res["answer_type"]))
        st.write(res["answer"])
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Citations**")
            if res["citations"]:
                st.dataframe(res["citations"], hide_index=True)
            else:
                st.write("None for this answer.")
            st.markdown("**Rules applied**")
            if res["applied_rules"]:
                st.dataframe(res["applied_rules"], hide_index=True)
            else:
                st.write("None.")
        with c2:
            st.markdown("**Tools invoked**")
            for t in res["tools_invoked"]:
                with st.expander(t["tool"]):
                    st.json({"input": t["input"], "output": t["output"]})
            if not res["tools_invoked"]:
                st.write("None.")
        if res["conflicts_detected"]:
            st.markdown("**Conflicts detected and how they were resolved**")
            for c in res["conflicts_detected"]:
                st.info(c.get("explanation", json.dumps(c)))
        with st.expander("Explanation, date used and trace"):
            st.write(res["explanation"])
            st.write(f"Answer date: {res['as_of_date']}")
            st.code(res["trace_id"])

with tab_sources:
    d = st.date_input("Status as of", value=date.today(), key="src_date")
    code, rows = call("GET", "/sources", params={"as_of": d.isoformat()})
    if code == 200:
        st.dataframe(rows, hide_index=True)
    else:
        st.error(rows.get("detail", rows))

with tab_audit:
    default = st.session_state.get("last", {}).get("trace_id", "")
    tid = st.text_input("Trace identifier", value=default)
    if st.button("Load audit record") and tid:
        headers = {"X-Student-Id": student_id.strip()} if student_id.strip() else {}
        if ADMIN_TOKEN:
            headers["X-Admin-Token"] = ADMIN_TOKEN
        code, rec = call("GET", f"/audit/{tid.strip()}", headers=headers)
        if code == 200:
            st.json(rec)
        else:
            st.error(rec.get("detail", rec))

with tab_upload:
    st.write("Adds a document while the system is running. Needs the administrator token (ADMIN_TOKEN) to be set for this app and the API.")
    f = st.file_uploader("Document (pdf, docx, md, txt)")
    c1, c2, c3 = st.columns(3)
    doc_id = c1.text_input("doc_id")
    title = c2.text_input("Title")
    issuer = c3.text_input("Issuer")
    c4, c5, c6 = st.columns(3)
    level = c4.selectbox("Authority level", [1, 2, 3, 4, 5], index=1, help="1 regulation, 2 circular, 3 handbook, 4 department notice, 5 informal")
    version = c5.text_input("Version", value="1.0")
    eff = c6.date_input("Effective from", value=date.today())
    supersedes = st.text_input("Supersedes (doc_id, optional)")
    if st.button("Ingest", disabled=not (f and doc_id and title and issuer)):
        meta = {"doc_id": doc_id, "title": title, "issuer": issuer, "authority_level": level, "doc_type": "circular", "version": version,
                "effective_from": eff.isoformat(), "supersedes": supersedes, "provenance": "uploaded through the web interface",
                "retrieved_on": date.today().isoformat()}
        code, res = call("POST", "/ingest", files={"file": (f.name, f.getvalue())}, data={"metadata": json.dumps(meta)},
                         headers={"X-Admin-Token": ADMIN_TOKEN})
        if code == 200:
            st.success(res)
        else:
            st.error(res.get("detail", res))
