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
from flow_graph import FlowWrite, FlowUpdate, FlowRun, flows, load_flow, serialize_flow, snapshot
from flow_execution import perform
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
    library = library_for(request)
    with library.storage.engine.connect() as connection:
        rows = connection.execute(select(flows).where(
            flows.c.library == library.name).order_by(flows.c.name, flows.c.id)).mappings().all()
    return [serialize_flow(row, False) for row in rows]


@router.get("/{flow_id}")
def get_flow(flow_id: UUID, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return serialize_flow(load_flow(library_for(request), flow_id))


@router.post("", status_code=201)
def create_flow(body: FlowWrite, request: Request):
    library = library_for(request)
    with library.storage.engine.begin() as connection:
        row = connection.execute(flows.insert().values(
            id=uuid4(), name=body.name, library=library.name, revision=1,
            graph=body.graph.model_dump(mode="json"), updated_at=datetime.now(timezone.utc),
        ).returning(flows)).mappings().one()
    return serialize_flow(row)


@router.put("/{flow_id}")
def update_flow(flow_id: UUID, body: FlowUpdate, request: Request):
    library = library_for(request)
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
        output = await perform(document, library, request.app.state.studio, body.input, run)
    except HTTPException as exc:
        raise HTTPException(exc.status_code, exc.detail, headers=headers) from exc
    return JSONResponse(output, headers=headers) if isinstance(output, dict) else Response(
        content=output, media_type="text/plain", headers=headers)
