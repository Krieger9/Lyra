CREATE TABLE IF NOT EXISTS personalities (
    id UUID PRIMARY KEY,
    library VARCHAR(255) NOT NULL,
    name VARCHAR(120) NOT NULL,
    revision INTEGER NOT NULL,
    template TEXT NOT NULL,
    goals JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_personalities_library ON personalities(library);

-- Curated, library-wide persona knowledge is separate from owner-scoped chat memories.
CREATE TABLE IF NOT EXISTS personality_memories (
    id UUID PRIMARY KEY,
    personality_id UUID NOT NULL REFERENCES personalities(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    source_memory_id UUID REFERENCES memories(id) ON DELETE SET NULL,
    position INTEGER NOT NULL,
    search_document TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_personality_memories_personality ON personality_memories(personality_id);
CREATE INDEX IF NOT EXISTS ix_personality_memories_search ON personality_memories USING GIN(search_document);
CREATE TABLE IF NOT EXISTS personality_memory_embeddings (
    memory_id UUID NOT NULL REFERENCES personality_memories(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding public.vector(3072) NOT NULL,
    PRIMARY KEY (memory_id, model, dimensions)
);
