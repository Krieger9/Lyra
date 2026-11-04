CREATE TABLE IF NOT EXISTS memories (
    id UUID PRIMARY KEY,
    library VARCHAR(255) NOT NULL,
    owner TEXT NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    content TEXT NOT NULL,
    kind VARCHAR(32) NOT NULL,
    evidence TEXT NOT NULL,
    source JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    search_document TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    UNIQUE (library, owner, fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_memories_owner ON memories (library, owner);
CREATE INDEX IF NOT EXISTS ix_memories_text ON memories USING gin (search_document);

-- Full precision is retained for exact, owner-filtered similarity search.
-- Embedding identity is independent of the memory ID for later backend migrations.
CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions BETWEEN 1 AND 16000),
    embedding public.vector NOT NULL CHECK (public.vector_dims(embedding) = dimensions),
    PRIMARY KEY (memory_id, model, dimensions)
);
