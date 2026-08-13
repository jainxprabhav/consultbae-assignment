-- ConsultBae assignment: unified schema
-- Layer 1 = raw source rows (immutable audit trail)
-- Layer 2 = merged golden records

DROP TABLE IF EXISTS audio_submission;
DROP TABLE IF EXISTS person_skill;
DROP TABLE IF EXISTS contact_profile;
DROP TABLE IF EXISTS gig_profile;
DROP TABLE IF EXISTS applicant_profile;
DROP TABLE IF EXISTS review_queue;
DROP TABLE IF EXISTS data_issue;
DROP TABLE IF EXISTS source_record;
DROP TABLE IF EXISTS person;

-- ---------- Layer 2: the golden record ----------
CREATE TABLE person (
    person_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name       TEXT NOT NULL,
    primary_email   TEXT,
    primary_phone   TEXT,          -- normalised to 10 digits
    city            TEXT,          -- canonical form
    skill_category  TEXT,          -- filled in by the n8n LLM flow (Task 2)
    tagged_at       TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_person_phone ON person(primary_phone);
CREATE INDEX idx_person_email ON person(primary_email);

-- ---------- Layer 1: immutable raw rows ----------
CREATE TABLE source_record (
    source_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file      TEXT NOT NULL,   -- source1 / source2 / source3
    source_row_num   INTEGER NOT NULL,-- line number in the original CSV
    person_id        INTEGER,         -- NULL if quarantined
    raw_json         TEXT NOT NULL,   -- the original row, untouched
    match_method     TEXT,            -- phone_exact | email_exact | name_city | new_person | quarantined
    confidence       REAL,
    FOREIGN KEY (person_id) REFERENCES person(person_id)
);

-- ---------- Source-specific attributes ----------
CREATE TABLE applicant_profile (          -- from source1 (Naukri)
    person_id         INTEGER PRIMARY KEY,
    experience_years  REAL,
    ctc_annual_inr    INTEGER,            -- always absolute rupees
    ctc_unit_inferred TEXT,               -- 'lakhs' or 'absolute' - records our assumption
    applied_date      TEXT,               -- ISO YYYY-MM-DD
    applied_date_raw  TEXT,               -- original string, for auditing
    FOREIGN KEY (person_id) REFERENCES person(person_id)
);

CREATE TABLE gig_profile (                -- from source2 (gig workers)
    person_id  INTEGER PRIMARY KEY,
    rate_value REAL,
    rate_unit  TEXT,                      -- 'hr' or 'month' - kept separate, not converted
    status     TEXT,                      -- canonical: active | inactive | paused
    FOREIGN KEY (person_id) REFERENCES person(person_id)
);

CREATE TABLE contact_profile (            -- from source3 (CBNexus)
    person_id          INTEGER PRIMARY KEY,
    verified           INTEGER,           -- 0/1
    projects_completed INTEGER,
    FOREIGN KEY (person_id) REFERENCES person(person_id)
);

CREATE TABLE person_skill (
    person_id  INTEGER NOT NULL,
    skill_name TEXT NOT NULL,             -- lowercased, trimmed
    PRIMARY KEY (person_id, skill_name),
    FOREIGN KEY (person_id) REFERENCES person(person_id)
);

-- ---------- Task 4: the data issues report, generated not written ----------
CREATE TABLE data_issue (
    issue_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file  TEXT,
    source_row   INTEGER,
    issue_type   TEXT NOT NULL,
    detail       TEXT,
    action_taken TEXT NOT NULL,
    detected_at  TEXT DEFAULT (datetime('now'))
);

-- ---------- Ambiguous matches a human must decide ----------
CREATE TABLE review_queue (
    review_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file  TEXT,
    source_row   INTEGER,
    description  TEXT NOT NULL,
    candidates   TEXT,                    -- JSON list of possible person_ids
    reason       TEXT NOT NULL
);

-- ---------- Task 3: audio submissions ----------
CREATE TABLE audio_submission (
    submission_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id      INTEGER,
    submitted_name TEXT NOT NULL,
    submitted_phone TEXT NOT NULL,
    file_path      TEXT NOT NULL,
    duration_sec   REAL,
    sample_rate_khz REAL,
    bitrate_kbps   REAL,
    loudness_db    REAL,
    noise_estimate TEXT,
    submitted_at   TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (person_id) REFERENCES person(person_id)
);