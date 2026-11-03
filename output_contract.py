"""JSON value definitions used by agent output contracts and flow field discovery."""
import json
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from json_mapping import check_size


Choice = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
JSON_TYPES = {"text": "string", "choice": "string", "object": "object", "array": "array"}
VALUE_TYPES = {"string": str, "object": dict, "array": list}


def check_names(fields):
    if len({field.name for field in fields}) != len(fields):
        raise ValueError("Object field names must be unique.")


class ValueDefinition(BaseModel, extra="forbid"):
    type: Literal["text", "choice", "object", "array"]
    description: str = Field(default="", max_length=1000)
    choices: list[Choice] = Field(default_factory=list, max_length=64)
    fields: list["OutputField"] | None = Field(default=None, min_length=1, max_length=64)
    items: "ValueDefinition | None" = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.type == "choice" and (not self.choices or len(set(self.choices)) != len(self.choices)):
            raise ValueError("A choice needs distinct allowed values.")
        if self.type != "choice" and self.choices:
            raise ValueError("Only a choice accepts allowed values.")
        if (self.fields is not None) != (self.type == "object"):
            raise ValueError("Define fields for objects only.")
        if self.fields:
            check_names(self.fields)
        if (self.items is not None) != (self.type == "array"):
            raise ValueError("Define an item type for arrays only.")
        return self

    def json_schema(self):
        prop = {"type": JSON_TYPES[self.type]}
        if self.description:
            prop["description"] = self.description
        if self.choices:
            prop["enum"] = self.choices
        if self.fields:
            prop.update(object_schema(self.fields))
        if self.items:
            prop["items"] = self.items.json_schema()
        return prop


class OutputField(ValueDefinition):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    required: bool = True


def object_schema(fields):
    return {"type": "object", "properties": {field.name: field.json_schema() for field in fields},
            "additionalProperties": False, "required": [field.name for field in fields if field.required]}


class ObjectContract(BaseModel, extra="forbid"):
    fields: list[OutputField] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def check_definition(self):
        check_names(self.fields)
        return self

    def json_schema(self):
        return object_schema(self.fields)

    def text_paths(self):
        """Switches can select named text leaves. Array indices depend on the actual value."""
        def walk(fields, prefix):
            for field in fields:
                path = prefix + "/" + field.name
                if field.type in ("text", "choice"):
                    yield path, field.choices
                elif field.fields:
                    yield from walk(field.fields, path)
        return dict(walk(self.fields, ""))

    def validate_value(self, value, observe=None):
        check_size(value)

        def gate(path, operation, actual, expected, passed, error, trace):
            if observe and (trace or not passed):
                observe("gate.evaluated", field=path or None, operation=operation, actual=actual,
                        expected=expected, passed=passed)
            if not passed:
                raise ValueError(f"{path}: {error}" if path else error)

        def validate_object(fields, obj, path, trace):
            for field in fields:
                key = f"{path}.{field.name}" if path else field.name
                if field.required:
                    gate(key, "present", field.name in obj, True, field.name in obj, "required field is missing.", trace)
                if field.name in obj:
                    validate(field, obj[field.name], key, trace)
            extra = sorted(obj.keys() - {f.name for f in fields})
            gate(path, "additional_fields", extra, [], not extra,
                 f"Unexpected output field {extra[0][:64]!r}." if extra else "", trace)

        def validate(definition, item, path, trace):
            kind = JSON_TYPES[definition.type]
            gate(path, "type", type(item).__name__, kind, isinstance(item, VALUE_TYPES[kind]),
                 f"expected {kind}.", trace)
            if definition.choices:
                gate(path, "one_of", item, definition.choices, item in definition.choices,
                     f"expected one of {json.dumps(definition.choices)}, received {item[:120]!r}.", trace)
            elif definition.fields:
                validate_object(definition.fields, item, path, trace)
            elif definition.items:
                # Record one successful array gate instead of a database event per element.
                for index, element in enumerate(item):
                    validate(definition.items, element, f"{path}[{index}]", False)
                gate(path, "items", len(item), "valid items", True, "", trace)

        gate("", "type", type(value).__name__, "object", isinstance(value, dict), "Expected a JSON object.", True)
        validate_object(self.fields, value, "", True)
        return value


class OutputSchema(ObjectContract):
    def parse_output(self, text, observe=None):
        try:
            value = json.loads(text)
            check_size(value)
        except (ValueError, RecursionError) as exc:
            if observe:
                observe("gate.evaluated", operation="parse_json", passed=False, expected="valid JSON within 256 KiB")
            raise ValueError("Expected a valid JSON object within 256 KiB.") from exc
        if observe:
            observe("gate.evaluated", operation="parse_json", passed=True, expected="valid JSON within 256 KiB")
        return self.validate_value(value, observe)
