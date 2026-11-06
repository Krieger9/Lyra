"""Named, library-scoped collections of prompts, independent of version pins."""
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import Column, ForeignKey, MetaData, String, Table, UniqueConstraint, Uuid, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from agent_store import Name, PromptName
from app_context import library_for
from auth import require_admin_access

metadata = MetaData(schema="agent_studio")
sets = Table("prompt_sets", metadata,
    Column("id", Uuid, primary_key=True), Column("library", String(255), nullable=False),
    Column("name", String(120), nullable=False), UniqueConstraint("library", "name"))
members = Table("prompt_set_members", metadata,
    Column("set_id", Uuid, ForeignKey(sets.c.id, ondelete="CASCADE"), primary_key=True),
    Column("prompt_name", String(255), primary_key=True))


class SetWrite(BaseModel, extra="forbid"):
    name: Name


class MemberWrite(BaseModel, extra="forbid"):
    names: list[PromptName] = Field(min_length=1, max_length=1000)
    remove: bool = False


def read_sets(connection, library):
    rows = connection.execute(select(sets.c.id, sets.c.name, members.c.prompt_name)
        .select_from(sets.outerjoin(members)).where(sets.c.library == library.name)
        .order_by(sets.c.name, sets.c.id, members.c.prompt_name))
    result = {}
    for set_id, name, prompt_name in rows:
        entry = result.setdefault(set_id, {"id": str(set_id), "name": name, "prompts": []})
        if prompt_name is not None:
            entry["prompts"].append(prompt_name)
    return list(result.values())


def lock_set(connection, library, set_id):
    row = connection.execute(select(sets).where(sets.c.id == set_id, sets.c.library == library.name)
                             .with_for_update()).mappings().first()
    if row is None:
        raise HTTPException(404, "Prompt set not found.")
    return row


def validate_prompt_set(connection, library, set_id):
    if set_id is not None and connection.execute(select(sets.c.id).where(
            sets.c.id == set_id, sets.c.library == library.name)).scalar_one_or_none() is None:
        raise HTTPException(422, "Choose a prompt set from this library.")


def add_members(connection, set_id, names):
    connection.execute(insert(members).values([
        {"set_id": set_id, "prompt_name": name} for name in sorted(set(names))
    ]).on_conflict_do_nothing())


router = APIRouter(prefix="/prompt-sets", dependencies=[Depends(require_admin_access)])


@router.get("")
def browse(response: Response, library=Depends(library_for)):
    response.headers["Cache-Control"] = "no-store"
    with library.storage.engine.connect() as connection:
        return read_sets(connection, library)


@router.post("", status_code=201)
def create(body: SetWrite, library=Depends(library_for)):
    set_id = uuid4()
    try:
        with library.storage.engine.begin() as connection:
            connection.execute(sets.insert().values(id=set_id, library=library.name, name=body.name))
    except IntegrityError as exc:
        raise HTTPException(409, "A prompt set with this name already exists.") from exc
    return {"id": str(set_id), "name": body.name, "prompts": []}


@router.put("/{set_id}/members")
def update_members(set_id: UUID, body: MemberWrite, library=Depends(library_for)):
    with library.storage.engine.begin() as connection:
        lock_set(connection, library, set_id)
        if body.remove:
            connection.execute(members.delete().where(members.c.set_id == set_id,
                                                       members.c.prompt_name.in_(body.names)))
        else:
            if set(body.names) - set(library.list_prompts()):
                raise HTTPException(422, "Choose existing prompts from this library.")
            add_members(connection, set_id, body.names)
    return {"id": str(set_id)}
