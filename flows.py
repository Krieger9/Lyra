"""Admin controller for flow design, validation and synchronous execution."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, JsonValue
from sqlalchemy import select

from app_context import library_for
from auth import require_admin_access
from flow_graph import FlowGraph, FlowWrite, FlowUpdate, FlowRun, flows, graph_agents, load_flow, serialize_flow, snapshot
from flow_switch import FlowFields
from flow_execution import respond
from json_mapping import MappingError, ObjectDefinition, assemble, check_size
from run_store import Run

router = APIRouter(prefix="/api/admin/flows", dependencies=[Depends(require_admin_access)])


class MappingPreview(BaseModel):
    definition: ObjectDefinition
    inputs: dict[str, JsonValue] = Field(max_length=400)
    readAs: dict[str, Literal["value", "json"]] = Field(default_factory=dict, max_length=400)


@router.post("/preview-json")
def preview_json(body: MappingPreview):
    try:
        check_size(body.inputs)
        return JSONResponse(assemble(body.definition, body.inputs, body.readAs), headers={"Cache-Control": "no-store"})
    except MappingError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("")
def list_flows(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return flow_summaries(library_for(request))


def flow_summaries(library):
    with library.storage.engine.connect() as connection:
        rows = connection.execute(select(flows).where(
            flows.c.library == library.name).order_by(flows.c.name, flows.c.id)).mappings().all()
    return [serialize_flow(row, False) for row in rows]


@router.post("/switch-fields/{node_id}")
def switch_fields(node_id: str, graph: FlowGraph, request: Request):
    nodes = {node.id: node for node in graph.nodes}
    if node_id not in nodes or nodes[node_id].type != "switch":
        raise HTTPException(422, "Select a Switch in this flow.")
    incoming = {node.id: [] for node in graph.nodes}
    for edge in graph.data_edges:
        incoming[edge.target].append(edge)
    try:
        return FlowFields(nodes, incoming, graph_agents(library_for(request), graph), graph.stateObject).output(node_id)
    except MappingError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{flow_id}")
def get_flow(flow_id: UUID, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return serialize_flow(load_flow(library_for(request), flow_id))


@router.post("", status_code=201)
def create_flow(body: FlowWrite, library=Depends(library_for)):
    """Create a saved draft. Each graph needs nodes with id/type api-input/apiInput and
    api-response/apiResponse. Each node needs a position with x and y, and a data object.
    To run a single agent, set its data.definition.id to a saved agent UUID and add two edges
    with id, source and target linking api-input to the agent and the agent to api-response."""
    with library.storage.engine.begin() as connection:
        row = connection.execute(flows.insert().values(
            id=uuid4(), name=body.name, library=library.name, revision=1,
            graph=body.graph.model_dump(mode="json"), updated_at=datetime.now(timezone.utc),
        ).returning(flows)).mappings().one()
    return serialize_flow(row)


@router.put("/{flow_id}")
def update_flow(flow_id: UUID, body: FlowUpdate, library=Depends(library_for)):
    with library.storage.engine.begin() as connection:
        row = connection.execute(flows.update().where(
            flows.c.id == flow_id, flows.c.library == library.name, flows.c.revision == body.revision,
        ).values(name=body.name, revision=body.revision + 1, graph=body.graph.model_dump(mode="json"),
                 updated_at=datetime.now(timezone.utc)).returning(flows)).mappings().first()
    if row is None:
        raise HTTPException(409, "This flow changed or is unavailable. Reopen it before saving.")
    return serialize_flow(row)


@router.post("/{flow_id}/run")
async def run_flow(flow_id: UUID, body: FlowRun, request: Request):
    library = library_for(request)
    document = snapshot(library, flow_id)
    run = Run(library, document, "api")
    headers = {"Cache-Control": "no-store", "X-Lyra-Run-Id": str(run.id)}
    try:
        def render(output):
            if isinstance(output, str):
                return Response(content=output, media_type="text/plain", headers=headers)
            return JSONResponse(output, headers=headers)

        return await respond(document, library, request.app.state.studio, body.input, run, body.chatContext,
                             design_request=request, render_response=render)
    except HTTPException as exc:
        raise HTTPException(exc.status_code, exc.detail, headers=headers) from exc
