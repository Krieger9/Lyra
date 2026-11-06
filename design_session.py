"""Request-scoped model tools for the built-in system agent."""
from pydantic import BaseModel, Field, ValidationError

from agent_store import PromptWrite
from design_api import DesignCall, PromptLookup, call_design, describe, operations
from json_mapping import json_text


class ListOperations(BaseModel, extra="forbid"):
    pass


class DescribeOperation(BaseModel, extra="forbid"):
    operation: str


class ReadPage(BaseModel, extra="forbid"):
    offset: int = Field(ge=0)


TOOL_TYPES = {
    "read_prompt": (PromptLookup, "Read back a prompt's exact text and version by name. Omit version for latest."),
    "publish_prompt": (PromptWrite, "Save a named prompt's text as a new version and return its published version."),
    "list_design_operations": (ListOperations, "List all available admin REST operation templates."),
    "describe_design_operation": (DescribeOperation, "Get the parameters and JSON body schema for an operation."),
    "call_design_operation": (DesignCall, "Execute a listed REST operation and read its status and data."),
    "read_result_page": (ReadPage, "Continue the last large tool result at the given character offset."),
}


def design_specs():
    return [{"name": name, "description": description, "parameters": model.model_json_schema()}
            for name, (model, description) in TOOL_TYPES.items()]


class DesignSession:
    def __init__(self, request, observe=None, *, allowed=None, dispatch=None):
        self.request = request
        self.observe = observe
        self.calls = 0
        self.receipts = []
        self.last_result = ""
        self.allowed = allowed
        self.dispatch = dispatch

    def permitted(self, operation):
        return self.allowed is None or operation in self.allowed

    def page(self, offset):
        if offset >= len(self.last_result):
            return {"error": "No result at that offset."}
        chunk = self.last_result[offset:offset + 12000]
        end = offset + len(chunk)
        return {"text": chunk, "next_offset": end if end < len(self.last_result) else None}

    async def call(self, name, arguments):
        self.calls += 1
        if name not in TOOL_TYPES:
            return {"error": "Unknown design tool."}
        try:
            args = TOOL_TYPES[name][0].model_validate(arguments)
            if isinstance(args, ReadPage):
                return self.page(args.offset)
            if isinstance(args, ListOperations):
                result = [op for op in operations(self.request.app) if self.permitted(op)]
            elif isinstance(args, DescribeOperation):
                if not self.permitted(args.operation):
                    raise ValueError("Operation unavailable for this method.")
                result = describe(self.request.app, args.operation)
            else:
                call = args
                if isinstance(args, PromptLookup):
                    call = args.as_call()
                elif isinstance(args, PromptWrite):
                    call = DesignCall(operation="POST /api/admin/prompts", body=args.model_dump(mode="json"))
                if not self.permitted(call.operation):
                    raise ValueError("Operation unavailable for this method.")
                result = (await self.dispatch(call) if self.dispatch else
                          await call_design(self.request, call, from_agent=True))
                receipt = {"operation": call.operation, "path": call.path, "status": result["status"]}
                self.receipts.append(receipt)
                if self.observe:
                    self.observe("design.operation", **receipt)
        except ValidationError as exc:
            return {"error": exc.errors(include_url=False, include_input=False)}
        except ValueError as exc:
            return {"error": str(exc)}
        self.last_result = json_text(result)
        return self.page(0) if len(self.last_result) > 12000 else result
