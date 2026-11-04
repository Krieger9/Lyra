"""Named JSON objects shared by State elements during one Flow execution."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, field_validator

from json_mapping import FieldMapping, MappingError, ObjectDefinition, assemble, check_size, lookup, validate_mappings


class StateDefinition(BaseModel, extra="forbid"):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z_][a-zA-Z0-9_.-]*$")
    scope: Literal["run", "conversation"]
    operation: Literal["read", "update", "replace", "map", "generate", "generateObject"]
    initial: dict[str, JsonValue] = Field(default_factory=dict)
    mappings: list[FieldMapping] = Field(default_factory=list, max_length=64)
    agentId: UUID | None = None
    includeChatTurn: bool = False
    property: str = Field(default="", max_length=80)
    output: Literal["state", "property", "input"] = "state"
    outputProperty: str = Field(default="", max_length=80)
    outputWire: str = Field(default="", max_length=80)

    @field_validator("initial")
    @classmethod
    def bounded_initial(cls, value):
        check_size(value)
        return value

    def validate_setup(self, properties, parents, agents):
        ids = {edge.id for edge in parents}
        if not ids:
            raise MappingError("Each connected State requires at least one input.")
        if self.includeChatTurn and (self.operation not in ("generate", "generateObject")
                                     or not any(edge.source == "api-response" for edge in parents)):
            raise MappingError("Including the latest chat turn requires a generating State connected to API Response.")
        if self.output == "input" and self.outputWire and self.outputWire not in ids:
            raise MappingError("Select a connected passthrough wire.")
        if self.output == "property" and self.outputProperty not in properties:
            raise MappingError("Select a declared State output property.")
        if self.operation in ("generate", "generateObject"):
            agent = agents.get(str(self.agentId))
            if not agent:
                raise MappingError("Select an available agent for this State.")
            if self.operation == "generateObject" and not agent.get("outputSchema"):
                raise MappingError("Choose an agent with a structured object output.")
            if self.operation == "generate" and self.property not in properties:
                raise MappingError("Select the State property to generate.")
        if self.operation in ("map", "generateObject"):
            if not self.mappings:
                raise MappingError("Add a State property mapping.")
            validate_mappings(ObjectDefinition(base=properties, mappings=self.mappings),
                              {"result"} if self.operation == "generateObject" else ids)

    def apply(self, objects, value, properties=None, observe=None):
        current = (properties or {}) | objects.get(self.key, self.initial)
        if self.operation in ("update", "replace"):
            if not isinstance(value, dict):
                raise MappingError("State writes require an object. Use a JSON element to map the incoming value.")
            current = current | value if self.operation == "update" else value
        elif self.operation == "generate":
            current = current | {self.property: value}
        elif self.operation in ("map", "generateObject"):
            current = assemble(ObjectDefinition(base=current, mappings=self.mappings), value, observe=observe)
        check_size(current)
        objects[self.key] = current
        return current

    def project(self, current, inputs, incoming):
        if self.output == "input":
            return inputs[self.outputWire] if self.outputWire else incoming
        if self.output == "property":
            return lookup(current, [self.outputProperty])
        return current
