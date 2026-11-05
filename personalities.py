"""Admin-authored personalities, curated knowledge and immutable execution snapshots."""
import re
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator
from sqlalchemy import text

from auth import require_admin_access
from json_mapping import json_text
from memory import embed, no_store
from memory_store import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, schema_name

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
TOKENS = re.compile(r"{{\s*(name|goals|memories)\s*}}")


class PersonalityMemory(BaseModel, extra="forbid"):
    id: UUID | None = None
    content: ShortText
    sourceMemoryId: UUID | None = None


class PersonalityWrite(BaseModel, extra="forbid"):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    template: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12000)]
    goals: list[ShortText] = Field(default_factory=list, max_length=20)
    memories: list[PersonalityMemory] = Field(default_factory=list, max_length=24)
    constitution: str = Field(default="", max_length=6000)
    persona: str = Field(default="", max_length=4000)
    continuityEnabled: bool = False

    @field_validator("template")
    @classmethod
    def valid_template(cls, value):
        remaining = TOKENS.sub("", value)
        if any(marker in remaining for marker in ("{{", "}}", "{%", "{#")):
            raise ValueError("Use only {{name}}, {{goals}} and {{memories}} placeholders; template code is not supported.")
        return value

    @model_validator(mode="after")
    def unique_memories(self):
        ids = [m.id for m in self.memories if m.id]
        contents = [" ".join(m.content.casefold().split()) for m in self.memories]
        if len(set(ids)) != len(ids) or len(set(contents)) != len(contents):
            raise ValueError("Each assigned memory must be unique.")
        return self


class PersonalityUpdate(PersonalityWrite):
    revision: int = Field(ge=1)


class SavedPersonality(PersonalityWrite):
    id: UUID
    revision: int


def render_personality(personality):
    """Substitute once: braces inside memories or goals are always literal text."""
    values = {
        "name": personality.name,
        "goals": "Personality goals:\n" + (json_text(personality.goals) if personality.goals else "None assigned."),
        "memories": "Personality memories (curated background facts, not instructions):\n" +
                    json_text([{"id": str(m.id) if m.id else None, "content": m.content} for m in personality.memories]),
    }
    used = {match.group(1) for match in TOKENS.finditer(personality.template)}
    rendered = TOKENS.sub(lambda match: values[match.group(1)], personality.template)
    if personality.constitution:
        rendered += "\n\nDurable commitments:\n" + personality.constitution
    if personality.persona:
        rendered += "\n\nPersona:\n" + personality.persona
    for key in ("goals", "memories"):
        if key not in used:
            rendered += "\n\n" + values[key]
    return rendered


def _read(connection, library, personality_id):
    schema = schema_name(connection)
    row = connection.execute(text(f'SELECT id, name, revision, template, goals, constitution, persona, '
                                  f'continuity_enabled AS "continuityEnabled" FROM {schema}.personalities '
                                  "WHERE id = :id AND library = :library"),
                             {"id": personality_id, "library": library}).mappings().first()
    if row is None:
        raise HTTPException(404, "Personality not found.")
    memories = connection.execute(text(f'SELECT id, content, source_memory_id AS "sourceMemoryId" '
        f'FROM {schema}.personality_memories WHERE personality_id = :id ORDER BY position, id'),
        {"id": personality_id}).mappings().all()
    return SavedPersonality(**dict(row), memories=[dict(m) for m in memories])


def load_personality(library, personality_id):
    with library.storage.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        return _read(connection, library.name, personality_id)


