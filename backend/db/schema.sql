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

CREATE TABLE IF NOT EXISTS run_jira_data (
    id                  SERIAL          PRIMARY KEY,
    run_id              VARCHAR(8)      REFERENCES runs(id) UNIQUE,
    ticket_title        VARCHAR(500),
    ticket_description  TEXT,
    staging_url         VARCHAR(1000),
    ticket_status       VARCHAR(100),
    assignee            VARCHAR(255),
    subtasks            JSONB,
    attachments         JSONB,
    comments            JSONB,
    prd_text            TEXT,
    design_links        JSONB,
    created_at          TIMESTAMP       DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS run_figma_data (
    id                  SERIAL          PRIMARY KEY,
    run_id              VARCHAR(8)      REFERENCES runs(id) UNIQUE,
    figma_url           VARCHAR(1000),
    file_key            VARCHAR(100),
    node_id             VARCHAR(100),
    file_name           VARCHAR(500),
    file_last_modified  VARCHAR(100),
    pages               JSONB,
    node_name           VARCHAR(500),
    node_type           VARCHAR(100),
    node_children       JSONB,
    exported_images     JSONB,
    export_errors       JSONB,
    created_at          TIMESTAMP       DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS run_browser_data (
    id                      SERIAL          PRIMARY KEY,
    run_id                  VARCHAR(8)      REFERENCES runs(id) UNIQUE,
    urls_visited            JSONB,
    page_titles             JSONB,
    screenshot_paths        JSONB,
    video_path              VARCHAR(500),
    page_content            TEXT,
    interactive_elements    JSONB,
    created_at              TIMESTAMP       DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS run_token_usage (
    id              SERIAL          PRIMARY KEY,
    run_id          VARCHAR(8)      REFERENCES runs(id),
    agent_name      VARCHAR(50)     NOT NULL,
    model           VARCHAR(100),
    input_tokens    INTEGER         DEFAULT 0,
    output_tokens   INTEGER         DEFAULT 0,
    cost_usd        NUMERIC(10,6)   DEFAULT 0,
    created_at      TIMESTAMP       DEFAULT NOW()
);
