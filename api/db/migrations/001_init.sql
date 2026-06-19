-- api/db/migrations/001_init.sql
CREATE TABLE IF NOT EXISTS dag_flows (
    id          TEXT    PRIMARY KEY,
    stage_plan  JSONB   NOT NULL,
    is_default  BOOLEAN DEFAULT false,
    deprecated  BOOLEAN DEFAULT false,
    created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id                  TEXT    PRIMARY KEY,
    filename            TEXT    NOT NULL,
    submitted_by        TEXT    NOT NULL DEFAULT 'anonymous',
    dag_flow_id         TEXT    REFERENCES dag_flows(id),
    status              TEXT    NOT NULL DEFAULT 'submitted'
                        CHECK(status IN ('submitted','queued','processing','completed','failed')),
    current_stage       TEXT,
    submitted_at        REAL,
    started_at          REAL,
    completed_at        REAL,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    error_msg           TEXT,
    output_srt_path     TEXT,
    corrected_srt_path  TEXT,
    verification_status TEXT    NOT NULL DEFAULT 'unverified'
                        CHECK(verification_status IN ('unverified','in_progress','verified')),
    verified_at         REAL,
    verified_by         TEXT,
    minio_input_key     TEXT,
    minio_processing_key TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id             SERIAL  PRIMARY KEY,
    job_id         TEXT    NOT NULL REFERENCES jobs(id),
    stage          TEXT    NOT NULL,
    status         TEXT    CHECK(status IN ('started','success','failed')),
    retry_attempt  INTEGER NOT NULL DEFAULT 0,
    error_msg      TEXT,
    payload        TEXT,
    ts             REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status    ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_events_job_id  ON events(job_id);
