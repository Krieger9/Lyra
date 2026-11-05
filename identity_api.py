"""Owner-scoped continuity inspection and controls in the admin workspace."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import text

from auth import admin_claims, bearer
from identity import IdentityDocument, IdentitySession, Browse, Search
from memory import no_store
from memory_store import schema_name
from personalities import load_personality


def require_identity_owner(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None:
        raise HTTPException(401, "Sign in to inspect your continuity.")
    claims = admin_claims(request, credentials)
    request.state.identity_owner = claims["tid"] + "/" + claims["sub"]


router = APIRouter(prefix="/api/admin/continuity", dependencies=[Depends(require_identity_owner), Depends(no_store)])


def session(request, personality_id):
    library = request.app.state.library
    personality = load_personality(library, personality_id)
    return IdentitySession(library, request.state.identity_owner, personality, {"kind": "owner_edit"})


def history_for(current, limit=50):
    with current.library.storage.engine.connect() as conn:
        schema = schema_name(conn)
        versions = conn.execute(text(f"SELECT revision,reason,source,created_at FROM {schema}.identity_versions "
            "WHERE branch_id=:id ORDER BY revision DESC LIMIT :limit"), {"id": current.id, "limit": limit}).mappings().all()
        events = conn.execute(text(f"SELECT id,revision,payload,created_at FROM {schema}.identity_events "
            "WHERE branch_id=:id ORDER BY revision DESC LIMIT :limit"), {"id": current.id, "limit": limit}).mappings().all()
    return {"versions": [dict(v) for v in versions], "events": [dict(e) for e in events]}


@router.get("/{personality_id}")
def read(personality_id: UUID, request: Request):
    current = session(request, personality_id)
    return {"id": current.id, "identity_id": personality_id, "revision": current.revision,
            "document": current.document, "context": current.context(), **history_for(current)}


@router.get("/{personality_id}/browse")
def browse(personality_id: UUID, request: Request, node_id: str = "library", offset: int = 0):
    current = session(request, personality_id)
    try:
        return current.browse(Browse(node_id=node_id, offset=offset))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{personality_id}/search")
def search(personality_id: UUID, request: Request, query: str, within: str = "library"):
    try:
        return session(request, personality_id).search(Search(query=query, within=within))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class Change(BaseModel, extra="forbid"):
    revision: int = Field(ge=1)
    tool: str
    arguments: dict


def check_revision(current, revision):
    if current.revision != revision:
        raise HTTPException(409, "Continuity changed. Reload before editing.")


@router.post("/{personality_id}/change")
async def change(personality_id: UUID, body: Change, request: Request):
    current = session(request, personality_id)
    check_revision(current, body.revision)
    if body.tool == "identity_revise":
        body.arguments["evidence_ids"] = [current.event_id]
    result = await current.call(body.tool, body.arguments)
    if "error" in result:
        raise HTTPException(422, result["error"])
    with current.library.storage.engine.begin() as conn:
        return current.commit(conn, None, "Explicit owner edit")


class Restore(BaseModel, extra="forbid"):
    revision: int = Field(ge=1)
    target_revision: int = Field(ge=1)


@router.post("/{personality_id}/restore")
def restore(personality_id: UUID, body: Restore, request: Request):
    current = session(request, personality_id)
    check_revision(current, body.revision)
    with current.library.storage.engine.begin() as conn:
        schema = schema_name(conn)
        document = conn.execute(text(f"SELECT document FROM {schema}.identity_versions "
            "WHERE branch_id=:id AND revision=:revision"), {"id": current.id, "revision": body.target_revision}).scalar_one_or_none()
        if document is None:
            raise HTTPException(404, "Version not found in your continuity.")
        current.document = IdentityDocument.model_validate(document)
        current.actions = [{"tool": "owner_restore", "arguments": {"reason": f"Restore version {body.target_revision}"}}]
        return current.commit(conn, None, "Owner restored an earlier version; later history retained")


@router.get("/{personality_id}/export")
def export(personality_id: UUID, request: Request):
    current = session(request, personality_id)
    return {"format": "lyra-continuity-v1", "identity_id": str(personality_id),
            "revision": current.revision, "document": current.document,
            "provenance": {"foundation_revision": current.document.foundation_revision,
                           "note": "Portable current state. Full source evidence remains in the originating installation."}}


class Import(BaseModel, extra="forbid"):
    revision: int = Field(ge=1)
    document: IdentityDocument


@router.post("/{personality_id}/import")
def import_document(personality_id: UUID, body: Import, request: Request):
    current = session(request, personality_id)
    check_revision(current, body.revision)
    current.document = body.document
    for node in current.document.nodes:
        node.attribution = "owner-imported record; source evidence may belong to another installation"
    current.actions = [{"tool": "owner_import", "arguments": {"reason": "Imported portable continuity document"}}]
    with current.library.storage.engine.begin() as conn:
        return current.commit(conn, None, "Owner imported a portable continuity document")


@router.post("/{personality_id}/reflect")
async def reflect(personality_id: UUID, request: Request):
    from chat_input import ChatRequest
    from model_adapter import provider_model
    from run_store import activation, Run
    current = session(request, personality_id)
    document = activation(current.library)
    if not document or personality_id not in document.profiles():
        raise HTTPException(422, "Activate a flow with this personality before starting a reflection cycle.")
    assignments = document.graph.agent_profile_ids
    nodes = [n for n in document.graph.nodes if assignments.get(n.id) == personality_id and n.data.definition]
    if not nodes:
        raise HTTPException(422, "The active flow has no participating conversation agent.")
    definition = document.agent_definitions()[str(nodes[0].data.definition.id)]
    if not definition.get("modelSelection") or definition["endpoint"]["adapter"] not in ("openai", "anthropic"):
        raise HTTPException(422, "Choose an OpenAI or Claude agent for reflection.")
    current.reflection_model = provider_model(definition["endpoint"], definition["modelSelection"]["model"])
    run = Run(current.library, document, "reflection")
    current.observe = run.emit
    current.source = {"kind": "owner_requested_reflection"}
    try:
        run.emit("run.started", input="Owner requested a bounded reflection cycle")
        completed = await current.reflect(ChatRequest(message="Review your existing interests, open questions and goals. "
            "You may organize your library or reconsider priorities. There is no new encounter or external evidence."), "")
        if not completed:
            raise HTTPException(503, "Reflection could not finish within its budget. No changes were saved.")
        run.finish("completed", result="Reflection cycle completed", identities=[current])
    except Exception:
        run.finish("failed", error="Reflection cycle failed.")
        raise
    return {"run_id": run.id, "changes": len(current.actions), "revision": current.revision+1}
