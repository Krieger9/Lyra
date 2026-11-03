"""Named JSON objects shared by State elements during one Flow execution."""
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, field_validator

from json_mapping import MappingError, check_size


class StateDefinition(BaseModel, extra="forbid"):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z_][a-zA-Z0-9_.-]*$")
    scope: Literal["run", "conversation"]
    operation: Literal["read", "update", "replace"]
    initial: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("initial")
    @classmethod
    def bounded_initial(cls, value):
        check_size(value)
        return value

    def apply(self, objects, value):
        current = objects.get(self.key, self.initial)
        if self.operation != "read":
            if not isinstance(value, dict):
                raise MappingError("State writes require an object. Use a JSON element to map the incoming value.")
            current = current | value if self.operation == "update" else value
        check_size(current)
        objects[self.key] = current
        return current
