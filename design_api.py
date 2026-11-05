"""Discover and invoke the admin workspace's actual REST operations in process."""
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field, JsonValue


def operations(app):
    return {f"{method.upper()} {path}": operation
            for path, methods in app.openapi()["paths"].items() if path.startswith("/api/admin/")
            for method, operation in methods.items() if method in {"get", "post", "put", "patch", "delete"}}


def describe(app, operation):
    entry = operations(app).get(operation)
    if entry is None:
        raise ValueError("Choose an operation from list_design_operations.")
    description = {k: entry[k] for k in ("summary", "description", "parameters", "requestBody") if k in entry}
    schemas = app.openapi()["components"]["schemas"]
    referenced = {}

    def collect(value):
        if isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            if "$ref" in value:
                name = value["$ref"].rsplit("/", 1)[1]
                if name not in referenced:
                    referenced[name] = schemas[name]
                    collect(schemas[name])
            for item in value.values():
                collect(item)

    collect(description)
    return {**description, "components": {"schemas": referenced}}


class DesignCall(BaseModel, extra="forbid"):
    operation: str = Field(description="Exact METHOD and route template from list_design_operations.")
    path: dict[str, str] = Field(default_factory=dict, description="Values for the template's path parameters.")
    query: dict[str, str | int | bool] = Field(default_factory=dict)
    body: JsonValue = None


class PromptLookup(BaseModel, extra="forbid"):
    name: str
    version: int | None = Field(None, ge=1, description="Omit to read the latest version.")

    def as_call(self):
        return DesignCall(operation="GET /api/admin/prompts", query=self.model_dump(exclude_none=True))


async def call_design(request, call: DesignCall, *, from_agent=False):
    entry = operations(request.app).get(call.operation)
    if entry is None:
        raise ValueError("Choose an operation from list_design_operations.")
    method, template = call.operation.split(" ", 1)
    expected = {p["name"] for p in entry.get("parameters", []) if p["in"] == "path"}
    if set(call.path) != expected:
        raise ValueError(f"Supply path parameters: {sorted(expected)}")
    for segment in call.path.values():
        if not segment or segment in {".", ".."} or any(c in segment for c in "/\\%"):
            raise ValueError("Invalid path segment.")
    url = template.format_map({k: quote(v, safe="") for k, v in call.path.items()})

    async def local_app(scope, receive, send):
        # Private ASGI state, never accepted from client headers or model arguments.
        scope["lyra.system_agent_call"] = from_agent
        scope["lyra.design_mcp"] = request.scope.get("lyra.design_mcp", False)
        await request.app(scope, receive, send)

    headers = {"authorization": request.headers.get("authorization", "")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(local_app, raise_app_exceptions=False),
                                 base_url="http://lyra.internal", headers=headers) as client:
        response = await client.request(method, url, params=call.query, json=call.body)
    try:
        data = response.json()
    except ValueError:
        data = response.text
    return {"status": response.status_code, "data": data,
            **({"run_id": response.headers["x-lyra-run-id"]} if "x-lyra-run-id" in response.headers else {})}
