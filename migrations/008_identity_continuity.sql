ALTER TABLE personalities ADD COLUMN IF NOT EXISTS constitution TEXT NOT NULL DEFAULT '';
ALTER TABLE personalities ADD COLUMN IF NOT EXISTS persona TEXT NOT NULL DEFAULT '';
ALTER TABLE personalities ADD COLUMN IF NOT EXISTS continuity_enabled BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS personality_versions (
    personality_id UUID NOT NULL REFERENCES personalities(id),
    revision INTEGER NOT NULL,
    document JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (personality_id, revision)
);
INSERT INTO personality_versions (personality_id, revision, document)
SELECT p.id, p.revision, jsonb_build_object('id', p.id, 'name', p.name, 'revision', p.revision,
    'template', p.template, 'constitution', p.constitution, 'persona', p.persona,
    'continuityEnabled', p.continuity_enabled, 'goals', p.goals, 'memories',
    COALESCE((SELECT jsonb_agg(jsonb_build_object('id', m.id, 'content', m.content,
        'sourceMemoryId', m.source_memory_id) ORDER BY m.position, m.id)
        FROM personality_memories m WHERE m.personality_id = p.id), '[]'::jsonb))
FROM personalities p ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS identity_branches (
    id UUID PRIMARY KEY,
    library VARCHAR(255) NOT NULL,
    owner TEXT NOT NULL,
    personality_id UUID NOT NULL REFERENCES personalities(id),
    revision INTEGER NOT NULL,
    document JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (library, owner, personality_id)
);
CREATE TABLE IF NOT EXISTS identity_versions (
    branch_id UUID NOT NULL REFERENCES identity_branches(id),
    revision INTEGER NOT NULL,
    document JSONB NOT NULL,
    reason TEXT NOT NULL,
    source JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (branch_id, revision)
);
CREATE TABLE IF NOT EXISTS identity_events (
    id UUID PRIMARY KEY,
    branch_id UUID NOT NULL REFERENCES identity_branches(id),
    revision INTEGER NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_identity_events_branch ON identity_events(branch_id, revision DESC);
