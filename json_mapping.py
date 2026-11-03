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
    if expression == ".json" or expression.startswith(".json/"):
        return pointer(expression[5:])
    raise MappingError("Use .text, .json or .json/path for the source.")


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


def assemble(definition, inputs):
    validate_mappings(definition, inputs.keys())
    result = deepcopy(definition.base)
    parsed = {}
    for index, rule in enumerate(definition.mappings, 1):
        value = inputs[rule.edgeId]
        path = source_path(rule.source)
        try:
            if path is None:
                value = value if isinstance(value, str) else json_text(value)
            else:
                if isinstance(value, str):
                    if rule.edgeId not in parsed:
                        try:
                            parsed[rule.edgeId] = json.loads(value)
                            check_size(parsed[rule.edgeId])
                        except (ValueError, RecursionError) as exc:
                            raise MappingError("Incoming text is not usable JSON.") from exc
                    value = parsed[rule.edgeId]
                value = lookup(value, path)
            target = pointer(rule.target)
            parent = lookup(result, target[:-1])
            parent[slot(parent, target[-1])] = value
            check_size(result)
        except MappingError as exc:
            raise MappingError(f"Mapping {index} to {rule.target}: {exc}") from exc
    return result
