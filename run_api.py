"""Admin controls for observed runs and the chat flow binding."""
import os
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app_context import library_for
from auth import require_admin_access
from flow_execution import plan_flow, run_in_background
from flow_graph import FlowRun, snapshot
from json_mapping import MappingError
from run_store import Run, activate, activation, deactivate, inspect_run, recent
from memory import memory_enabled
from memory_store import list_memories, memory_detail


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin_access), Depends(no_store)])


@router.get("/memory-status")
def memory_status():
    return {"enabled": memory_enabled(), "embeddingConfigured": bool(os.getenv("OPENAI_API_KEY"))}


@router.get("/memories")
def browse_memories(request: Request, query: str = Query("", max_length=2000), offset: int = Query(0, ge=0),
                    limit: int = Query(50, ge=1, le=100)):
    return list_memories(library_for(request), query=query, offset=offset, limit=limit)


@router.get("/memories/{memory_id}")
def inspect_memory(memory_id: UUID, request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return memory_detail(library_for(request), memory_id, offset=offset, limit=limit)


class Revision(BaseModel):
    revision: int = Field(ge=1)


class DebugRun(FlowRun, Revision):
    pass


@router.post("/flows/{flow_id}/runs", status_code=202)
async def start(flow_id: UUID, body: DebugRun, request: Request, background: BackgroundTasks):
    library = library_for(request)
    document = snapshot(library, flow_id, body.revision)
    run = Run(library, document, "debug")
    background.add_task(run_in_background, document, library, request.app.state.studio, body.input, run, body.chatContext)
    return {"id": str(run.id)}


@router.get("/runs")
def list_runs(request: Request, flow_id: UUID | None = None, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)):
    return recent(library_for(request), flow_id, offset, limit)


@router.get("/runs/{run_id}")
def read_run(run_id: UUID, request: Request, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
             include_snapshot: bool = True):
    return inspect_run(library_for(request), run_id, after, limit, include_snapshot)


@router.get("/chat-flow")
def chat_binding(request: Request):
    doc = activation(library_for(request))
    return doc.model_dump(include={"id", "revision", "name"}, mode="json") if doc else None


@router.put("/chat-flow/{flow_id}")
def bind(flow_id: UUID, body: Revision, request: Request):
    library = library_for(request)
    document = snapshot(library, flow_id, body.revision)
    try:
        _, incoming = plan_flow(document.graph, document.agent_definitions())
    except MappingError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not incoming["api-response"]:
        raise HTTPException(422, "Connect API Response before activating the flow for chat.")
    activate(library, document)
    return {"id": str(flow_id), "revision": body.revision, "name": document.name}


@router.delete("/chat-flow/{flow_id}")
def unbind(flow_id: UUID, request: Request, revision: int = Query(ge=1)):
    deactivate(library_for(request), flow_id, revision)
    return {"active": False}
