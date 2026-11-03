-- Baseline for databases created before Lyra tracked schema revisions.
CREATE TABLE IF NOT EXISTS models (
    id UUID PRIMARY KEY, library VARCHAR(255) NOT NULL, name VARCHAR(255) NOT NULL,
    revision INTEGER NOT NULL, UNIQUE (library, name)
);
CREATE TABLE IF NOT EXISTS protocols (
    id UUID PRIMARY KEY, library VARCHAR(255) NOT NULL, name VARCHAR(120) NOT NULL,
    adapter VARCHAR(120) NOT NULL, revision INTEGER NOT NULL, UNIQUE (library, name)
);
CREATE TABLE IF NOT EXISTS model_groups (
    id UUID PRIMARY KEY, library VARCHAR(255) NOT NULL, name VARCHAR(120) NOT NULL,
    models JSONB NOT NULL, revision INTEGER NOT NULL, UNIQUE (library, name)
);
CREATE TABLE IF NOT EXISTS providers (
    id UUID PRIMARY KEY, library VARCHAR(255) NOT NULL, name VARCHAR(120) NOT NULL,
    uri VARCHAR(2048) NOT NULL, group_id UUID NOT NULL REFERENCES model_groups(id),
    revision INTEGER NOT NULL, UNIQUE (library, name)
);
CREATE TABLE IF NOT EXISTS agent_definitions (
    id UUID PRIMARY KEY, library VARCHAR(255) NOT NULL, name VARCHAR(120) NOT NULL,
    instructions_name VARCHAR(255) NOT NULL, instructions_version INTEGER NOT NULL,
    prompt_name VARCHAR(255), prompt_version INTEGER, revision INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (library, instructions_name, instructions_version)
        REFERENCES {prompt_schema}.prompt_versions(library, name, version),
    FOREIGN KEY (library, prompt_name, prompt_version)
        REFERENCES {prompt_schema}.prompt_versions(library, name, version)
);
CREATE TABLE IF NOT EXISTS flows (
    id UUID PRIMARY KEY, name VARCHAR(120) NOT NULL, library VARCHAR(255) NOT NULL,
    revision INTEGER NOT NULL, graph JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS flow_runs (
    id UUID PRIMARY KEY, library VARCHAR(255) NOT NULL, flow_id UUID NOT NULL,
    revision INTEGER NOT NULL, source VARCHAR NOT NULL, status VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ, snapshot JSONB NOT NULL,
    result JSON, error VARCHAR
);
CREATE INDEX IF NOT EXISTS ix_flow_runs_history ON flow_runs (library, flow_id, started_at);
CREATE TABLE IF NOT EXISTS flow_run_events (
    run_id UUID NOT NULL REFERENCES flow_runs(id) ON DELETE CASCADE, seq INTEGER NOT NULL,
    node VARCHAR, kind VARCHAR NOT NULL, at TIMESTAMPTZ NOT NULL, elapsed_ms INTEGER NOT NULL,
    data JSON NOT NULL, PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS chat_flow (
    library VARCHAR(255) PRIMARY KEY, snapshot JSONB NOT NULL
);
