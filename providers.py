"""Library-scoped model, group, protocol and provider configuration."""
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl, TypeAdapter, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agent_store import (ModelName, Name, builtin_protocol_id, definitions, lock_model_catalog, model_groups,
                         models as model_table, protocols, providers)
from model_adapter import ADAPTERS
from app_context import library_for
from auth import require_admin_access

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin_access)])


class GroupWrite(BaseModel):
    name: Name
    modelIds: list[UUID] | None = Field(default=None, min_length=1, max_length=128)
    models: list[ModelName] | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_models(self):
        if (self.modelIds is None) == (self.models is None):
            raise ValueError("Select models by ID or by name.")
        selected = self.modelIds or self.models
        if len(set(selected)) != len(selected):
            raise ValueError("Model names in a group must be unique.")
        return self


class GroupUpdate(GroupWrite):
    revision: int = Field(ge=1)


class ProviderWrite(BaseModel):
    name: Name
    uri: str = Field(min_length=1, max_length=2048)
    groupId: UUID
    protocolId: UUID | None = None

    @field_validator("uri")
    @classmethod
    def endpoint_uri(cls, value):
        uri = TypeAdapter(HttpUrl).validate_python(value)
        if uri.username or uri.password or uri.query is not None or uri.fragment is not None:
            raise ValueError("Use an HTTP(S) endpoint without credentials, query or fragment.")
        return str(uri).rstrip("/")


class ProviderUpdate(ProviderWrite):
    revision: int = Field(ge=1)


def entry(row):
    result = {"id": str(row["id"]), "name": row["name"], "revision": row["revision"]}
    if "models" in row:
        return result | {"models": row["models"]}
    if "adapter" in row:
        return result | {"adapter": row["adapter"]}
    if "uri" in row:
        return result | {"uri": row["uri"], "groupId": str(row["group_id"]), "protocolId": str(row["protocol_id"])}
    return result


def catalog_entries(conn, library):
    def entries(table):
        return [entry(row) for row in conn.execute(select(table).where(
            table.c.library == library.name).order_by(table.c.name)).mappings()]
    models = entries(model_table)
    identifiers = {model["name"]: model["id"] for model in models}
    groups = entries(model_groups)
    for group in groups:
        group["modelIds"] = [identifiers[name] for name in group["models"]]
    return {"providers": entries(providers), "modelGroups": groups, "models": models,
            "protocols": entries(protocols), "adapters": [{"id": k, "name": v[0]} for k, v in ADAPTERS.items()]}


class ModelWrite(BaseModel):
    name: ModelName
    # Used when creating a model from the model picker of an agent.
    addToGroup: UUID | None = None
    groupRevision: int | None = Field(default=None, ge=1)


class ModelUpdate(ModelWrite):
    revision: int = Field(ge=1)


class ProtocolWrite(BaseModel):
    name: Name
    adapter: str

    @field_validator("adapter")
    @classmethod
    def installed(cls, value):
        if value not in ADAPTERS:
            raise ValueError("Select an installed client adapter.")
        return value


class ProtocolUpdate(ProtocolWrite):
    revision: int = Field(ge=1)


def write_row(conn, table, library, key, body, values):
    values.update(name=body.name, revision=1 if key is None else body.revision + 1)
    statement = table.insert().values(id=uuid4(), library=library.name) if key is None else table.update().where(
        table.c.id == key, table.c.library == library.name, table.c.revision == body.revision)
    try:
        row = conn.execute(statement.values(**values).returning(table)).mappings().first()
    except IntegrityError:
        raise HTTPException(409, "That name is already used in this library.") from None
    if not row:
        raise HTTPException(409, "This configuration changed or is unavailable. Reload before saving.")
    return entry(row)


