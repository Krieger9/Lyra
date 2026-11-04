"""Owner-scoped PostgreSQL memories and hybrid retrieval over one embedding space."""
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import text
from fastapi import HTTPException

from json_mapping import json_text


EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 3072


def schema_name(connection):
    schema = connection.get_execution_options().get("schema_translate_map", {}).get("agent_studio", "agent_studio")
    return connection.dialect.identifier_preparer.quote_identifier(schema)


def save_memories(connection, library, owner, candidates, run_id):
    schema = schema_name(connection)
    links = []
    for candidate in candidates:
        fingerprint = sha256(" ".join(candidate["content"].casefold().split()).encode()).hexdigest()
        row = candidate | {"id": uuid4(), "library": library, "owner": owner, "fingerprint": fingerprint,
                           "model": EMBEDDING_MODEL, "dimensions": EMBEDDING_DIMENSIONS}
        row["source"] = json_text(candidate["source"] | {"runId": str(run_id)})
        row["embedding"] = json_text(candidate["embedding"])
        saved = connection.execute(text(f"""
            INSERT INTO {schema}.memories (id, library, owner, fingerprint, content, kind, evidence, source)
            VALUES (:id, :library, :owner, :fingerprint, :content, :kind, :evidence, CAST(:source AS jsonb))
            ON CONFLICT (library, owner, fingerprint) DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP
            RETURNING id, source
        """), row).mappings().one()
        memory_id = saved["id"]
        connection.execute(text(f"""
            INSERT INTO {schema}.memory_embeddings (memory_id, model, dimensions, embedding)
            VALUES (:memory_id, :model, :dimensions, CAST(:embedding AS public.vector))
            ON CONFLICT (memory_id, model, dimensions) DO NOTHING
        """), row | {"memory_id": memory_id})
        relation = "created" if saved["source"].get("runId") == str(run_id) else "reinforced"
        node = candidate["source"].get("node", "")
        connection.execute(text(f"""
            INSERT INTO {schema}.memory_turns (memory_id, run_id, relation, node, evidence)
            VALUES (:memory_id, :run_id, :relation, :node, :evidence) ON CONFLICT DO NOTHING
        """), {"memory_id": memory_id, "run_id": run_id, "relation": relation,
               "node": node, "evidence": candidate["evidence"]})
        links.append({"id": str(memory_id), "relation": relation, "node": node})
    return links


def record_recalls(connection, library, owner, recalled, run_id):
    schema = schema_name(connection)
    for memory in recalled:
        connection.execute(text(f"""
            INSERT INTO {schema}.memory_turns (memory_id, run_id, relation, node)
            SELECT id, :run_id, 'recalled', 'api-input' FROM {schema}.memories
            WHERE id = :id AND library = :library AND owner = :owner
            ON CONFLICT DO NOTHING
        """), {"run_id": run_id, "id": memory["id"], "library": library, "owner": owner})


def list_memories(library, owner=None, query="", offset=0, limit=50):
    with library.storage.engine.connect() as connection:
        schema = schema_name(connection)
        scope = " AND owner = :owner" if owner is not None else ""
        search = " AND search_document @@ websearch_to_tsquery('english', :query)" if query.strip() else ""
        rows = connection.execute(text(f"""
            SELECT id, content, kind, evidence, source, created_at, last_seen_at
            FROM {schema}.memories WHERE library = :library {scope} {search}
            ORDER BY created_at DESC, id LIMIT :limit OFFSET :offset
        """), {"library": library.name, "owner": owner, "query": query, "limit": limit + 1,
               "offset": offset}).mappings().all()
        return {"memories": [dict(row) for row in rows[:limit]], "hasMore": len(rows) > limit}


