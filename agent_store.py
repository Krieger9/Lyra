"""PostgreSQL persistence for Lyra's reusable agent definitions."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, Field, StringConstraints
from prompt_studio import NotFoundError
from prompt_studio.sqlalchemy_storage import versions
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKeyConstraint, Integer, MetaData, String, Table, Uuid, inspect, text
from sqlalchemy.dialects.postgresql import JSONB, insert

from output_contract import OutputSchema

metadata = MetaData()
DEFAULT_INSTRUCTIONS = "Instructions.default"
DEFAULT_INSTRUCTIONS_TEXT = "You are a helpful agent"
versions.to_metadata(metadata)
definitions = Table(
    "agent_definitions", metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", String(120), nullable=False),
    Column("library", String(255), nullable=False),
    Column("instructions_name", String(255), nullable=False),
    Column("instructions_version", Integer, nullable=False),
    Column("prompt_name", String(255), nullable=True),
    Column("prompt_version", Integer, nullable=True),
    Column("output_schema", JSONB(none_as_null=True)),
    Column("revision", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("(prompt_name IS NULL) = (prompt_version IS NULL)", name="agent_prompt_reference_complete"),
    ForeignKeyConstraint(["library", "instructions_name", "instructions_version"],
                         ["prompt_studio.prompt_versions.library", "prompt_studio.prompt_versions.name",
                          "prompt_studio.prompt_versions.version"]),
    ForeignKeyConstraint(["library", "prompt_name", "prompt_version"],
                         ["prompt_studio.prompt_versions.library", "prompt_studio.prompt_versions.name",
                          "prompt_studio.prompt_versions.version"]),
    schema="agent_studio",
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
PromptName = Annotated[str, StringConstraints(min_length=1, max_length=255, pattern=r"\S")]


class PromptReference(BaseModel):
    name: PromptName
    version: int = Field(ge=1)


class AgentDefinition(BaseModel):
    name: Name
    instructions: PromptReference | None = None
    # A null reference means pass the incoming flow text through directly.
    prompt: PromptReference | None = None
    outputSchema: OutputSchema | None = None


class AgentUpdate(AgentDefinition):
    revision: int = Field(ge=1)


class PromptWrite(BaseModel):
    name: PromptName
    text: str = Field(min_length=1, max_length=100000)


def create_schema(engine):
    with engine.begin() as connection:
        definitions.create(connection, checkfirst=True)
        schema = connection.get_execution_options().get("schema_translate_map", {}).get("agent_studio", "agent_studio")
        inspector = inspect(connection)
        columns = inspector.get_columns(definitions.name, schema=schema)
        quote = connection.dialect.identifier_preparer.quote
        table_name = f"{quote(schema)}.{quote(definitions.name)}"
        if not any(column["name"] == "output_schema" for column in columns):
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS output_schema JSONB"))
        # Upgrade definitions saved before raw input became an option.
        if any(not column["nullable"] for column in columns if column["name"] in ("prompt_name", "prompt_version")):
            connection.execute(text(f"ALTER TABLE {table_name} ALTER COLUMN prompt_name DROP NOT NULL, "
                                    "ALTER COLUMN prompt_version DROP NOT NULL"))
        checks = inspector.get_check_constraints(definitions.name, schema=schema)
        if not any(check["name"] == "agent_prompt_reference_complete" for check in checks):
            connection.execute(text(f"ALTER TABLE {table_name} ADD CONSTRAINT agent_prompt_reference_complete "
                                    "CHECK ((prompt_name IS NULL) = (prompt_version IS NULL))"))


def seed_defaults(library):
    # Concurrent startups can seed version 1 without publishing extra revisions.
    with library.storage.engine.begin() as connection:
        connection.execute(insert(versions).values(
            library=library.name, name=DEFAULT_INSTRUCTIONS, version=1,
            text=DEFAULT_INSTRUCTIONS_TEXT, created_at=datetime.now(timezone.utc),
        ).on_conflict_do_nothing())


def serialize_agent(row):
    return {
        "id": str(row["id"]), "name": row["name"], "revision": row["revision"],
        "instructions": {"name": row["instructions_name"], "version": row["instructions_version"]},
        "prompt": None if row["prompt_name"] is None else {"name": row["prompt_name"], "version": row["prompt_version"]},
        "outputSchema": row["output_schema"],
    }


def agent_values(body, library):
    instructions = body.instructions
    if instructions is None:
        default = library.get(DEFAULT_INSTRUCTIONS)
        instructions = PromptReference(name=default.name, version=default.version)
    for reference in (instructions, body.prompt):
        if reference is None:
            continue
        try:
            library.get(reference.name, reference.version)
        except NotFoundError:
            raise HTTPException(422, "Choose an existing PromptStudio version.")
    return {
        "name": body.name, "library": library.name,
        "instructions_name": instructions.name, "instructions_version": instructions.version,
        "prompt_name": body.prompt.name if body.prompt else None,
        "prompt_version": body.prompt.version if body.prompt else None,
        "output_schema": body.outputSchema.model_dump() if body.outputSchema else None,
        "updated_at": datetime.now(timezone.utc),
    }
