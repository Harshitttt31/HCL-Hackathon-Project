-- ============================================================================
-- Annex C fixed schema (required tables and columns are NOT renamed or removed).
-- Extra columns/tables are additive and clearly marked.
-- ============================================================================
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS students (
    student_id        TEXT PRIMARY KEY CHECK (student_id GLOB 'S[0-9][0-9][0-9][0-9]'),
    full_name         TEXT NOT NULL,
    programme         TEXT NOT NULL,
    batch_year        INTEGER NOT NULL CHECK (batch_year BETWEEN 1990 AND 2100),
    current_semester  INTEGER NOT NULL CHECK (current_semester BETWEEN 1 AND 10),
    cgpa              REAL CHECK (cgpa IS NULL OR (cgpa >= 0.0 AND cgpa <= 10.0)),
    active_backlogs   INTEGER NOT NULL DEFAULT 0 CHECK (active_backlogs >= 0)
);

CREATE TABLE IF NOT EXISTS courses (
    course_code  TEXT PRIMARY KEY,
    course_name  TEXT NOT NULL,
    programme    TEXT NOT NULL,
    semester     INTEGER NOT NULL CHECK (semester BETWEEN 1 AND 10),
    credits      INTEGER NOT NULL CHECK (credits >= 0)
);

CREATE TABLE IF NOT EXISTS attendance (
    student_id        TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    course_code       TEXT NOT NULL REFERENCES courses(course_code) ON DELETE CASCADE,
    classes_held      INTEGER NOT NULL CHECK (classes_held > 0),
    classes_attended  INTEGER NOT NULL CHECK (classes_attended >= 0 AND classes_attended <= classes_held),
    PRIMARY KEY (student_id, course_code)
    -- attendance percentage is computed by tools, never stored
);

CREATE TABLE IF NOT EXISTS results (
    student_id      TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    course_code     TEXT NOT NULL REFERENCES courses(course_code) ON DELETE CASCADE,
    exam_session    TEXT NOT NULL,                                   -- e.g. 2026-MAY
    exam_type       TEXT NOT NULL CHECK (exam_type IN ('REGULAR','SUPPLEMENTARY')),
    internal_marks  INTEGER CHECK (internal_marks IS NULL OR internal_marks >= 0),
    external_marks  INTEGER CHECK (external_marks IS NULL OR external_marks >= 0),
    total_marks     INTEGER CHECK (total_marks IS NULL OR total_marks >= 0),
    max_marks       INTEGER CHECK (max_marks IS NULL OR max_marks > 0),
    result          TEXT NOT NULL CHECK (result IN ('PASS','FAIL','ABSENT','DETAINED')),
    CHECK (internal_marks IS NULL OR external_marks IS NULL OR total_marks IS NULL
           OR total_marks = internal_marks + external_marks),
    CHECK (total_marks IS NULL OR max_marks IS NULL OR total_marks <= max_marks),
    PRIMARY KEY (student_id, course_code, exam_session, exam_type)
);

-- Annex C rule_registry. Thresholds used by tools are read from here, never from code constants.
CREATE TABLE IF NOT EXISTS rule_registry (
    rule_id            TEXT PRIMARY KEY,
    description        TEXT NOT NULL,
    parameter          TEXT NOT NULL,
    operator           TEXT NOT NULL,
    value              TEXT NOT NULL,
    scope_programmes   TEXT NOT NULL DEFAULT 'ALL',
    scope_batches      TEXT NOT NULL DEFAULT 'ALL',
    effective_from     TEXT NOT NULL,
    effective_to       TEXT,
    source_doc_id      TEXT NOT NULL REFERENCES source_register(doc_id) ON DELETE CASCADE,
    source_section     TEXT NOT NULL,
    -- additive columns --
    unit               TEXT NOT NULL DEFAULT '',
    attributes         TEXT,                                          -- JSON for structured extras
    origin             TEXT NOT NULL DEFAULT 'curated' CHECK (origin IN ('curated','extracted')),
    quote              TEXT,                                          -- verbatim supporting text
    chunk_id           TEXT,
    source_page        INTEGER,
    created_at         TEXT
);