def memory_detail(library, memory_id, owner=None, offset=0, limit=50):
    with library.storage.engine.connect() as connection:
        schema = schema_name(connection)
        scope = " AND owner = :owner" if owner is not None else ""
        memory = connection.execute(text(f"""
            SELECT id, content, kind, evidence, source, created_at, last_seen_at
            FROM {schema}.memories WHERE id = :id AND library = :library {scope}
        """), {"id": memory_id, "library": library.name, "owner": owner}).mappings().first()
        if memory is None:
            raise HTTPException(404, "Memory not found.")
        turns = connection.execute(text(f"""
            SELECT t.run_id, t.relation, t.node, t.evidence, t.recorded_at,
                   r.flow_id, r.revision, r.started_at, r.status,
                   c.turn_id, c.conversation_id,
                   (SELECT e.data->'input' FROM {schema}.flow_run_events e
                    WHERE e.run_id = r.id AND e.kind = 'run.started' ORDER BY e.seq LIMIT 1) AS input
            FROM {schema}.memory_turns t JOIN {schema}.memories m ON m.id = t.memory_id
            JOIN {schema}.flow_runs r ON r.id = t.run_id AND r.library = m.library
            LEFT JOIN {schema}.flow_state_turns c ON c.run_id = r.id AND c.owner = m.owner AND c.library = m.library
            WHERE t.memory_id = :id ORDER BY r.started_at, t.run_id, t.relation, t.node
            LIMIT :limit OFFSET :offset
        """), {"id": memory_id, "limit": limit + 1, "offset": offset}).mappings().all()
        return dict(memory) | {"turns": [dict(row) for row in turns[:limit]], "hasMore": len(turns) > limit}


def memories_for_run(connection, library, run_id, owner=None):
    schema = schema_name(connection)
    scope = " AND m.owner = :owner" if owner is not None else ""
    rows = connection.execute(text(f"""
        SELECT m.id, m.content, m.kind, t.relation, t.node, t.evidence, t.recorded_at
        FROM {schema}.memory_turns t JOIN {schema}.memories m ON m.id = t.memory_id
        JOIN {schema}.flow_runs r ON r.id = t.run_id AND r.library = m.library
        WHERE t.run_id = :run_id AND m.library = :library {scope}
        ORDER BY t.relation, m.created_at, m.id, t.node
    """), {"run_id": run_id, "library": library, "owner": owner}).mappings()
    return [dict(row) for row in rows]


def search_memories(library, owner, query, embedding=None, limit=8):
    """Fuse lexical and exact cosine rankings. Apply ownership before either ranking."""
    with library.storage.engine.connect() as connection:
        schema = schema_name(connection)
        params = {"library": library.name, "owner": owner, "query": query, "limit": limit,
                  "candidates": max(40, limit * 4), "model": EMBEDDING_MODEL,
                  "dimensions": EMBEDDING_DIMENSIONS, "embedding": json_text(embedding)}
        lexical = f"""
            SELECT id, row_number() OVER (ORDER BY ts_rank_cd(search_document, q) DESC, id) AS rank
            FROM {schema}.memories, websearch_to_tsquery('english', :query) q
            WHERE library = :library AND owner = :owner AND search_document @@ q
            ORDER BY ts_rank_cd(search_document, q) DESC, id LIMIT :candidates
        """
        semantic = f"""
            SELECT m.id, row_number() OVER (ORDER BY e.embedding OPERATOR(public.<=>)
                CAST(:embedding AS public.vector), m.id) AS rank
            FROM {schema}.memories m JOIN {schema}.memory_embeddings e ON e.memory_id = m.id
            WHERE m.library = :library AND m.owner = :owner
                AND e.model = :model AND e.dimensions = :dimensions
                AND e.embedding OPERATOR(public.<=>) CAST(:embedding AS public.vector) < 0.65
            ORDER BY e.embedding OPERATOR(public.<=>) CAST(:embedding AS public.vector), m.id
            LIMIT :candidates
        """ if embedding is not None else "SELECT NULL::uuid AS id, 0 AS rank WHERE false"
        rows = connection.execute(text(f"""
            WITH lexical AS ({lexical}), semantic AS ({semantic}), ranked AS (
                SELECT id, sum(1.0 / (60 + rank)) AS score FROM (
                    SELECT * FROM lexical UNION ALL SELECT * FROM semantic
                ) matches GROUP BY id
            )
            SELECT m.id, m.content, m.kind, m.evidence, m.source, m.created_at, m.last_seen_at, r.score
            FROM ranked r JOIN {schema}.memories m ON m.id = r.id
            ORDER BY r.score DESC, m.id LIMIT :limit
        """), params).mappings()
        return [dict(row) for row in rows]
