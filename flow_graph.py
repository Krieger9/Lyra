"""Saved flow model, validation and loading."""
from math import isfinite
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator
from sqlalchemy import Column, DateTime, Integer, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import JSONB

from agent_store import Name, definitions, metadata, serialize_agent
from json_mapping import ObjectDefinition, check_size

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
    name: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    readAs: Literal["value", "json"] = "value"


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
        names = set()
        response_inputs = 0
        for edge in self.edges:
            if edge.name:
                key = (edge.target, edge.name)
                if key in names:
                    raise ValueError("Incoming wire names must be distinct on the same node")
                names.add(key)
            if edge.source not in nodes or edge.target not in nodes or edge.source == edge.target:
                raise ValueError("Connections must join distinct existing nodes")
            source, target = nodes[edge.source], nodes[edge.target]
            if edge.readAs == "json" and target.type != "json":
                raise ValueError("Read as JSON applies to a JSON node's incoming wires")
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


def snapshot(library, flow_id, revision=None):
    row = load_flow(library, flow_id)
    if revision is not None and row["revision"] != revision:
        raise HTTPException(409, "This flow changed. Reopen the saved revision before running or activating it.")
    document = serialize_flow(row)
    ids = [node.data.definition.id for node in FlowGraph.model_validate(row["graph"]).nodes
           if node.type == "agent" and node.data.definition]
    with library.storage.engine.connect() as conn:
        rows = conn.execute(select(definitions).where(definitions.c.library == library.name,
                                                     definitions.c.id.in_(ids))).mappings()
        document["agents"] = {str(agent["id"]): serialize_agent(agent) for agent in rows}
    return document
