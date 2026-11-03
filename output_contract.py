"""Structured output contracts shared by storage, execution and adapters."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from json_mapping import check_size


Choice = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class OutputField(BaseModel, extra="forbid"):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    type: Literal["text", "choice"]
    description: str = Field(default="", max_length=1000)
    required: bool = True
    choices: list[Choice] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_choices(self):
        if self.type == "choice" and (not self.choices or len(set(self.choices)) != len(self.choices)):
            raise ValueError("A choice field needs distinct allowed values.")
        if self.type == "text" and self.choices:
            raise ValueError("Only a choice field accepts allowed values.")
        return self


class OutputSchema(BaseModel, extra="forbid"):
    fields: list[OutputField] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique_names(self):
        if len({f.name for f in self.fields}) != len(self.fields):
            raise ValueError("Output field names must be unique.")
        return self

    def json_schema(self):
        properties = {}
        for field in self.fields:
            prop = {"type": "string"}
            if field.description:
                prop["description"] = field.description
            if field.choices:
                prop["enum"] = field.choices
            properties[field.name] = prop
        return {"type": "object", "properties": properties, "additionalProperties": False,
                "required": [field.name for field in self.fields if field.required]}

    def parse_output(self, text, observe=None):
        def gate(field, operation, actual, expected, passed):
            if observe:
                observe("gate.evaluated", field=field, operation=operation, actual=actual, expected=expected, passed=passed)
            return passed

        try:
            value = json.loads(text)
            check_size(value)
        except (ValueError, RecursionError) as exc:
            if observe:
                observe("gate.evaluated", operation="parse_json", passed=False, expected="valid JSON within 256 KiB")
            raise ValueError("Expected a valid JSON object within 256 KiB.") from exc
        if observe:
            observe("gate.evaluated", operation="parse_json", passed=True, expected="valid JSON within 256 KiB")
        if not gate(None, "type", type(value).__name__, "object", isinstance(value, dict)):
            raise ValueError("Expected a JSON object.")
        for field in self.fields:
            if field.required:
                gate(field.name, "present", field.name in value, True, field.name in value)
            if field.name not in value:
                if field.required:
                    raise ValueError(f"{field.name}: required field is missing.")
                continue
            item = value[field.name]
            if not gate(field.name, "type", type(item).__name__, "string", isinstance(item, str)):
                raise ValueError(f"{field.name}: expected a string.")
            if field.choices and not gate(field.name, "one_of", item, field.choices, item in field.choices):
                raise ValueError(f"{field.name}: expected one of {json.dumps(field.choices)}, received {item[:120]!r}.")
        extra = value.keys() - {field.name for field in self.fields}
        if not gate(None, "additional_fields", sorted(extra), [], not extra):
            raise ValueError(f"Unexpected output field {min(extra)[:64]!r}.")
        return value
