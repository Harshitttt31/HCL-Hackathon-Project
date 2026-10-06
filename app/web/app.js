// Academic Assistant web interface. Talks to the API on the same origin; all text is inserted with textContent.

const TYPE_LABEL = {
  retrieved_fact: "From official documents",
  calculated: "Calculated from your records",
  not_found: "Not covered by the documents",
  clarification_needed: "Needs one more detail",
  refused: "Declined for privacy or safety",
  conflict_flagged: "Sources conflict, needs a human",
};
const STUDENT_EXAMPLES = [
  "What is my attendance in CS301?",
  "Am I eligible to appear in the exam for CS201 and if not, how many classes do I need to attend?",
  "What is my CGPA and is it enough for campus placements?",
  "What is the minimum attendance for B.Tech CSE students?",
  "What is the last date and fee for the supplementary exam?",
];
const GENERAL_EXAMPLES = [
  "What is the minimum attendance for B.Tech CSE students?",
  "What is the last date and fee for the supplementary exam?",
  "What are the library borrowing rules?",
];
const STORAGE_KEY = "univ-assistant.session";

const state = {
  token: null,
  user: null,
  expiresAt: 0,
  thread: [],     // [{question, asOf, status: 'pending'|'done'|'error', response, error}]
  draft: "",
  asOf: "",
  health: null,
};
let expiryTimer = null;
let healthTimer = null;
let closeUserMenu = () => {};
document.addEventListener("click", (e) => closeUserMenu(e));