def save_model(library, body, key=None):
    with library.storage.engine.begin() as conn:
        lock_model_catalog(conn, library)
        old = conn.execute(select(model_table.c.name).where(model_table.c.id == key,
                           model_table.c.library == library.name)).scalar_one_or_none() if key else None
        saved = write_row(conn, model_table, library, key, body, {})
        if old and old != body.name:
            # Membership and assignments keep pointing at this model when its wire name changes.
            groups = conn.execute(select(model_groups).where(model_groups.c.library == library.name,
                                  model_groups.c.models.contains([old]))).mappings().all()
            for group in groups:
                conn.execute(model_groups.update().where(model_groups.c.id == group["id"]).values(
                    models=[body.name if n == old else n for n in group["models"]]))
            conn.execute(definitions.update().where(definitions.c.library == library.name,
                         definitions.c.model_name == old).values(model_name=body.name))
        if body.addToGroup:
            group = conn.execute(select(model_groups).where(model_groups.c.library == library.name,
                                 model_groups.c.id == body.addToGroup)).mappings().first()
            if not group or group["revision"] != body.groupRevision:
                raise HTTPException(409, "The selected group changed. Reopen it before adding the model.")
            if body.name not in group["models"]:
                if len(group["models"]) >= 128:
                    raise HTTPException(422, "The group already contains 128 models.")
                conn.execute(model_groups.update().where(model_groups.c.id == body.addToGroup).values(
                    models=[*group["models"], body.name], revision=group["revision"] + 1))
        return saved


def save_entry(library, body, key=None):
    is_group = isinstance(body, GroupWrite)
    table = model_groups if is_group else providers
    with library.storage.engine.begin() as conn:
        lock_model_catalog(conn, library)
        used = select(definitions.c.model_name).select_from(definitions.join(providers)).where(
            definitions.c.library == library.name)
        if is_group:
            selected = body.modelIds or body.models
            column = model_table.c.id if body.modelIds else model_table.c.name
            rows = conn.execute(select(model_table.c.id, model_table.c.name).where(
                model_table.c.library == library.name, column.in_(selected))).all()
            if len(rows) != len(selected):
                raise HTTPException(422, "Select existing models from this library.")
            names = dict(rows)
            by_name = {name: mid for mid, name in rows}
            model_ids = body.modelIds or [by_name[name] for name in body.models]
            models = [names[mid] for mid in model_ids]
            used = used.where(providers.c.group_id == key)
            values = {"models": models}
        else:
            models = conn.execute(select(model_groups.c.models).where(model_groups.c.id == body.groupId,
                                  model_groups.c.library == library.name)).scalar_one_or_none()
            if models is None:
                raise HTTPException(422, "Select an existing model group.")
            used = used.where(providers.c.id == key)
            protocol_id = body.protocolId
            if key and "protocolId" not in body.model_fields_set:
                protocol_id = conn.execute(select(providers.c.protocol_id).where(providers.c.id == key,
                                           providers.c.library == library.name)).scalar_one_or_none()
            protocol_id = protocol_id or builtin_protocol_id(library.name, "ollama")
            if not conn.execute(select(protocols.c.id).where(protocols.c.id == protocol_id,
                                protocols.c.library == library.name)).scalar_one_or_none():
                raise HTTPException(422, "Select an existing protocol.")
            values = {"uri": body.uri, "group_id": body.groupId, "protocol_id": protocol_id}
        if key is not None:
            missing = set(conn.execute(used).scalars()) - set(models)
            if missing:
                raise HTTPException(409, "Reassign agents using these models first: " + ", ".join(sorted(missing)))
        saved = write_row(conn, table, library, key, body, values)
        if is_group:
            saved["modelIds"] = [str(mid) for mid in model_ids]
        return saved


@router.post("/models", status_code=201)
def create_model(body: ModelWrite, request: Request):
    return save_model(library_for(request), body)


@router.put("/models/{key}")
def update_model(key: UUID, body: ModelUpdate, request: Request):
    return save_model(library_for(request), body, key)


def save_protocol(library, body, key=None):
    with library.storage.engine.begin() as conn:
        lock_model_catalog(conn, library)
        return write_row(conn, protocols, library, key, body, {"adapter": body.adapter})


@router.post("/protocols", status_code=201)
def create_protocol(body: ProtocolWrite, request: Request):
    return save_protocol(library_for(request), body)


@router.put("/protocols/{key}")
def update_protocol(key: UUID, body: ProtocolUpdate, request: Request):
    return save_protocol(library_for(request), body, key)


@router.post("/model-groups", status_code=201)
def create_group(body: GroupWrite, request: Request):
    return save_entry(library_for(request), body)


@router.put("/model-groups/{key}")
def update_group(key: UUID, body: GroupUpdate, request: Request):
    return save_entry(library_for(request), body, key)


@router.post("/providers", status_code=201)
def create_provider(body: ProviderWrite, request: Request):
    return save_entry(library_for(request), body)


@router.put("/providers/{key}")
def update_provider(key: UUID, body: ProviderUpdate, request: Request):
    return save_entry(library_for(request), body, key)
