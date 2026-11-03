"""Saved flow designs and their API response contract."""

import asyncio
import logging
from datetime import datetime, timezone
from math import isfinite
from typing import Literal
from uuid import UUID, uuid4

from agent_studio import AgentStudio
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prompt_studio import NotFoundError
from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator
from sqlalchemy import Column, DateTime, Integer, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import JSONB

from agent_definitions import Name, definitions, library_for, metadata, serialize_agent
from auth import require_admin_access
from json_mapping import MappingError, ObjectDefinition, assemble, check_size, json_text, validate_mappings

flows = Table(
    "flows", metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", String(120), nullable=False),
    Column("library", String(255), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("graph", JSONB, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    schema="agent_studio",
)


class Position(BaseModel):
    x: float
    y: float

    @field_validator("x", "y")
    @classmethod
    def finite(cls, value):
        if not isfinite(value):
            raise ValueError("Coordinates must be finite")
        return value


class AgentReference(BaseModel):
    id: UUID


class NodeData(BaseModel):
    label: str = Field(default="", max_length=120)
    definition: AgentReference | None = None
    objectDefinition: ObjectDefinition | None = None


class FlowNode(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    type: Literal["apiInput", "apiResponse", "agent", "json", "item"]
    position: Position
    data: NodeData


class FlowEdge(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    source: str
    target: str


class Viewport(Position):
    zoom: float = Field(gt=0, le=10)


class PanelSize(BaseModel):
    width: float = Field(ge=0, le=10000)
    height: float = Field(ge=0, le=10000)


class FlowGraph(BaseModel):
    nodes: list[FlowNode] = Field(min_length=2, max_length=200)
    edges: list[FlowEdge] = Field(default_factory=list, max_length=400)
    viewport: Viewport = Field(default_factory=lambda: Viewport(x=0, y=0, zoom=1))
    panelSize: PanelSize = Field(default_factory=lambda: PanelSize(width=280, height=240))

    @model_validator(mode="after")
    def validate_graph(self):
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("Node IDs must be unique")
        for kind, node_id in (("apiInput", "api-input"), ("apiResponse", "api-response")):
            matches = [node for node in self.nodes if node.type == kind]
            if len(matches) != 1 or matches[0].id != node_id:
                raise ValueError("A flow requires one permanent API Input and API Response")
        seen = set()
        ids = set()
        response_inputs = 0
        for edge in self.edges:
            if edge.source not in nodes or edge.target not in nodes or edge.source == edge.target:
                raise ValueError("Connections must join distinct existing nodes")
            source, target = nodes[edge.source], nodes[edge.target]
            allowed = source.type in ("apiInput", "agent", "json") and target.type in ("agent", "json")
            allowed = allowed or (source.type in ("agent", "json") and target.type == "apiResponse")
            if not allowed:
                raise ValueError("This connection is not supported")
            if edge.id in ids or (edge.source, edge.target) in seen:
                raise ValueError("Duplicate connection")
            ids.add(edge.id)
            seen.add((edge.source, edge.target))
            if target.type == "apiResponse":
                response_inputs += 1
        if response_inputs > 1:
            raise ValueError("API Response accepts one output")
        return self


class FlowWrite(BaseModel):
    name: Name
    graph: FlowGraph


class FlowUpdate(FlowWrite):
    revision: int = Field(ge=1)


class FlowRun(BaseModel):
    input: str | dict[str, JsonValue] | list[JsonValue] = ""

    @field_validator("input")
    @classmethod
    def bounded_input(cls, value):
        if isinstance(value, str) and len(value) > 8000:
            raise ValueError("Text input exceeds 8000 characters.")
        check_size(value)
        return value


def create_schema(engine):
    with engine.begin() as connection:
        flows.create(connection, checkfirst=True)


def serialize_flow(row, include_graph=True):
    value = {"id": str(row["id"]), "name": row["name"], "revision": row["revision"]}
    if include_graph:
        value["graph"] = row["graph"]
    return value


def load_flow(library, flow_id):
    with library.storage.engine.connect() as connection:
        row = connection.execute(select(flows).where(
            flows.c.id == flow_id, flows.c.library == library.name,
        )).mappings().first()
    if row is None:
        raise HTTPException(404, "Flow not found.")
    return row


router = APIRouter(prefix="/api/admin/flows", dependencies=[Depends(require_admin_access)])


class MappingPreview(BaseModel):
    definition: ObjectDefinition
    inputs: dict[str, JsonValue] = Field(max_length=400)


@router.post("/preview-json")
def preview_json(body: MappingPreview):
    try:
        check_size(body.inputs)
        return JSONResponse(assemble(body.definition, body.inputs), headers={"Cache-Control": "no-store"})
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


class FlowPrompts:
    def __init__(self, library, definition):
        self.library, self.definition = library, definition

    def render(self, name, parameters, **options):
        instruction = self.definition["instructions"]
        system = self.library.render(instruction["name"], parameters, version=instruction["version"])
        prompt = self.definition["prompt"]
        content = parameters["request"] if prompt is None else self.library.render(
            prompt["name"], parameters, version=prompt["version"])
        return system + "\n\n" + content


async def execute_flow(graph, library, studio, input_text):
    nodes = {node.id: node for node in graph.nodes}
    incoming = {node.id: [] for node in graph.nodes}
    outgoing = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        incoming[edge.target].append(edge)
        outgoing[edge.source].append(edge.target)
    reached = {"api-input"}
    pending = ["api-input"]
    while pending:
        source = pending.pop()
        for target in outgoing[source]:
            if target not in reached:
                reached.add(target)
                pending.append(target)
    # Every connected branch must be valid before any model is called.
    plan, visiting, completed = [], set(), {"api-input"}

    def visit(node_id):
        if node_id in visiting:
            raise HTTPException(422, "The connected flow contains a cycle.")
        if node_id in completed:
            return
        visiting.add(node_id)
        node = nodes[node_id]
        parents = incoming[node_id]
        if node.type == "json":
            if node.data.objectDefinition is None:
                raise HTTPException(422, "JSON node needs an object definition.")
            validate_mappings(node.data.objectDefinition, {edge.id for edge in parents})
        if node.type == "agent" and (len(parents) != 1 or not node.data.definition):
            raise HTTPException(422, "Each connected Agent needs a definition and exactly one input to run.")
        for edge in parents:
            visit(edge.source)
        if node.type in ("agent", "json"):
            plan.append(node)
        visiting.remove(node_id)
        completed.add(node_id)

    for node in graph.nodes:
        if node.id in reached or node.id == "api-response":
            visit(node.id)
    with library.storage.engine.connect() as connection:
        rows = connection.execute(select(definitions).where(
            definitions.c.library == library.name,
            definitions.c.id.in_([node.data.definition.id for node in plan if node.type == "agent"]),
        )).mappings().all()
    agents = {row["id"]: serialize_agent(row) for row in rows}
    for node in plan:
        if node.type != "agent":
            continue
        definition = agents.get(node.data.definition.id)
        if definition is None:
            raise HTTPException(422, "A connected Agent definition is unavailable.")
        for reference in (definition["instructions"], definition["prompt"]):
            if reference:
                try:
                    library.get(reference["name"], reference["version"])
                except NotFoundError:
                    raise HTTPException(422, "A connected Agent prompt version is unavailable.")
    outputs = {"api-input": input_text}
    for node in plan:
        if node.type == "json":
            inputs = {edge.id: outputs[edge.source] for edge in incoming[node.id]}
            outputs[node.id] = assemble(node.data.objectDefinition, inputs)
            continue
        definition = agents[node.data.definition.id]
        runner = AgentStudio(FlowPrompts(library, definition), studio.model, studio.traces)
        value = outputs[incoming[node.id][0].source]
        result = await runner.run(value if isinstance(value, str) else json_text(value), prompt=definition["name"])
        if not isinstance(result, str):
            raise HTTPException(502, "An Agent returned invalid output.")
        outputs[node.id] = result
    parents = incoming["api-response"]
    return outputs[parents[0].source] if parents else ""


@router.post("/{flow_id}/run")
async def run_flow(flow_id: UUID, body: FlowRun, request: Request):
    library = library_for(request)
    row = load_flow(library, flow_id)
    graph = FlowGraph.model_validate(row["graph"])
    try:
        output = await asyncio.wait_for(execute_flow(graph, library, request.app.state.studio, body.input), timeout=120)
    except HTTPException:
        raise
    except MappingError as error:
        raise HTTPException(422, str(error)) from error
    except asyncio.TimeoutError:
        raise HTTPException(504, "Flow execution timed out.")
    except Exception as error:
        logging.getLogger(__name__).error("Flow execution failed (%s)", type(error).__name__)
        raise HTTPException(503, "Flow execution failed. Check the model and database.")
    if isinstance(output, dict):
        return JSONResponse(output, headers={"Cache-Control": "no-store"})
    return Response(content=output, status_code=200, media_type="text/plain", headers={"Cache-Control": "no-store"})
