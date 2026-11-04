CREATE TABLE IF NOT EXISTS memory_turns (
    memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES flow_runs(id) ON DELETE CASCADE,
    relation TEXT NOT NULL CHECK (relation IN ('created', 'reinforced', 'recalled')),
    node TEXT NOT NULL DEFAULT '',
    evidence TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (memory_id, run_id, relation, node)
);
CREATE INDEX IF NOT EXISTS ix_memory_turns_run ON memory_turns (run_id);

-- Preserve the original source of existing memories, including recovered memories.
INSERT INTO memory_turns (memory_id, run_id, relation, node, evidence, recorded_at)
SELECT m.id, r.id, 'created', coalesce(m.source->>'node', ''), m.evidence, m.created_at
FROM memories m JOIN flow_runs r ON r.id::text = m.source->>'runId' AND r.library = m.library
ON CONFLICT DO NOTHING;

-- Successful historical recall events already contain stable memory identifiers.
INSERT INTO memory_turns (memory_id, run_id, relation, node, recorded_at)
SELECT m.id, r.id, 'recalled', coalesce(e.node, 'api-input'), e.at
FROM flow_run_events e JOIN flow_runs r ON r.id = e.run_id AND r.status = 'completed'
JOIN flow_state_turns c ON c.run_id = r.id AND c.library = r.library
CROSS JOIN LATERAL jsonb_array_elements_text(
    CASE WHEN jsonb_typeof(e.data::jsonb->'ids') = 'array' THEN e.data::jsonb->'ids' ELSE '[]'::jsonb END
) recalled(id)
JOIN memories m ON m.id::text = recalled.id AND m.library = r.library AND m.owner = c.owner
WHERE e.kind = 'memory.recalled'
ON CONFLICT DO NOTHING;