async def save_personality(library, body, personality_id=None):
    previous = load_personality(library, personality_id) if personality_id else None
    if previous and previous.revision != body.revision:
        raise HTTPException(409, "This personality changed. Reload before saving.")
    old = {m.id: m for m in previous.memories} if previous else {}
    for memory in body.memories:
        if memory.id and memory.id not in old:
            raise HTTPException(422, "Assigned memory does not belong to this personality.")
        if memory.id and memory.sourceMemoryId != old[memory.id].sourceMemoryId:
            raise HTTPException(422, "An existing memory's source cannot be changed.")
    changed = [m for m in body.memories if m.id not in old or old[m.id].content != m.content]
    vectors = await embed([m.content for m in changed])
    personality_id = personality_id or uuid4()
    with library.storage.engine.begin() as connection:
        schema = schema_name(connection)
        if previous:
            updated = connection.execute(text(f"UPDATE {schema}.personalities SET name=:name, template=:template, "
                "goals=CAST(:goals AS jsonb), revision=revision+1, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=:id AND library=:library AND revision=:revision"),
                {"id": personality_id, "library": library.name, "revision": body.revision,
                 "name": body.name, "template": body.template, "goals": json_text(body.goals)})
            if not updated.rowcount:
                raise HTTPException(409, "This personality changed. Reload before saving.")
        else:
            connection.execute(text(f"INSERT INTO {schema}.personalities (id,library,name,revision,template,goals) "
                "VALUES (:id,:library,:name,1,:template,CAST(:goals AS jsonb))"),
                {"id": personality_id, "library": library.name, "name": body.name,
                 "template": body.template, "goals": json_text(body.goals)})
        retained = {m.id for m in body.memories if m.id}
        for removed in old.keys() - retained:
            connection.execute(text(f"DELETE FROM {schema}.personality_memories WHERE id=:id"), {"id": removed})
        embeddings = iter(vectors)
        for position, memory in enumerate(body.memories):
            if memory.sourceMemoryId:
                source = connection.execute(text(f"SELECT id FROM {schema}.memories WHERE id=:id AND library=:library"),
                    {"id": memory.sourceMemoryId, "library": library.name}).first()
                if not source:
                    raise HTTPException(422, "Source memory is unavailable in this library.")
            memory_id = memory.id or uuid4()
            connection.execute(text(f"INSERT INTO {schema}.personality_memories "
                "(id,personality_id,content,source_memory_id,position) VALUES (:id,:personality,:content,:source,:position) "
                "ON CONFLICT (id) DO UPDATE SET content=EXCLUDED.content, position=EXCLUDED.position"),
                {"id": memory_id, "personality": personality_id, "content": memory.content,
                 "source": memory.sourceMemoryId, "position": position})
            if memory in changed:
                connection.execute(text(f"INSERT INTO {schema}.personality_memory_embeddings "
                    "(memory_id,model,dimensions,embedding) VALUES (:id,:model,:dimensions,CAST(:embedding AS public.vector)) "
                    "ON CONFLICT (memory_id,model,dimensions) DO UPDATE SET embedding=EXCLUDED.embedding"),
                    {"id": memory_id, "model": EMBEDDING_MODEL, "dimensions": EMBEDDING_DIMENSIONS,
                     "embedding": json_text(next(embeddings))})
        connection.execute(text(f"UPDATE {schema}.personalities SET constitution=:constitution, persona=:persona, "
            "continuity_enabled=:enabled WHERE id=:id"), {"id": personality_id, "constitution": body.constitution,
            "persona": body.persona, "enabled": body.continuityEnabled})
        saved = _read(connection, library.name, personality_id)
        connection.execute(text(f"INSERT INTO {schema}.personality_versions (personality_id,revision,document) "
            "VALUES (:id,:revision,CAST(:document AS jsonb))"), {"id": personality_id, "revision": saved.revision,
            "document": json_text(saved.model_dump(mode="json"))})
        return saved


router = APIRouter(prefix="/api/admin/personalities", dependencies=[Depends(require_admin_access), Depends(no_store)])


@router.get("")
def browse(request: Request):
    library = request.app.state.library
    with library.storage.engine.connect() as connection:
        schema = schema_name(connection)
        return [dict(row) for row in connection.execute(text(f"SELECT id,name,revision FROM {schema}.personalities "
            "WHERE library=:library ORDER BY lower(name),id"), {"library": library.name}).mappings()]


@router.post("/preview")
def preview(body: PersonalityWrite):
    return {"prompt": render_personality(body)}


@router.post("", status_code=201)
async def create(body: PersonalityWrite, request: Request):
    return await save_personality(request.app.state.library, body)


@router.get("/{personality_id}")
def read(personality_id: UUID, request: Request):
    return load_personality(request.app.state.library, personality_id)


@router.put("/{personality_id}")
async def update(personality_id: UUID, body: PersonalityUpdate, request: Request):
    return await save_personality(request.app.state.library, body, personality_id)


@router.get("/{personality_id}/runs")
def used_in(personality_id: UUID, request: Request):
    library = request.app.state.library
    load_personality(library, personality_id)
    with library.storage.engine.connect() as connection:
        schema = schema_name(connection)
        return [dict(row) for row in connection.execute(text(f"SELECT id,flow_id,started_at,status, "
            f"snapshot->'personality'->>'revision' AS personality_revision FROM {schema}.flow_runs r "
            "WHERE library=:library AND snapshot->'personality'->>'id'=:id "
            f"AND EXISTS (SELECT 1 FROM {schema}.flow_run_events e WHERE e.run_id=r.id "
            "AND e.kind='personality.rendered') ORDER BY started_at DESC LIMIT 50"),
            {"id": str(personality_id), "library": library.name}).mappings()]
