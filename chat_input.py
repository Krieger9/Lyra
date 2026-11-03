"""Chat request shape and per-call projections of an incoming flow value."""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator

from json_mapping import MappingError


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000, pattern=r"\S")
    history: list[Turn] = Field(default_factory=list, max_length=12)

    @classmethod
    def from_wire(cls, value):
        try:
            return cls.model_validate({"message": value} if isinstance(value, str) else value)
        except ValidationError as exc:
            error = exc.errors()[0]
            path = ".".join(map(str, error["loc"])) or "input"
            raise MappingError(f"Expected chat input (message and optional history). {path}: {error['msg']}") from exc


class InputSelection(BaseModel, extra="forbid"):
    source: Literal["wire", "chat"] = "wire"
    mode: Literal["message", "recent", "all"]
    turns: int | None = Field(default=None, ge=1, le=12, strict=True)

    @model_validator(mode="after")
    def validate_window(self):
        if (self.mode == "recent") != (self.turns is not None):
            raise ValueError("Only recent history takes a turn count (1–12).")
        return self

    def select(self, value, chat_context=None):
        if self.source == "chat":
            if chat_context is None:
                raise MappingError("Original chat request is unavailable. Supply chatContext for this run.")
            chat = chat_context
        else:
            chat = ChatRequest.from_wire(value)
        history = [] if self.mode == "message" else chat.history
        if self.turns is not None:
            history = history[-self.turns:]
        return chat.message, [turn.model_dump() for turn in history]