// ------------------------------------------------------------------------------------------------
// DOM helpers
// ------------------------------------------------------------------------------------------------
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;  // CSSOM, so the page's CSP (no inline style attributes) allows it
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const $app = () => document.getElementById("app");
const svgNS = "http://www.w3.org/2000/svg";
function brandMark() {
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", "0 0 32 32");
  const cap = document.createElementNS(svgNS, "path");
  cap.setAttribute("d", "M5 12l11-5 11 5-11 5z"); cap.setAttribute("fill", "white");
  const band = document.createElementNS(svgNS, "path");
  band.setAttribute("d", "M10 15v6c0 2 2.7 4 6 4s6-2 6-4v-6");
  band.setAttribute("stroke", "white"); band.setAttribute("stroke-width", "2.4"); band.setAttribute("fill", "none");
  svg.append(cap, band);
  return h("div", { class: "brand-mark", "aria-hidden": "true" }, svg);
}
function brand() {
  return h("div", { class: "brand" }, brandMark(),
    h("div", {}, h("div", { class: "brand-name" }, "Academic Assistant"), h("div", { class: "brand-sub" }, "Meridian Institute of Technology")));
}
function badge(type) { return h("span", { class: `badge t-${type}` }, TYPE_LABEL[type] || type); }
function json(value) { return h("pre", { class: "json" }, JSON.stringify(value, null, 2)); }
function alertBox(kind, text) { return h("div", { class: `alert ${kind}`, role: kind === "error" ? "alert" : "status" }, text); }
function todayISO() { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
function initials(user) {
  const src = user.full_name || user.username || "?";
  return src.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");
}

// ------------------------------------------------------------------------------------------------
// API
// ------------------------------------------------------------------------------------------------
class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
function detailText(detail) {
  if (!detail) return "Something went wrong.";
  if (Array.isArray(detail)) return detail.map((d) => (typeof d === "string" ? d : d.msg || JSON.stringify(d))).join("; ");
  return typeof detail === "string" ? detail : JSON.stringify(detail);
}
async function api(path, { method = "GET", body, form, auth = true } = {}) {
  const headers = {};
  if (auth && state.token) headers.Authorization = `Bearer ${state.token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check that the API is running.");
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && auth && state.token) {
    signOut(detailText(data.detail) === "session expired, sign in again" ? "Your session has expired. Please sign in again." : "Please sign in again.");
    throw new ApiError(401, "Signed out");
  }
  if (!res.ok) throw new ApiError(res.status, detailText(data.detail));
  return data;
}

// ------------------------------------------------------------------------------------------------
// Session
// ------------------------------------------------------------------------------------------------
function saveSession() {
  try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ token: state.token, user: state.user, expiresAt: state.expiresAt })); } catch { /* storage unavailable */ }
}
function loadSession() {
  try {
    const s = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null");
    if (s && s.token && s.expiresAt > Date.now() + 5000) Object.assign(state, s);
  } catch { /* ignore */ }
}
function startSession(res) {
  state.token = res.access_token;
  state.user = res.user;
  state.expiresAt = Date.now() + res.expires_in * 1000;
  state.thread = [];
  saveSession();
  armExpiry();
}
function armExpiry() {
  clearTimeout(expiryTimer);
  const ms = state.expiresAt - Date.now();
  expiryTimer = setTimeout(() => signOut("Your session has expired. Please sign in again."), Math.max(0, Math.min(ms, 2 ** 31 - 1)));
}
function signOut(message) {
  clearTimeout(expiryTimer);
  clearInterval(healthTimer);
  healthTimer = null;
  state.token = null; state.user = null; state.expiresAt = 0; state.thread = []; state.draft = "";
  try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ }
  location.hash = "";
  renderLogin(message);
}

// ------------------------------------------------------------------------------------------------
// Sign-in page
// ------------------------------------------------------------------------------------------------
function renderLogin(message) {
  const err = h("div");
  if (message) err.append(alertBox("info", message));
  const username = h("input", { class: "input", name: "username", autocomplete: "username", required: true, placeholder: "S1001", autofocus: true });
  const password = h("input", { class: "input", name: "password", type: "password", autocomplete: "current-password", required: true });
  const submit = h("button", { class: "btn primary", type: "submit" }, "Sign in");
  const form = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      submit.disabled = true; submit.textContent = "Signing in…";
      err.replaceChildren();
      try {
        startSession(await api("/auth/login", { method: "POST", auth: false, body: { username: username.value, password: password.value } }));
        renderShell();
      } catch (ex) {
        err.replaceChildren(alertBox("error", ex.message));
        password.value = ""; password.focus();
      } finally {
        submit.disabled = false; submit.textContent = "Sign in";
      }
    },
  },
  h("label", { class: "field" }, h("span", {}, "Student ID or username"), username),
  h("label", { class: "field" }, h("span", {}, "Password"), password),
  err, submit);
  $app().replaceChildren(h("div", { class: "login-wrap" }, h("div", { class: "login-card" },
    brand(),
    h("p", { class: "muted", style: "margin:1.25rem 0 0" }, "Sign in to ask about regulations, your attendance, results and eligibility."),
    form,
    h("div", { class: "demo-hint" }, "Demo: sign in with a student ID such as S1001. The first-time password is the one set in DEMO_STUDENT_PASSWORD (default student123); you can change it after signing in."))));
}

// ------------------------------------------------------------------------------------------------
// App shell
// ------------------------------------------------------------------------------------------------
const ROUTES = {
  ask: { label: "Ask", render: renderAsk },
  records: { label: "My records", render: renderRecords, student: true },
  sources: { label: "Sources", render: renderSources },
  admin: { label: "Admin", render: renderAdmin, admin: true },
};
function routeAllowed(key) {
  const r = ROUTES[key];
  return r && !(r.student && state.user.role !== "student") && !(r.admin && state.user.role !== "admin");
}
function currentRoute() {
  const key = (location.hash.replace(/^#\/?/, "") || "ask").split("?")[0];
  return routeAllowed(key) ? key : "ask";
}

function renderShell() {
  const route = currentRoute();
  const nav = h("nav", { class: "nav", "aria-label": "Main" },
    Object.entries(ROUTES).filter(([k]) => routeAllowed(k))
      .map(([k, r]) => h("a", { href: `#/${k}`, class: k === route ? "active" : null, "aria-current": k === route ? "page" : null }, r.label)));
  const healthEl = h("span", { class: "health", id: "health" });
  showHealth(healthEl, state.health);
  const main = h("main", { id: "main" });
  $app().replaceChildren(h("header", { class: "topbar" }, brand(), nav, h("div", { class: "spacer" }), healthEl, userMenu()), main);
  ROUTES[route].render(main);
  if (!healthTimer) {
    refreshHealth();
    healthTimer = setInterval(refreshHealth, 60000);
  }
}

function userMenu() {
  const u = state.user;
  const menu = h("div", { class: "menu hidden", role: "menu" },
    h("div", { class: "who" },
      h("div", { style: "font-weight:650" }, u.full_name || u.username),
      h("div", { class: "muted small" }, u.role === "admin" ? "Administrator" : [u.student_id, u.programme, u.current_semester ? `Semester ${u.current_semester}` : null].filter(Boolean).join(" · "))),
    h("button", { class: "btn ghost sm", role: "menuitem", onclick: () => { close(); openChangePassword(); } }, "Change password"),
    h("button", { class: "btn ghost sm", role: "menuitem", onclick: () => signOut("You have signed out.") }, "Sign out"));
  const btn = h("button", { class: "btn ghost user-btn", "aria-haspopup": "menu", "aria-expanded": "false", onclick: (e) => { e.stopPropagation(); toggle(); } },
    h("span", { class: "avatar" }, initials(u)), h("span", { class: "small" }, u.role === "admin" ? u.username : u.student_id));
  function toggle() { const open = menu.classList.toggle("hidden") === false; btn.setAttribute("aria-expanded", String(open)); }
  function close() { menu.classList.add("hidden"); btn.setAttribute("aria-expanded", "false"); }
  closeUserMenu = (e) => { if (!menu.contains(e.target)) close(); };
  return h("div", { class: "user-menu" }, btn, menu);
}

function showHealth(el, hl) {
  if (!hl) { el.replaceChildren(h("span", { class: "dot" }), h("span", { class: "label" }, "Checking…")); return; }
  if (hl.unreachable) { el.replaceChildren(h("span", { class: "dot down" }), h("span", { class: "label" }, "API unreachable")); return; }
  el.replaceChildren(h("span", { class: `dot ${hl.status}` }), h("span", { class: "label" }, hl.status === "ok" ? "All systems ok" : `Service ${hl.status}`));
  el.title = `Language model: ${hl.llm_backend} (${hl.llm_model}) – ${hl.llm}\nDocuments: ${hl.documents}, chunks: ${hl.chunks_indexed}\nSQLite: ${hl.sqlite}\nVector store: ${hl.vector_store}`;
}

async function refreshHealth() {
  try {
    state.health = await api("/health", { auth: false });
  } catch {
    state.health = { unreachable: true };
  }
  const el = document.getElementById("health");
  if (el) showHealth(el, state.health);
}

// ------------------------------------------------------------------------------------------------
// Ask
// ------------------------------------------------------------------------------------------------
function renderAsk(main) {
  const isStudent = state.user.role === "student";
  const thread = h("div", { class: "thread", id: "thread" });
  const textarea = h("textarea", { class: "input", rows: 1, placeholder: isStudent ? "Ask about rules, your attendance, results or eligibility…" : "Ask a general policy question…",
    "aria-label": "Your question", value: state.draft });
  const send = h("button", { class: "btn primary", type: "submit" }, "Ask");
  const asOf = h("input", { type: "date", class: "input", value: state.asOf, max: "2100-12-31", "aria-label": "Answer as of date" });
  const clearDate = h("button", { class: "btn ghost sm", type: "button", onclick: () => { asOf.value = ""; state.asOf = ""; } }, "Today");
  asOf.addEventListener("change", () => { state.asOf = asOf.value; });

  const autosize = () => { textarea.style.height = "auto"; textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`; };
  textarea.addEventListener("input", () => { state.draft = textarea.value; autosize(); });
  textarea.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });

  const form = h("form", { class: "composer", onsubmit: (e) => { e.preventDefault(); submitQuestion(textarea.value); } },
    h("div", { class: "composer-box" }, textarea,
      h("div", { class: "composer-tools" },
        h("label", {}, "Answer as of", asOf), clearDate,
        h("span", { class: "spacer" }),
        h("span", { class: "muted small" }, "Enter to send · Shift+Enter for a new line"), send)));

  function submitQuestion(text) {
    const q = text.trim();
    if (!q || state.thread.some((t) => t.status === "pending")) return;
    const item = { question: q, asOf: state.asOf || "", status: "pending" };
    state.thread.push(item);
    state.draft = ""; textarea.value = ""; autosize();
    drawThread();
    const body = { question: q };
    if (item.asOf) body.as_of_date = item.asOf;
    api("/ask", { method: "POST", body })
      .then((res) => { item.status = "done"; item.response = res; })
      .catch((ex) => { item.status = "error"; item.error = ex.message; })
      .finally(() => { if (document.getElementById("thread") === thread) drawThread(); });
  }

  function drawThread() {
    const pending = state.thread.some((t) => t.status === "pending");
    send.disabled = pending;
    if (!state.thread.length) {
      const examples = isStudent ? STUDENT_EXAMPLES : GENERAL_EXAMPLES;
      thread.replaceChildren(h("div", { class: "empty-state" },
        h("h2", {}, isStudent ? `Hello${state.user.full_name ? ", " + state.user.full_name.split(" ")[0] : ""}.` : "Ask a policy question"),
        h("p", { class: "muted" }, "Answers come from the institute's authorised documents and your own records. Every number is calculated by code and every rule is cited."),
        !isStudent ? alertBox("info", "Signed in as an administrator: questions about a particular student's records are not available here.") : null,
        h("div", { class: "chips" }, examples.map((ex) => h("button", { class: "chip", type: "button", onclick: () => submitQuestion(ex) }, ex)))));
      return;
    }
    thread.replaceChildren(...state.thread.flatMap((item) => [
      h("div", { class: "q-bubble" }, item.question),
      item.asOf ? h("div", { class: "q-meta" }, `as of ${item.asOf}`) : null,
      item.status === "pending" ? h("div", { class: "card answer pending" }, h("span", { class: "spinner" }), "Checking the documents and your records…")
        : item.status === "error" ? h("div", { class: "card answer" }, alertBox("error", item.error))
          : answerCard(item.response),
    ].filter(Boolean)));
    const last = thread.lastElementChild;
    if (last) last.scrollIntoView({ behavior: "smooth", block: pending ? "end" : "start" });
  }

  main.replaceChildren(h("div", { class: "ask-layout" }, thread, form));
  drawThread();
  autosize();
  textarea.focus();
  // a question handed over from another page (e.g. "ask how many classes I need")
  const pre = sessionStorage.getItem("univ-assistant.prefill");
  if (pre) { sessionStorage.removeItem("univ-assistant.prefill"); submitQuestion(pre); }
}

function answerCard(r) {
  const card = h("article", { class: "card answer" },
    h("div", { class: "answer-head" }, badge(r.answer_type), h("span", { class: "muted small" }, `Rules as in force on ${r.as_of_date}`)),
    h("div", { class: "answer-text" }, r.answer));

  if (r.citations?.length) {
    card.append(h("div", { class: "citations", "aria-label": "Sources" }, r.citations.map((c) =>
      h("span", { class: "citation", title: c.title || "" }, h("b", {}, c.doc_id),
        [c.section ? `§${c.section}` : null, c.page ? `p.${c.page}` : null, c.version ? `v${c.version}` : null].filter(Boolean).join(" · ")))));
  }
  if (r.conflicts_detected?.length) {
    const seen = new Set();
    const items = r.conflicts_detected.filter((c) => { const k = c.explanation || JSON.stringify(c); if (seen.has(k)) return false; seen.add(k); return true; });
    card.append(h("details", { open: r.answer_type === "conflict_flagged" ? true : null },
      h("summary", {}, `How conflicting sources were resolved (${items.length})`),
      items.map((c) => h("div", { class: "conflict" },
        c.winner ? h("div", {}, h("span", { class: "winner" }, `${c.winner.doc_id} §${c.winner.section}: ${c.winner.value}`),
          (c.overridden || []).map((o) => h("span", {}, "  over  ", h("span", { class: "loser" }, `${o.doc_id} §${o.section}: ${o.value}`)))) : null,
        h("div", { class: "muted small", style: "margin-top:.3rem" }, c.resolution_rule || ""),
        c.explanation ? h("div", { class: "small", style: "margin-top:.3rem" }, c.explanation) : null))));
  }
  if (r.applied_rules?.length) {
    card.append(h("details", {}, h("summary", {}, `Rules applied (${r.applied_rules.length})`),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Rule"), h("th", {}, "Value"), h("th", {}, "Source"))),
        h("tbody", {}, r.applied_rules.map((a) => h("tr", {}, h("td", { class: "mono" }, a.rule_id), h("td", {}, a.value), h("td", {}, a.source_doc_id))))))));
  }
  if (r.tools_invoked?.length) {
    card.append(h("details", {}, h("summary", {}, `Calculations and lookups (${r.tools_invoked.length})`),
      r.tools_invoked.map((t) => h("div", { class: "tool" }, h("div", { class: "tool-name" }, t.tool),
        h("details", {}, h("summary", {}, "Input and output"), json({ input: t.input, output: t.output }))))));
  }
  if (r.explanation) card.append(h("details", {}, h("summary", {}, "Explanation"), h("p", { class: "small", style: "margin:.4rem 0 0" }, r.explanation)));
  card.append(h("div", { class: "answer-foot" }, h("span", {}, "Trace ", h("span", { class: "mono" }, r.trace_id)),
    h("button", { class: "btn sm", type: "button", onclick: () => openAudit(r.trace_id) }, "View audit record")));
  return card;
}

// ------------------------------------------------------------------------------------------------
// Audit record dialog
// ------------------------------------------------------------------------------------------------
function dialog(title, ...body) {
  const dlg = h("dialog", { "aria-label": title },
    h("div", { class: "dialog-head" }, h("h2", {}, title), h("span", { class: "spacer" }),
      h("button", { class: "btn ghost sm", type: "button", onclick: () => dlg.close(), "aria-label": "Close" }, "Close")),
    h("div", { class: "dialog-body" }, ...body));
  dlg.addEventListener("close", () => dlg.remove());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  document.body.append(dlg);
  dlg.showModal();
  return dlg;
}

async function openAudit(traceId) {
  const body = h("div", {}, h("div", { class: "pending" }, h("span", { class: "spinner" }), "Loading…"));
  dialog(`Audit record ${traceId}`, body);
  try {
    const rec = await api(`/audit/${encodeURIComponent(traceId)}`);
    const v = rec.validation || {};
    const timings = (rec.node_trace || []).filter((t) => t && t.node);
    body.replaceChildren(
      h("dl", { class: "kv" },
        h("dt", {}, "Time"), h("dd", {}, rec.ts || ""),
        h("dt", {}, "Answer type"), h("dd", {}, rec.answer_type ? badge(rec.answer_type) : ""),
        h("dt", {}, "Question category"), h("dd", {}, rec.question_category || "–"),
        h("dt", {}, "Intents"), h("dd", {}, (rec.intents || []).join(", ") || "–"),
        h("dt", {}, "Privacy check"), h("dd", {}, rec.privacy ? (rec.privacy.allowed ? "allowed" : `refused (${rec.privacy.code})`) : "–"),
        h("dt", {}, "Wording check"), h("dd", {}, v.ok === undefined ? "–" : (v.ok ? "passed" : (v.fallback || "rejected")) + (v.issues?.length ? ` – ${v.issues.join("; ")}` : "")),
        h("dt", {}, "Language model"), h("dd", {}, rec.llm ? `${rec.llm.backend} (${rec.llm.model})${rec.llm.used_for_wording ? ", wording used" : ", template wording"}` : "–"),
        h("dt", {}, "Latency"), h("dd", {}, rec.latency_ms !== undefined ? `${rec.latency_ms} ms` : "–"),
        h("dt", {}, "Student"), h("dd", { class: "mono" }, rec.student_id_hash ? `hash ${rec.student_id_hash}` : "anonymous")),
      timings.length ? h("details", {}, h("summary", {}, "Pipeline steps"), h("div", { class: "table-wrap" }, h("table", {},
        h("tbody", {}, timings.map((t) => h("tr", {}, h("td", { class: "mono" }, t.node), h("td", {}, t.ms !== undefined ? `${t.ms} ms` : ""), h("td", { class: "small muted" }, t.summary || ""))))))) : null,
      h("details", {}, h("summary", {}, "Full record (JSON)"), json(rec)));
  } catch (ex) {
    body.replaceChildren(alertBox("error", ex.message));
  }
}

// ------------------------------------------------------------------------------------------------
// My records
// ------------------------------------------------------------------------------------------------
async function renderRecords(main) {
  main.replaceChildren(h("div", { class: "pending" }, h("span", { class: "spinner" }), "Loading your records…"));
  let o;
  try { o = await api("/me/overview"); } catch (ex) { main.replaceChildren(alertBox("error", ex.message)); return; }
  const p = o.profile;
  const rule = o.min_attendance_rule;
  const threshold = rule && rule.value !== undefined ? parseFloat(rule.value) : null;
  const att = o.attendance;
  const stat = (label, value, sub) => h("div", { class: "card stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), sub ? h("div", { class: "muted small" }, sub) : null);

  const askNeeded = (code) => {
    sessionStorage.setItem("univ-assistant.prefill", `Am I eligible to appear in the exam for ${code} and if not, how many classes do I need to attend?`);
    location.hash = "#/ask";
  };

  const attendanceCard = att && att.courses?.length ? h("section", { class: "card" },
    h("div", { class: "row", style: "margin-bottom:.6rem" }, h("h3", {}, "Attendance this semester"), h("span", { class: "spacer" }),
      rule ? h("span", { class: "muted small" }, `Minimum ${rule.display} (${rule.rule_id}, ${rule.source_doc_id} §${rule.source_section})`) : null),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Course"), h("th", {}, "Attended"), h("th", { style: "width:35%" }, "Attendance"), h("th", {}, ""))),
      h("tbody", {}, att.courses.map((c) => {
        const low = threshold !== null && c.attendance_pct < threshold;
        const bar = h("div", { class: `bar${low ? " low" : ""}`, role: "img", "aria-label": `${c.attendance_pct}%` }, h("i", { style: `width:${Math.min(100, c.attendance_pct)}%` }));
        if (threshold !== null) bar.append(h("span", { class: "mark", style: `left:${Math.min(100, threshold)}%` }));
        return h("tr", {},
          h("td", {}, h("div", { style: "font-weight:600" }, c.course_code), h("div", { class: "muted small" }, c.course_name || "")),
          h("td", {}, `${c.classes_attended} / ${c.classes_held}`),
          h("td", {}, h("div", { class: "row", style: "flex-wrap:nowrap" }, h("div", { style: "flex:1" }, bar), h("b", {}, `${c.attendance_pct}%`))),
          h("td", {}, low ? h("button", { class: "btn sm", type: "button", onclick: () => askNeeded(c.course_code) }, "What do I need?")
            : threshold !== null ? h("span", { class: "pill ok" }, "On track") : null));
      }))))) : h("section", { class: "card" }, h("h3", {}, "Attendance"), h("p", { class: "muted" }, "No attendance records found."));

  const res = o.results;
  const resultPill = (r) => h("span", { class: `pill ${r === "PASS" ? "ok" : r === "FAIL" || r === "DETAINED" ? "bad" : "warn"}` }, r);
  const resultsCard = res && res.course_status?.length ? h("section", { class: "card" },
    h("h3", { style: "margin-bottom:.6rem" }, "Results"),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Course"), h("th", {}, "Latest exam"), h("th", {}, "Attempts"), h("th", {}, "Result"))),
      h("tbody", {}, res.course_status.map((c) => h("tr", {},
        h("td", {}, h("div", { style: "font-weight:600" }, c.course_code), h("div", { class: "muted small" }, c.course_name || "")),
        h("td", {}, `${c.latest_session} · ${c.latest_exam_type.toLowerCase()}`),
        h("td", {}, c.attempts),
        h("td", {}, resultPill(c.current_result)))))))) : h("section", { class: "card" }, h("h3", {}, "Results"), h("p", { class: "muted" }, "No results recorded yet."));

  main.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("h2", {}, p.full_name || p.student_id),
      h("p", { class: "muted" }, [p.student_id, p.programme, p.batch_year ? `Batch ${p.batch_year}` : null, p.current_semester ? `Semester ${p.current_semester}` : null].filter(Boolean).join(" · ")))),
    h("div", { class: "stats" },
      stat("CGPA", o.cgpa !== null && o.cgpa !== undefined ? Number(o.cgpa).toFixed(2) : "–", "Official record"),
      stat("Overall attendance", att?.overall_pct !== undefined && att?.overall_pct !== null ? `${att.overall_pct}%` : "–", "Current semester courses"),
      stat("Active backlogs", String(o.active_backlogs ?? "–"), res?.backlog_courses?.length ? res.backlog_courses.join(", ") : "None")),
    attendanceCard, resultsCard,
    h("p", { class: "muted small", style: "margin-top:1rem" }, `Figures as of ${o.as_of}. Percentages are calculated from classes held and attended; nothing here is estimated by the language model.`));
}

// ------------------------------------------------------------------------------------------------
// Sources
// ------------------------------------------------------------------------------------------------
function renderSources(main) {
  const date = h("input", { type: "date", class: "input", value: todayISO(), style: "width:auto" });
  const body = h("div");
  const STATUS = { effective: ["ok", "In force"], upcoming: ["info", "Upcoming"], expired: ["warn", "Expired"] };
  const statusPill = (s) => { const [cls, label] = STATUS[s] || ["", s || "–"]; return h("span", { class: `pill ${cls}` }, label); };
  async function load() {
    body.replaceChildren(h("div", { class: "pending" }, h("span", { class: "spinner" }), "Loading…"));
    try {
      const rows = await api(`/sources?as_of=${encodeURIComponent(date.value || todayISO())}`, { auth: false });
      body.replaceChildren(h("div", { class: "card table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Level", "Document", "Issuer", "Version", "In force", "Scope", "Status", "Chunks"].map((t) => h("th", {}, t)))),
        h("tbody", {}, rows.sort((a, b) => a.authority_level - b.authority_level || a.doc_id.localeCompare(b.doc_id)).map((d) => h("tr", {},
          h("td", {}, h("span", { class: "level", title: d.authority_label }, d.authority_level)),
          h("td", {}, h("div", { style: "font-weight:600" }, d.title), h("div", { class: "muted small mono" }, d.doc_id),
            d.supersedes ? h("div", { class: "small muted" }, `Supersedes ${d.supersedes}`) : null),
          h("td", { class: "small" }, d.issuer),
          h("td", {}, d.version),
          h("td", { class: "small" }, `${d.effective_from} → ${d.effective_to || "open"}`),
          h("td", { class: "small" }, `${d.scope_programmes} · ${d.scope_batches}`),
          h("td", {}, statusPill(d.status_as_of)),
          h("td", {}, d.chunks_indexed, d.ocr_used ? h("span", { class: "pill", style: "margin-left:.35rem" }, "OCR") : null)))))));
    } catch (ex) {
      body.replaceChildren(alertBox("error", ex.message));
    }
  }
  date.addEventListener("change", load);
  main.replaceChildren(
    h("div", { class: "page-head" },
      h("div", {}, h("h2", {}, "Source register"), h("p", { class: "muted" }, "Every document the assistant may cite, with its authority level (1 is highest) and validity window.")),
      h("span", { class: "spacer" }), h("label", { class: "row small muted" }, "Status as of", date)),
    body);
  load();
}

// ------------------------------------------------------------------------------------------------
// Admin
// ------------------------------------------------------------------------------------------------
function renderAdmin(main) {
  main.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("h2", {}, "Administration"), h("p", { class: "muted" }, "Add documents, load student data and inspect audit records."))),
    ingestCard(), loadCard(), auditLookupCard());
}

function ingestCard() {
  const out = h("div");
  const f = (name, label, attrs = {}) => h("label", { class: "field" }, h("span", {}, label), h("input", { class: "input", name, ...attrs }));
  const sel = (name, label, options, value) => h("label", { class: "field" }, h("span", {}, label),
    h("select", { class: "input", name }, options.map(([v, t]) => h("option", { value: v, selected: v === value ? true : null }, t))));
  const form = h("form", { class: "stack" },
    h("label", { class: "field" }, h("span", {}, "Document (PDF, DOCX, Markdown or text)"), h("input", { class: "input", type: "file", name: "file", required: true, accept: ".pdf,.docx,.md,.txt" })),
    h("div", { class: "grid" },
      f("doc_id", "Document ID", { required: true, placeholder: "ACAD-2027-01", pattern: "[A-Za-z0-9._-]+" }),
      f("title", "Title", { required: true }),
      f("issuer", "Issuer", { required: true }),
      sel("authority_level", "Authority level", [["1", "1 · Regulation"], ["2", "2 · Circular / notification"], ["3", "3 · Office notice"], ["4", "4 · Department / handbook"], ["5", "5 · Unofficial"]], "2"),
      sel("doc_type", "Type", ["regulation", "circular", "notice", "faq", "handbook", "unofficial"].map((t) => [t, t]), "circular"),
      f("version", "Version", { required: true, value: "1.0" }),
      f("effective_from", "Effective from", { type: "date", required: true, value: todayISO() }),
      f("effective_to", "Effective to (optional)", { type: "date" }),
      f("supersedes", "Supersedes (optional)", { placeholder: "ACAD-REG-2024#7.2" }),
      f("scope_programmes", "Programmes", { value: "ALL" }),
      f("scope_batches", "Batches", { value: "ALL" })),
    out,
    h("div", {}, h("button", { class: "btn primary", type: "submit" }, "Add document")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const meta = { provenance: "uploaded through the web interface", retrieved_on: todayISO(), synthetic: "N" };
    for (const k of ["doc_id", "title", "issuer", "doc_type", "version", "effective_from", "effective_to", "supersedes", "scope_programmes", "scope_batches"]) {
      const v = String(fd.get(k) || "").trim();
      if (v) meta[k] = v;
    }
    meta.authority_level = Number(fd.get("authority_level"));
    const payload = new FormData();
    payload.append("file", fd.get("file"));
    payload.append("metadata", JSON.stringify(meta));
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true; out.replaceChildren(h("div", { class: "pending" }, h("span", { class: "spinner" }), "Reading and indexing the document…"));
    try {
      const r = await api("/ingest", { method: "POST", form: payload });
      out.replaceChildren(alertBox("success", `${r.doc_id}: ${r.status}, ${r.chunks_indexed} chunks indexed, ${r.rules_registered} rules extracted${r.ocr_used ? ", OCR used" : ""}.` +
        (r.warnings?.length ? ` Warnings: ${r.warnings.join("; ")}` : "")));
    } catch (ex) {
      out.replaceChildren(alertBox("error", ex.message));
    } finally { btn.disabled = false; }
  });
  return h("section", { class: "card" }, h("h3", { style: "margin-bottom:.8rem" }, "Add a document"), form);
}

function loadCard() {
  const out = h("div");
  const names = ["courses", "students", "attendance", "results", "rules"];
  const form = h("form", { class: "stack" },
    h("div", { class: "grid" }, names.map((n) => h("label", { class: "field" }, h("span", {}, `${n}.csv`), h("input", { class: "input", type: "file", name: n, accept: ".csv,text/csv" })))),
    out, h("div", {}, h("button", { class: "btn primary", type: "submit" }, "Load data")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    for (const n of names) { const file = form.elements[n].files[0]; if (file) fd.append(n, file); }
    if (![...fd.keys()].length) { out.replaceChildren(alertBox("error", "Choose at least one CSV file.")); return; }
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true; out.replaceChildren(h("div", { class: "pending" }, h("span", { class: "spinner" }), "Loading…"));
    try {
      const r = await api("/admin/load", { method: "POST", form: fd });
      out.replaceChildren(alertBox("success", `Loaded. Students in the database: ${r.students_total}.`), json(r.reports));
    } catch (ex) {
      out.replaceChildren(alertBox("error", ex.message));
    } finally { btn.disabled = false; }
  });
  return h("section", { class: "card" }, h("h3", { style: "margin-bottom:.8rem" }, "Load student data (CSV)"), form);
}

function auditLookupCard() {
  const input = h("input", { class: "input", placeholder: "Trace ID, e.g. 2a55592b", pattern: "[A-Za-z0-9]{1,32}", required: true, style: "max-width:260px" });
  const form = h("form", { class: "row", onsubmit: (e) => { e.preventDefault(); openAudit(input.value.trim()); } },
    input, h("button", { class: "btn", type: "submit" }, "Open audit record"));
  return h("section", { class: "card" }, h("h3", { style: "margin-bottom:.8rem" }, "Audit records"), form);
}

// ------------------------------------------------------------------------------------------------
// Change password
// ------------------------------------------------------------------------------------------------
function openChangePassword() {
  const out = h("div");
  const cur = h("input", { class: "input", type: "password", autocomplete: "current-password", required: true });
  const nw = h("input", { class: "input", type: "password", autocomplete: "new-password", required: true, minlength: 8 });
  const again = h("input", { class: "input", type: "password", autocomplete: "new-password", required: true, minlength: 8 });
  const btn = h("button", { class: "btn primary", type: "submit" }, "Change password");
  const form = h("form", { class: "stack" },
    h("label", { class: "field" }, h("span", {}, "Current password"), cur),
    h("label", { class: "field" }, h("span", {}, "New password (at least 8 characters)"), nw),
    h("label", { class: "field" }, h("span", {}, "Repeat the new password"), again),
    out, h("div", {}, btn));
  const dlg = dialog("Change password", form);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (nw.value !== again.value) { out.replaceChildren(alertBox("error", "The new passwords do not match.")); return; }
    btn.disabled = true;
    try {
      await api("/auth/change-password", { method: "POST", body: { current_password: cur.value, new_password: nw.value } });
      out.replaceChildren(alertBox("success", "Password changed."));
      setTimeout(() => dlg.close(), 1200);
    } catch (ex) {
      out.replaceChildren(alertBox("error", ex.message));
    } finally { btn.disabled = false; }
  });
}

// ------------------------------------------------------------------------------------------------
// Start
// ------------------------------------------------------------------------------------------------
window.addEventListener("hashchange", () => { if (state.token) renderShell(); });

(async function start() {
  loadSession();
  if (!state.token) { renderLogin(); return; }
  try {
    state.user = await api("/auth/me");  // also catches tokens signed before an API restart
    saveSession();
    armExpiry();
    renderShell();
  } catch (ex) {
    if (ex.status !== 401) renderLogin(ex.message);
  }
})();
