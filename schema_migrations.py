"""Ordered PostgreSQL migrations for tables owned by Lyra."""
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, text

from agent_store import builtin_protocol_id


def _schema(connection, name):
    return connection.get_execution_options().get("schema_translate_map", {}).get(name, name)


@contextmanager
def schema_transaction(engine):
    with engine.begin() as connection:
        # All Lyra workers acquire this transaction lock before either Studio bootstraps.
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtext('lyra.schema'), 1)")
        yield connection


def _baseline(connection):
    path = Path(__file__).with_name("migrations") / "001_lyra_tables.sql"
    quote = connection.dialect.identifier_preparer.quote_identifier
    connection.exec_driver_sql(path.read_text(encoding="utf-8").format(
        prompt_schema=quote(_schema(connection, "prompt_studio"))))


def _agent_inputs_and_outputs(connection):
    connection.exec_driver_sql("""
        ALTER TABLE agent_definitions
            ALTER COLUMN prompt_name DROP NOT NULL,
            ALTER COLUMN prompt_version DROP NOT NULL,
            ADD COLUMN IF NOT EXISTS output_schema JSONB,
            ADD COLUMN IF NOT EXISTS provider_id UUID REFERENCES providers(id),
            ADD COLUMN IF NOT EXISTS model_name VARCHAR(255)
    """)
    checks = inspect(connection).get_check_constraints(
        "agent_definitions", schema=_schema(connection, "agent_studio"))
    existing = {check["name"] for check in checks}
    for name, expression in (
        ("agent_prompt_reference_complete", "(prompt_name IS NULL) = (prompt_version IS NULL)"),
        ("agent_model_selection_complete", "(provider_id IS NULL) = (model_name IS NULL)"),
    ):
        if name not in existing:
            connection.exec_driver_sql(f"ALTER TABLE agent_definitions ADD CONSTRAINT {name} CHECK ({expression})")


def _provider_catalog(connection):
    missing_models = connection.exec_driver_sql("""
        SELECT library, jsonb_array_elements_text(models) FROM model_groups
        EXCEPT SELECT library, name FROM models
    """)
    rows = [{"id": uuid4(), "library": library, "name": name} for library, name in missing_models]
    if rows:
        connection.execute(text("""INSERT INTO models (id, library, name, revision)
                                   VALUES (:id, :library, :name, 1) ON CONFLICT (library, name) DO NOTHING"""), rows)
    connection.exec_driver_sql("ALTER TABLE providers ADD COLUMN IF NOT EXISTS protocol_id UUID REFERENCES protocols(id)")
    libraries = connection.exec_driver_sql("SELECT DISTINCT library FROM providers WHERE protocol_id IS NULL").scalars()
    for library in libraries:
        for adapter, label in (("ollama", "Ollama native"), ("openai", "OpenAI Chat Completions"),
                               ("anthropic", "Claude Messages")):
            connection.execute(text("""
                INSERT INTO protocols (id, library, name, adapter, revision)
                VALUES (:id, :library, :label, :adapter, 1) ON CONFLICT DO NOTHING
            """), {"id": builtin_protocol_id(library, adapter), "library": library, "label": label, "adapter": adapter})
        connection.execute(text("""
            UPDATE providers SET protocol_id = :id WHERE library = :library AND protocol_id IS NULL
        """), {"id": builtin_protocol_id(library, "ollama"), "library": library})
    connection.exec_driver_sql("ALTER TABLE providers ALTER COLUMN protocol_id SET NOT NULL")


def _conversation_state(connection):
    from conversation_state import checkpoints
    checkpoints.create(connection, checkfirst=True)


def _memories(connection):
    connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    path = Path(__file__).with_name("migrations") / "005_memories.sql"
    connection.exec_driver_sql(path.read_text(encoding="utf-8"))


def _memory_turns(connection):
    path = Path(__file__).with_name("migrations") / "006_memory_turns.sql"
    connection.exec_driver_sql(path.read_text(encoding="utf-8"))


def _personalities(connection):
    path = Path(__file__).with_name("migrations") / "007_personalities.sql"
    connection.exec_driver_sql(path.read_text(encoding="utf-8"))


# Append revisions here. The recorded sequence is part of the database format.
MIGRATIONS = ((1, _baseline), (2, _agent_inputs_and_outputs), (3, _provider_catalog),
              (4, _conversation_state), (5, _memories), (6, _memory_turns), (7, _personalities))


def apply_migrations(connection):
    """Apply missing revisions inside the caller's locked schema transaction."""
    quote = connection.dialect.identifier_preparer.quote_identifier
    schema = quote(_schema(connection, "agent_studio"))
    connection.exec_driver_sql(f"SET LOCAL search_path TO {schema}, pg_temp")
    if not inspect(connection).has_table("lyra_schema_migrations", schema=_schema(connection, "agent_studio")):
        connection.exec_driver_sql("""
            CREATE TABLE lyra_schema_migrations (
                revision INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
    applied = connection.exec_driver_sql("SELECT revision FROM lyra_schema_migrations ORDER BY revision").scalars().all()
    if applied != [revision for revision, _ in MIGRATIONS][:len(applied)]:
        raise RuntimeError("Lyra schema migration history is incompatible with this application version.")
    for revision, migrate in MIGRATIONS[len(applied):]:
        migrate(connection)
        connection.execute(text("INSERT INTO lyra_schema_migrations (revision) VALUES (:revision)"), {"revision": revision})