-- ============================================================================
-- Additive tables
-- ============================================================================
-- Annex B Source Register. This (not the vector store) is the system of record for authority,
-- scope, versions and supersession.
CREATE TABLE IF NOT EXISTS source_register (
    doc_id            TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    issuer            TEXT NOT NULL,
    authority_level   INTEGER NOT NULL CHECK (authority_level BETWEEN 1 AND 5),
    doc_type          TEXT NOT NULL CHECK (doc_type IN ('regulation','circular','notice','faq','handbook','unofficial')),
    version           TEXT NOT NULL,
    effective_from    TEXT NOT NULL,
    effective_to      TEXT,
    supersedes        TEXT NOT NULL DEFAULT '',
    scope_programmes  TEXT NOT NULL DEFAULT 'ALL',
    scope_batches     TEXT NOT NULL DEFAULT 'ALL',
    provenance        TEXT NOT NULL DEFAULT '',
    retrieved_on      TEXT NOT NULL,
    synthetic         TEXT NOT NULL DEFAULT 'N' CHECK (synthetic IN ('Y','N')),
    -- additive --
    content_hash      TEXT,
    filename          TEXT,
    chunks_indexed    INTEGER NOT NULL DEFAULT 0,
    ocr_used          INTEGER NOT NULL DEFAULT 0,
    parse_warnings    TEXT,
    ingested_at       TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id           TEXT PRIMARY KEY,
    doc_id             TEXT NOT NULL REFERENCES source_register(doc_id) ON DELETE CASCADE,
    ordinal            INTEGER NOT NULL,
    section_number     TEXT NOT NULL DEFAULT '',
    section_title      TEXT NOT NULL DEFAULT '',
    section_path       TEXT NOT NULL DEFAULT '[]',                    -- JSON list of headings
    heading_level      INTEGER NOT NULL DEFAULT 0,
    page               INTEGER,
    kind               TEXT NOT NULL DEFAULT 'paragraph',             -- paragraph | list | table
    text               TEXT NOT NULL,
    embed_text         TEXT NOT NULL,
    table_json         TEXT,
    references_json    TEXT NOT NULL DEFAULT '[]',
    prev_chunk_id      TEXT,
    next_chunk_id      TEXT,
    content_hash       TEXT NOT NULL,
    injection_suspected INTEGER NOT NULL DEFAULT 0,
    confidence         REAL NOT NULL DEFAULT 1.0,
    ocr                INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    trace_id           TEXT PRIMARY KEY,
    ts                 TEXT NOT NULL,
    student_id_hash    TEXT,
    question_hash      TEXT NOT NULL,
    question_category  TEXT,
    answer_type        TEXT,
    latency_ms         INTEGER,
    record_json        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    doc_id    TEXT NOT NULL,
    status    TEXT NOT NULL,
    chunks    INTEGER NOT NULL DEFAULT 0,
    details   TEXT
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    model      TEXT NOT NULL,
    text_hash  TEXT NOT NULL,
    vector     BLOB NOT NULL,
    PRIMARY KEY (model, text_hash)
);

-- ============================================================================
-- Indexes
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance(student_id);
CREATE INDEX IF NOT EXISTS idx_results_student ON results(student_id);
CREATE INDEX IF NOT EXISTS idx_results_student_course ON results(student_id, course_code);
CREATE INDEX IF NOT EXISTS idx_courses_prog_sem ON courses(programme, semester);
CREATE INDEX IF NOT EXISTS idx_rules_parameter ON rule_registry(parameter);
CREATE INDEX IF NOT EXISTS idx_rules_doc ON rule_registry(source_doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_register_level ON source_register(authority_level);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
