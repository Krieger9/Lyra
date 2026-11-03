"""Build a fresh JSON object from literals and incoming wire values."""

import json
import re
from copy import deepcopy

from pydantic import BaseModel, Field, JsonValue, field_validator


class MappingError(ValueError):
    pass


class FieldMapping(BaseModel):
    edgeId: str = Field(max_length=80)
    source: str = Field(max_length=1024)
    target: str = Field(max_length=1024)


def json_text(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    except (ValueError, RecursionError) as exc:
        raise MappingError("Value exceeds JSON numeric or nesting limits.") from exc


def check_size(value):
    try:
        size = len(json_text(value).encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise MappingError("JSON contains an invalid Unicode character.") from exc
    if size > 262144:
        raise MappingError("JSON value exceeds 256 KiB.")


class ObjectDefinition(BaseModel):
    base: dict[str, JsonValue]
    mappings: list[FieldMapping] = Field(default_factory=list, max_length=64)

    @field_validator("base")
    @classmethod
    def bounded_base(cls, value):
        check_size(value)
        return value


def pointer(path):
    if path == "":
        return ()
    if not path.startswith("/") or re.search(r"~(?![01])", path):
        raise MappingError(f"Invalid JSON path {path!r}. Use /field or /items/0.")
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/"))


def slot(container, key):
    if isinstance(container, dict) and key in container:
        return key
    if isinstance(container, list) and re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) < len(container):
        return int(key)
    raise MappingError(f"Field or array index {key!r} does not exist.")


def lookup(value, path):
    for key in path:
        value = value[slot(value, key)]
    return value


def source_path(expression):
    if expression == ".text":
        return None
    if expression == ".value" or expression.startswith(".value/"):
        return pointer(expression[6:])
    if expression == ".json" or expression.startswith(".json/"):
        return pointer(expression[5:])
    raise MappingError("Choose a wire value, a field path, text or parsed JSON.")


def validate_mappings(definition, edge_ids):
    targets = []
    for index, rule in enumerate(definition.mappings, 1):
        try:
            if rule.edgeId not in edge_ids:
                raise MappingError("Select an incoming connection.")
            source_path(rule.source)
            target = pointer(rule.target)
            if not target:
                raise MappingError("Choose a destination field in the base object.")
            lookup(definition.base, target)
            if any(previous[:len(target)] == target or target[:len(previous)] == previous for previous in targets):
                raise MappingError("Destination overlaps another mapping.")
            targets.append(target)
        except MappingError as exc:
            raise MappingError(f"Mapping {index}: {exc}") from exc


def parse_text(value):
    try:
        parsed = json.loads(value)
        check_size(parsed)
        return parsed
    except (ValueError, RecursionError) as exc:
        raise MappingError("Incoming text is not usable JSON.") from exc


def assemble(definition, inputs, read_as=None, observe=None):
    validate_mappings(definition, inputs.keys())
    read_as = read_as or {}
    result = deepcopy(definition.base)
    parsed = {}
    for index, rule in enumerate(definition.mappings, 1):
        value = inputs[rule.edgeId]
        path = source_path(rule.source)
        try:
            parse_json = read_as.get(rule.edgeId) == "json" or rule.source.startswith(".json")
            if isinstance(value, str) and parse_json:
                if rule.edgeId not in parsed:
                    parsed[rule.edgeId] = parse_text(value)
                value = parsed[rule.edgeId]
            if path is None:
                value = value if isinstance(value, str) else json_text(value)
            else:
                if path and isinstance(value, str) and not parse_json:
                    raise MappingError("This wire carries text. Choose Read as JSON to access fields.")
                value = lookup(value, path)
            target = pointer(rule.target)
            parent = lookup(result, target[:-1])
            parent[slot(parent, target[-1])] = value
            check_size(result)
            if observe:
                observe("mapping.applied", index=index, wire=rule.edgeId, source=rule.source,
                        target=rule.target, readAs=read_as.get(rule.edgeId, "value"), value=value)
        except MappingError as exc:
            if observe:
                observe("mapping.failed", index=index, rule=rule.model_dump(), error=str(exc))
            raise MappingError(f"Mapping {index} to {rule.target}: {exc}") from exc
    return result
