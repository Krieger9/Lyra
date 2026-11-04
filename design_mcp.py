"""MCP tools for editing the same design library as the Lyra admin workspace."""
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from starlette.concurrency import run_in_threadpool
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

import agent_definitions
import flows
import run_api
from chat_input import ChatRequest
from conversation_state import open_turn
from flow_execution import perform
from agent_store import AgentDefinition, AgentUpdate, PromptWrite
from auth import require_design_access
from flow_graph import FlowUpdate, FlowWrite, load_flow, serialize_flow, snapshot
from run_mcp import mount_mcp, register_run_tools
from run_store import Run


def install_design_mcp(app):
    server = FastMCP("Lyra design", stateless_http=True, json_response=True)
    read = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, openWorldHint=False)
    register_run_tools(app, server)

    @server.tool(annotations=read)
    async def get_catalog() -> dict[str, Any]:
        """Read saved agents, their revisions, prompt versions and available model selections."""
        return jsonable_encoder(await run_in_threadpool(agent_definitions.read_catalog, app.state.library))

    @server.tool(annotations=write)
    async def publish_prompt(body: PromptWrite) -> dict[str, Any]:
        """Publish a new prompt version. Existing pinned agents keep their selected version."""
        return jsonable_encoder(await run_in_threadpool(agent_definitions.save_prompt, body, app.state.library))

    @server.tool(annotations=write)
    async def create_agent(body: AgentDefinition) -> dict[str, Any]:
        """Create a reusable agent. Select prompt and model references from get_catalog."""
        return await run_in_threadpool(agent_definitions.create_agent, body, app.state.library)

    @server.tool(annotations=write)
    async def update_agent(agent_id: UUID, body: AgentUpdate) -> dict[str, Any]:
        """Save an agent definition using its current revision. Reload get_catalog on conflict."""
        return await run_in_threadpool(agent_definitions.update_agent, agent_id, body, app.state.library)

    @server.tool(annotations=read)
    async def list_flows() -> list[dict[str, Any]]:
        """List saved flow IDs, names and revisions."""
        return await run_in_threadpool(flows.flow_summaries, app.state.library)

    @server.tool(annotations=read)
    async def get_flow(flow_id: UUID) -> dict[str, Any]:
        """Read the complete editable graph and its current revision."""
        return serialize_flow(await run_in_threadpool(load_flow, app.state.library, flow_id))

    @server.tool(annotations=write)
    async def create_flow(body: FlowWrite) -> dict[str, Any]:
        """Save a new flow design. This does not activate it for chat."""
        return await run_in_threadpool(flows.create_flow, body, app.state.library)

    @server.tool(annotations=write)
    async def update_flow(flow_id: UUID, body: FlowUpdate) -> dict[str, Any]:
        """Replace the complete saved design using the revision from get_flow. Reload on conflict."""
        return await run_in_threadpool(flows.update_flow, flow_id, body, app.state.library)

    @server.tool(annotations=write)
    async def test_flow_turn(ctx: Context, flow_id: UUID, revision: int, conversation_id: UUID,
                             turn_id: UUID, chat: ChatRequest) -> dict[str, Any]:
        """Run a saved revision with real models in a private design-test conversation. Reuse its
        conversation_id across turns and its turn_id only for retries. Inspect the returned run for State."""
        library = app.state.library
        document = await run_in_threadpool(snapshot, library, flow_id, revision)
        owner = ctx.request_context.request.state.design_owner + "/" + str(revision)
        turn, replay = await run_in_threadpool(open_turn, library, owner, flow_id, conversation_id, turn_id, chat)
        if replay:
            return {"run_id": str(replay[0]), "reply": replay[1]}
        run = Run(library, document, "debug")
        value = chat.model_dump() if document.graph.chat_input else chat.message
        reply = await perform(document, library, app.state.studio, value, run, chat, turn)
        return {"run_id": str(run.id), "reply": reply}

    @server.tool(annotations=read)
    async def get_chat_binding(ctx: Context) -> dict[str, Any]:
        """Read the currently activated chat flow and pinned revision."""
        return {"active": await run_in_threadpool(run_api.chat_binding, ctx.request_context.request)}

    @server.tool(annotations=write)
    async def activate_chat_flow(ctx: Context, flow_id: UUID, revision: int) -> dict[str, Any]:
        """Validate a saved revision and activate its snapshot for chat."""
        return await run_in_threadpool(run_api.bind, flow_id, run_api.Revision(revision=revision), ctx.request_context.request)

    return mount_mcp(app, server, "/mcp/design", require_design_access)
