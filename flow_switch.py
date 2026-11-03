"""Switch conditions and the input fields available to the flow designer and runner."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from json_mapping import MappingError, lookup, pointer, validate_mappings


class Condition(BaseModel):
    field: str = Field(max_length=1024)  # JSON Pointer, including "" for the whole input
    operator: Literal["empty", "notEmpty", "equals", "notEquals"]
    value: str | None = Field(default=None, max_length=120)

    def matches(self, payload):
        try:
            actual = lookup(payload, pointer(self.field))
        except MappingError:
            actual = None
        if actual is not None and not isinstance(actual, str):
            raise MappingError(f"Switch field {self.field or '(input)'} expected text.")
        if self.operator == "empty":
            return actual is None or not actual.strip()
        if self.operator == "notEmpty":
            return actual is not None and bool(actual.strip())
        if actual is None:
            return False
        return actual == self.value if self.operator == "equals" else actual != self.value


class SwitchPath(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(max_length=120)
    match: Literal["all", "any"] = "all"
    checks: list[Condition] = Field(default_factory=list, max_length=32)


class SwitchDefinition(BaseModel):
    paths: list[SwitchPath] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def distinct_ports(self):
        ids = {"otherwise", *(path.id for path in self.paths)}
        if len(ids) != len(self.paths) + 1:
            raise ValueError("Switch paths need distinct IDs and otherwise is reserved.")
        return self

    def select(self, payload):
        for path in self.paths:
            matches = all if path.match == "all" else any
            if matches(check.matches(payload) for check in path.checks):
                return path.id, path.label
        return "otherwise", "Otherwise"

    def validate_fields(self, fields):
        for path in self.paths:
            if not path.label.strip() or not path.checks:
                raise MappingError("Each Switch path needs a name and at least one condition.")
            for check in path.checks:
                if check.field not in fields:
                    raise MappingError(f"{path.label}: select an available input field ({check.field!r}).")
                choices = fields[check.field]
                compares = check.operator in ("equals", "notEquals")
                if compares != bool(choices):
                    raise MappingError(f"{path.label}: choose a supported operator for {check.field!r}.")
                if compares and check.value not in choices:
                    raise MappingError(f"{path.label}: {check.value!r} is no longer an available choice.")


def text_fields(value, path=""):
    if isinstance(value, str):
        yield path, []
    elif isinstance(value, (dict, list)):
        for key, item in (value.items() if isinstance(value, dict) else enumerate(value)):
            token = str(key).replace("~", "~0").replace("/", "~1")
            yield from text_fields(item, f"{path}/{token}")


class FlowFields:
    """Resolve text paths to choices (or an empty list for free text) through wire mappings."""

    def __init__(self, nodes, incoming, agents):
        self.nodes = nodes
        self.incoming = incoming
        self.agents = agents
        self.cache = {}
        self.visiting = set()

    def output(self, node_id):
        if node_id in self.cache:
            return self.cache[node_id]
        if node_id in self.visiting:
            raise MappingError("The connected flow contains a cycle.")
        self.visiting.add(node_id)
        try:
            fields = self._resolve(self.nodes[node_id])
            self.cache[node_id] = fields
            return fields
        finally:
            self.visiting.remove(node_id)

    def _resolve(self, node):
        parents = self.incoming[node.id]
        if node.type == "switch" and len(parents) == 1:
            return self.output(parents[0].source)
        if node.type == "apiInput":
            return {"": []}
        if node.type == "agent" and node.data.definition:
            definition = self.agents.get(str(node.data.definition.id))
            if not definition:
                return {}
            schema = definition.get("outputSchema")
            return {"/" + f["name"]: f.get("choices", []) for f in schema["fields"]} if schema else {"": []}
        obj = node.data.objectDefinition
        if node.type != "json" or not obj:
            return {}
        wires = {e.id: e for e in parents}
        validate_mappings(obj, wires.keys())
        fields = dict(text_fields(obj.base))
        for rule in obj.mappings:
            target = rule.target
            # A whole-object binding replaces all literal descendants at the destination.
            fields = {k: v for k, v in fields.items() if k != target and not k.startswith(target + "/")}
            if rule.source == ".text":
                fields[target] = []
                continue
            prefix = rule.source[5:] if rule.source.startswith(".json") else rule.source[6:]
            source = self.output(wires[rule.edgeId].source)
            for key, choices in source.items():
                if key == prefix or key.startswith(prefix + "/"):
                    fields[target + key[len(prefix):]] = choices
                    if len(fields) > 8192:
                        raise MappingError("Switch input exceeds 8192 selectable fields.")
            # A field explicitly extracted from untyped JSON can be checked for text at runtime.
            if prefix and "" in source and (rule.source.startswith(".json") or wires[rule.edgeId].readAs == "json"
                                            or self.nodes[wires[rule.edgeId].source].type == "apiInput"):
                fields[target] = []
        return fields
