"""A single flow-design tool for conversation agents, scoped to one admin chat turn."""
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from design_api import call_design
from design_session import DesignSession
from flow_graph import flows, snapshot
from run_store import active, runs


class DesignTask(BaseModel, extra="forbid"):
    method: Literal["Query", "Explain", "Update"]
    request: str = Field(min_length=1, max_length=8000)
    flow_id: UUID | None = None


SPEC = {"name": "flow_designer", "parameters": DesignTask.model_json_schema(),
        "description": "Delegate a whole design task in plain language. Query reads configuration and returns data. "
        "Explain investigates behavior, tradeoffs or complex ideas. Update builds and tests drafts. "
        "Only request activation when the user explicitly asks to go live."}

READ = {"GET /api/admin/catalog", "GET /api/admin/flows", "GET /api/admin/flows/{flow_id}",
        "GET /api/admin/chat-flow", "GET /api/admin/prompts", "GET /api/admin/memory-status",
        "GET /api/admin/personalities", "GET /api/admin/personalities/{personality_id}"}
TEST = "POST /api/admin/flows/{flow_id}/test-turn"
ACTIVATE = "PUT /api/admin/chat-flow/{flow_id}"
WRITE = {"POST /api/admin/agents", "PUT /api/admin/agents/{agent_id}", "POST /api/admin/prompts",
         "POST /api/admin/flows", "PUT /api/admin/flows/{flow_id}", TEST}


class DesignerTurn:
    def __init__(self, request, document, allow_activation, observe, *, read_only=False, definition=None):
        self.request, self.document = request, document
        self.definition = definition if definition is not None else document.designer.model_dump(mode="json")
        self.read_only = read_only
        self.calls = 0
        self.allow_activation, self.observe = allow_activation, observe
        self.pending = None

    async def dispatch(self, call):
        library = self.request.app.state.library
        if call.operation == ACTIVATE:
            from run_api import validate_chat_binding
            flow_id = UUID(call.path.get("flow_id", ""))
            revision = call.body.get("revision") if isinstance(call.body, dict) else None
            if not self.allow_activation or type(revision) is not int:
                return {"status": 403, "data": "Activation requires permission and a saved revision."}
            try:
                draft = snapshot(library, flow_id, revision)
                validate_chat_binding(library, draft)
            except HTTPException as exc:
                return {"status": exc.status_code, "data": exc.detail}
            with library.storage.engine.connect() as conn:
                verified = conn.execute(select(runs.c.id).where(
                    runs.c.library == library.name, runs.c.flow_id == flow_id, runs.c.revision == revision,
                    runs.c.status == "completed", runs.c.source == "debug",
                    runs.c.snapshot == draft.model_dump(mode="json", exclude_unset=True)).limit(1)).scalar_one_or_none()
            if not verified:
                return {"status": 409, "data": "Run a successful test of this exact draft and its pinned dependencies first."}
            self.pending = draft
            return {"status": 202, "data": {"activation": "staged until the chat turn succeeds",
                                            "id": str(flow_id), "revision": revision, "test_run": str(verified)}}
        if call.operation == "PUT /api/admin/agents/{agent_id}" and UUID(call.path.get("agent_id", "")) == UUID(self.definition["id"]):
            return {"status": 403, "data": "The delegated designer cannot edit its own definition."}
        return await call_design(self.request, call, from_agent=True)

    def commit(self, conn, library):
        if self.pending is None:
            return
        draft = self.pending
        current = conn.execute(select(flows.c.revision).where(flows.c.library == library,
            flows.c.id == draft.id).with_for_update()).scalar_one_or_none()
        if current != draft.revision:
            raise HTTPException(409, "Draft changed after verification. Test the new revision.")
        old = self.document.model_dump(mode="json", exclude_unset=True)
        saved = draft.model_dump(mode="json", exclude_unset=True)
        changed = conn.execute(active.update().where(active.c.library == library, active.c.snapshot == old)
                               .values(snapshot=saved))
        if not changed.rowcount:
            raise HTTPException(409, "Chat activation changed while designing. Review the latest binding.")

    def specs(self):
        if self.read_only:
            schema = DesignTask.model_json_schema()
            schema["properties"]["method"]["enum"] = ["Query", "Explain"]
            return [SPEC | {"parameters": schema, "description": "Ask the System Flow Designer to inspect flow wiring, "
                            "agent definitions, model configuration or explain design ideas. This session is read-only."}]
        return [SPEC]

    async def call(self, name, arguments):
        from agent_output import run_agent
        self.calls += 1
        try:
            task = DesignTask.model_validate(arguments)
        except ValidationError as exc:
            return {"error": str(exc)}
        allowed = READ
        if task.method == "Update":
            if self.read_only:
                return {"error": "Select Work on drafts in chat to enable Update."}
            allowed = READ | WRITE
            if self.allow_activation:
                allowed = allowed | {ACTIVATE}
        session = DesignSession(self.request, self.observe, allowed=allowed, dispatch=self.dispatch)
        payload = task.model_dump(mode="json") | {"flow_in_context": str(self.document.id) if self.document else None,
            "instructions": "Use only the operations available for this method. For Update, test the saved draft "
            "and report its ID, revision, test result and any incomplete work. Stage activation only if requested."}
        if self.observe:
            self.observe("designer.request", method=task.method, request=task.request)
        try:
            answer = await run_agent(self.request.app.state.library, self.request.app.state.studio,
                self.definition, payload, self.observe, design_session=session)
            return {"answer": answer, "operations": session.receipts}
        except HTTPException as exc:
            self.pending = None
            return {"error": exc.detail, "operations": session.receipts}


