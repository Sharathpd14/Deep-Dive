CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    question    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    loop_count  INTEGER NOT NULL DEFAULT 0,
    report_md   TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS sub_questions (
    run_id       TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id           TEXT NOT NULL,
    text         TEXT NOT NULL,
    queries_json TEXT NOT NULL DEFAULT '[]',
    status       TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS evidence (
    run_id       TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id           TEXT NOT NULL,
    sub_q_id     TEXT,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    domain       TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL,
    tier         REAL NOT NULL DEFAULT 0.5,
    content_hash TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    PRIMARY KEY (run_id, id),
    UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS claims (
    run_id            TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id                TEXT NOT NULL,
    sub_q_id          TEXT NOT NULL,
    text              TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    verdict           TEXT NOT NULL DEFAULT 'pending',
    quote             TEXT NOT NULL DEFAULT '',
    reason            TEXT NOT NULL DEFAULT '',
    round             INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS trail_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ts          TEXT NOT NULL,
    agent       TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    reason      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_evidence_subq ON evidence(run_id, sub_q_id);
CREATE INDEX IF NOT EXISTS idx_claims_subq   ON claims(run_id, sub_q_id);
CREATE INDEX IF NOT EXISTS idx_trail_run     ON trail_events(run_id, id);