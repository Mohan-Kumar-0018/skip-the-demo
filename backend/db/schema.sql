CREATE TABLE IF NOT EXISTS runs (
    id              VARCHAR(8)   PRIMARY KEY,
    ticket_id       VARCHAR(50)  NOT NULL,
    feature_name    VARCHAR(255),
    status          VARCHAR(50)  DEFAULT 'running',
    stage           VARCHAR(255),
    progress        INTEGER      DEFAULT 0,
    created_at      TIMESTAMP    DEFAULT NOW(),
    completed_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS run_steps (
    id              SERIAL       PRIMARY KEY,
    run_id          VARCHAR(8)   REFERENCES runs(id),
    step_name       VARCHAR(100),
    step_status     VARCHAR(50),
    updated_at      TIMESTAMP    DEFAULT NOW(),
    UNIQUE(run_id, step_name)
);

CREATE TABLE IF NOT EXISTS run_results (
    id              SERIAL       PRIMARY KEY,
    run_id          VARCHAR(8)   REFERENCES runs(id) UNIQUE,
    design_score    INTEGER,
    deviations      JSONB,
    summary         TEXT,
    release_notes   TEXT,
    video_path      VARCHAR(500),
    screenshots     JSONB,
    slack_sent      BOOLEAN      DEFAULT FALSE,
    created_at      TIMESTAMP    DEFAULT NOW()
);
