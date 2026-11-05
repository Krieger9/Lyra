"""Saved flow model, validation and loading."""
from math import isfinite
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator
from sqlalchemy import Column, DateTime, Integer, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import JSONB

from agent_store import AgentDefinition, Name, PromptReference, execution_agents, metadata
from chat_input import ChatRequest, InputSelection
from flow_switch import SwitchDefinition
from flow_state import StateDefinition
from personalities import SavedPersonality, load_personality
from json_mapping import MappingError, ObjectDefinition, check_size

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
    chatInput: bool = False
    recallMemories: bool = True
    includeQueryState: bool = False
    rememberWhen: Literal["afterResponse", "onEvidence"] = "afterResponse"
    memoryEvidence: Literal["value", "exchange"] = "value"
    extractorAgentId: UUID | None = None
    personalityId: UUID | None = None
    usePersonality: bool = True
    inputSelection: InputSelection | None = None
    definition: AgentReference | None = None
    objectDefinition: ObjectDefinition | None = None
    switchDefinition: SwitchDefinition | None = None
    stateDefinition: StateDefinition | None = None


class FlowNode(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    type: Literal["apiInput", "apiResponse", "personality", "agent", "json", "item", "switch", "state", "recall", "remember"]
    position: Position
    data: NodeData

    @model_validator(mode="after")
    def validate_input_options(self):
        if (self.type == "state") != (self.data.stateDefinition is not None):
            raise ValueError("A State element requires its own State definition.")
        if self.data.chatInput and self.type != "apiInput":
            raise ValueError("Chat request format belongs to API Input.")
        if self.data.personalityId and self.type not in ("personality", "apiInput"):
            raise ValueError("Select a profile on the Personality element.")
        if self.data.inputSelection and self.type != "agent":
            raise ValueError("Input selection belongs to an Agent.")
        return self


class FlowEdge(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    source: str
    sourceHandle: str | None = Field(default=None, max_length=80)
    target: str
    targetHandle: Literal["personality", "memories"] | None = None
    name: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    readAs: Literal["value", "json"] = "value"


class Viewport(Position):
    zoom: float = Field(gt=0, le=10)


class PanelSize(BaseModel):
    width: float = Field(ge=0, le=10000)
    height: float = Field(ge=0, le=10000)


def path_requirements(node, parents, requirements):
    """A usable wire requires all upstream Switch decisions on its path."""
    guards = []
    combined = {}
    for edge in parents:
        guard = requirements[edge.source].copy()
        if edge.sourceHandle is not None:
            guard[edge.source] = edge.sourceHandle
        if node.type == "apiResponse":
            for previous in guards:
                if not any(k in previous and previous[k] != v for k, v in guard.items()):
                    raise MappingError("API Response inputs must come from mutually exclusive Switch paths.")
        elif any(k in combined and combined[k] != v for k, v in guard.items()):
            raise MappingError(f"{node.data.label} requires inputs from mutually exclusive Switch paths.")
        combined.update(guard)
        guards.append(guard)
    if node.type == "apiResponse" and guards:
        # Only decisions shared by every possible reply constrain a downstream State.
        return {key: value for key, value in guards[0].items()
                if all(guard.get(key) == value for guard in guards[1:])}
    return combined


class FlowGraph(BaseModel):
    memoryMode: Literal["wired", "legacy"] = "wired"
    personalityMode: Literal["wired", "legacy"] = "wired"
    stateObject: dict[str, JsonValue] = Field(default_factory=dict, max_length=64)

    @field_validator("stateObject")
    @classmethod
    def bounded_state(cls, value):
        check_size(value)
        return value

    nodes: list[FlowNode] = Field(min_length=2, max_length=402)
    edges: list[FlowEdge] = Field(default_factory=list, max_length=1000)
    viewport: Viewport = Field(default_factory=lambda: Viewport(x=0, y=0, zoom=1))
    panelSize: PanelSize = Field(default_factory=lambda: PanelSize(width=280, height=240))

    @property
    def chat_input(self):
        return next(node.data.chatInput for node in self.nodes if node.type == "apiInput")

    @classmethod
    def from_saved(cls, graph):
        if isinstance(graph, dict):
            modes = {"personalityMode": "legacy", "memoryMode": "legacy"}
            parsed = cls.model_validate({**modes, **graph})
            # A run made from an old activation must retain the original snapshot fields.
            parsed.model_fields_set.difference_update(modes.keys() - graph.keys())
            return parsed
        return cls.model_validate(graph)

    @property
    def data_edges(self):
        return [edge for edge in self.edges if edge.targetHandle is None]

    @property
    def uses_memory(self):
        operations = {n.id for n in self.nodes if n.type in ("recall", "remember")}
        return self.memoryMode == "legacy" or any(e.target in operations for e in self.edges)

    @property
    def memory_sources(self):
        return {edge.target: edge.source for edge in self.edges if edge.targetHandle == "memories"}

    @property
    def agent_profile_ids(self):
        cards = {n.id: n.data.personalityId for n in self.nodes if n.type == "personality"}
        if self.personalityMode == "legacy":
            profile = next(iter(cards.values()), None) or next(n.data.personalityId for n in self.nodes if n.type == "apiInput")
            return {n.id: profile for n in self.nodes if profile and n.type == "agent" and n.data.usePersonality}
        return {e.target: cards[e.source] for e in self.edges if e.targetHandle == "personality" and cards[e.source]}

    @model_validator(mode="after")
    def validate_graph(self):
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("Node IDs must be unique")
        personality_count = sum(node.type == "personality" for node in self.nodes)
        old_input = any(n.type == "apiInput" and n.data.personalityId for n in self.nodes)
        if self.personalityMode == "wired" and "personalityMode" not in self.model_fields_set:
            # Snapshots omit unset fields. Record the mode even when writing a disconnected card.
            self.personalityMode = "legacy" if old_input else "wired"
        if self.personalityMode == "legacy" and personality_count > 1:
            raise ValueError("Legacy flows support one flow-wide personality.")
        memory_count = sum(n.type in ("recall", "remember") for n in self.nodes)
        if self.memoryMode == "wired" and "memoryMode" not in self.model_fields_set:
            # Older editor clients posted the switches directly on API Input and State.
            legacy_controls = any((n.type == "apiInput" and "recallMemories" in n.data.model_fields_set) or
                                  (n.type == "state" and "captureMemories" in n.data.stateDefinition.model_fields_set)
                                  for n in self.nodes)
            self.memoryMode = "legacy" if legacy_controls and not memory_count else "wired"
        if self.memoryMode == "legacy" and memory_count:
            raise ValueError("Use wired memory for Recall and Remember elements.")
        if len(nodes) - personality_count - memory_count > 200:
            raise ValueError("A flow can have up to 200 nodes plus Personality.")
        if old_input and (personality_count or self.personalityMode == "wired"):
            raise ValueError("Select the profile on the Personality element.")
        for kind, node_id in (("apiInput", "api-input"), ("apiResponse", "api-response")):
            matches = [node for node in self.nodes if node.type == kind]
            if len(matches) != 1 or matches[0].id != node_id:
                raise ValueError("A flow requires one permanent API Input and API Response")
        seen = set()
        ids = set()
        names = set()
        single_inputs = set()
        personality_inputs = set()
        memory_inputs = set()
        for edge in self.edges:
            if edge.name and edge.targetHandle is None:
                key = (edge.target, edge.name)
                if key in names:
                    raise ValueError("Incoming wire names must be distinct on the same node")
                names.add(key)
            if edge.source not in nodes or edge.target not in nodes or edge.source == edge.target:
                raise ValueError("Connections must join distinct existing nodes")
            source, target = nodes[edge.source], nodes[edge.target]
            if edge.readAs == "json" and target.type != "json":
                raise ValueError("Read as JSON applies to a JSON node's incoming wires")
            allowed = source.type in ("apiInput", "agent", "json", "switch", "state") and target.type in ("agent", "json", "switch", "state")
            allowed = allowed or (source.type in ("agent", "json", "switch", "state") and target.type == "apiResponse")
            allowed = allowed or (source.type == "apiResponse" and target.type == "state")
            allowed = allowed or (source.type in ("apiInput", "apiResponse", "agent", "json", "switch", "state")
                                  and target.type in ("recall", "remember"))
            if edge.targetHandle == "memories":
                allowed = source.type == "recall" and (target.type == "agent" or
                    (target.type == "state" and target.data.stateDefinition.operation in ("generate", "generateObject")))
                if target.id in memory_inputs:
                    raise ValueError("Connect one Recall to each Memories port.")
                memory_inputs.add(target.id)
            if edge.targetHandle == "personality":
                allowed = self.personalityMode == "wired" and source.type == "personality" and target.type == "agent"
                if target.id in personality_inputs:
                    raise ValueError("An agent accepts one Personality connection.")
                personality_inputs.add(target.id)
            if not allowed:
                raise ValueError("This connection is not supported")
            if source.type == "switch":
                paths = source.data.switchDefinition.paths if source.data.switchDefinition else []
                if edge.sourceHandle not in {"otherwise", *(p.id for p in paths)}:
                    raise ValueError("Select an existing Switch path for this wire.")
            elif edge.sourceHandle is not None:
                raise ValueError("Only Switch outputs have named ports.")
            if target.type in ("switch", "recall", "remember"):
                if target.id in single_inputs:
                    raise ValueError(f"A {target.type.title()} accepts one input.")
                single_inputs.add(target.id)
            connection = (source.id, edge.sourceHandle, target.id)
            if edge.id in ids or connection in seen:
                raise ValueError("Duplicate connection")
            ids.add(edge.id)
            seen.add(connection)
        incoming = {key: [] for key in nodes}
        if sum(nodes[e.target].type not in ("recall", "remember") for e in self.data_edges) > 400:
            raise ValueError("A flow can have up to 400 data wires.")
        for edge in self.data_edges:
            incoming[edge.target].append(edge)
        if len(incoming["api-response"]) > 1 or any(e.source == "api-response" for e in self.edges):
            requirements = {}
            visiting = set()

            def visit(key):
                if key in visiting:
                    raise ValueError("Remove cycles from API Response connections.")
                if key not in requirements:
                    visiting.add(key)
                    for parent in incoming[key]:
                        visit(parent.source)
                    requirements[key] = path_requirements(nodes[key], incoming[key], requirements)
                    visiting.remove(key)

            visit("api-response")
        return self


class FlowWrite(BaseModel):
    name: Name
    graph: FlowGraph


class FlowUpdate(FlowWrite):
    revision: int = Field(ge=1)


class FlowRun(BaseModel):
    input: str | dict[str, JsonValue] | list[JsonValue] = ""
    chatContext: ChatRequest | None = None

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
        value["graph"] = {"personalityMode": "legacy", "memoryMode": "legacy", **row["graph"]}
    return value


def load_flow(library, flow_id):
    with library.storage.engine.connect() as connection:
        row = connection.execute(select(flows).where(
            flows.c.id == flow_id, flows.c.library == library.name,
        )).mappings().first()
    if row is None:
        raise HTTPException(404, "Flow not found.")
    return row


class SavedEndpoint(BaseModel):
    uri: str
    provider: str
    group: str
    models: list[str]
    adapter: str = "ollama"  # Snapshots from before the protocol catalog used native Ollama.


class SavedAgent(AgentDefinition):
    id: UUID
    revision: int
    instructions: PromptReference
    endpoint: SavedEndpoint | None = None


class FlowSnapshot(BaseModel):
    id: UUID
    name: str
    revision: int
    graph: FlowGraph
    agents: dict[str, SavedAgent]
    personality: SavedPersonality | None = None
    personalities: dict[UUID, SavedPersonality] = Field(default_factory=dict)

    @field_validator("graph", mode="before")
    @classmethod
    def read_saved_graph(cls, value):
        return FlowGraph.from_saved(value)

    def profiles(self):
        if self.personality:
            return {self.personality.id: self.personality}
        return self.personalities

    def agent_definitions(self):
        return {key: agent.model_dump(mode="json") for key, agent in self.agents.items()}


def snapshot(library, flow_id, revision=None) -> FlowSnapshot:
    row = load_flow(library, flow_id)
    if revision is not None and row["revision"] != revision:
        raise HTTPException(409, "This flow changed. Reopen the saved revision before running or activating it.")
    graph = FlowGraph.from_saved(row["graph"])
    profiles = {key: load_personality(library, key) for key in dict.fromkeys(graph.agent_profile_ids.values())}
    return FlowSnapshot(**serialize_flow(row, include_graph=False), graph=graph,
                        agents=graph_agents(library, graph),
                        personalities=profiles)


def graph_agents(library, graph):
    ids = [node.data.definition.id for node in graph.nodes
           if node.type == "agent" and node.data.definition]
    ids.extend(node.data.stateDefinition.agentId for node in graph.nodes if node.type == "state" and
               node.data.stateDefinition.operation in ("generate", "generateObject") and node.data.stateDefinition.agentId)
    ids.extend(node.data.extractorAgentId for node in graph.nodes if node.type == "remember" and node.data.extractorAgentId)
    with library.storage.engine.connect() as conn:
        return execution_agents(conn, library, ids)
