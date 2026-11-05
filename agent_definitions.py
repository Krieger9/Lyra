"""Admin endpoints for agent definitions and prompt versions."""
import asyncio
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from prompt_studio import NotFoundError, StorageConflictError
from pydantic import JsonValue
from sqlalchemy import select

from agent_output import run_agent
from agent_store import (AgentDefinition, AgentUpdate, PromptWrite, agent_values, definitions, designer_id,
                         execution_agents, lock_model_catalog, serialize_agent, validate_selection)
from auth import require_admin_access
from app_context import library_for
from chat_input import InputSelection
from flow_graph import FlowRun
from json_mapping import MappingError
from providers import catalog_entries
from design_session import DesignSession
from tool_sets import FLOW_DESIGN, TOOL_SETS


router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin_access)])


@router.get("/catalog")
def catalog(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return read_catalog(library_for(request))


def read_catalog(library):
    with library.storage.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        rows = connection.execute(select(definitions).where(
            definitions.c.library == library.name).order_by(definitions.c.name, definitions.c.id)).mappings().all()
        models = catalog_entries(connection, library)
    prompts = [version for name in library.list_prompts() for version in library.list_versions(name)]
    return {"agents": [serialize_agent(row) for row in rows], "prompts": prompts, "toolSets": list(TOOL_SETS.values()), **models}


@router.post("/agents", status_code=201)
def create_agent(body: AgentDefinition, library=Depends(library_for)):
    values = agent_values(body, library) | {"id": uuid4(), "revision": 1}
    with library.storage.engine.begin() as connection:
        lock_model_catalog(connection, library)
        validate_selection(connection, library, body.modelSelection)
        row = connection.execute(definitions.insert().values(**values).returning(definitions)).mappings().one()
    return serialize_agent(row)


@router.put("/agents/{agent_id}")
def update_agent(agent_id: UUID, body: AgentUpdate, library=Depends(library_for)):
    values = agent_values(body, library) | {"revision": body.revision + 1}
    if "outputSchema" not in body.model_fields_set:
        values.pop("output_schema")
    if "modelSelection" not in body.model_fields_set:
        values.pop("provider_id")
        values.pop("model_name")
    if "toolSets" not in body.model_fields_set:
        values.pop("tool_sets")
    with library.storage.engine.begin() as connection:
        lock_model_catalog(connection, library)
        validate_selection(connection, library, body.modelSelection)
        row = connection.execute(definitions.update().where(
            definitions.c.id == agent_id, definitions.c.library == library.name,
            definitions.c.revision == body.revision,
        ).values(**values).returning(definitions)).mappings().first()
        if row is None:
            raise HTTPException(409, "This agent changed or is unavailable. Reload before saving.")
    return serialize_agent(row)


class AgentTestInput(FlowRun):
    input: str | dict[str, JsonValue] | list[JsonValue]
    inputSelection: InputSelection | None = None


@router.post("/agents/{agent_id}/test")
async def test_agent(agent_id: UUID, body: AgentTestInput, request: Request, response: Response):
    library = library_for(request)
    designer = None
    with library.storage.engine.connect() as connection:
        definition = execution_agents(connection, library, [agent_id]).get(str(agent_id))
        if definition and FLOW_DESIGN in definition["toolSets"] and not request.scope.get("lyra.system_agent_call"):
            from delegated_designer import DesignerTurn
            from run_store import activation
            key = designer_id(library.name)
            delegate = execution_agents(connection, library, [key]).get(str(key))
            if delegate is None:
                raise HTTPException(422, "System Flow Designer is unavailable.")
            designer = DesignerTurn(request, activation(library), False, None, read_only=True, definition=delegate)
    if definition is None:
        raise HTTPException(404, "Agent definition not found.")
    response.headers["Cache-Control"] = "no-store"
    session = DesignSession(request)

    def failure(status, detail):
        if session.receipts:
            calls = ", ".join(f"{r['operation']} (HTTP {r['status']})" for r in session.receipts)
            detail = f"{detail} API calls already made: {calls}. Check their results before retrying."
        return HTTPException(status, detail)

    try:
        output = await asyncio.wait_for(run_agent(library, request.app.state.studio, definition, body.input,
                                                  selection=body.inputSelection, chat_context=body.chatContext,
                                                  design_session=session, designer=designer), 120)
    except MappingError as exc:
        raise failure(422, str(exc)) from exc
    except HTTPException as exc:
        if session.receipts:
            raise failure(exc.status_code, exc.detail) from exc
        raise
    except asyncio.TimeoutError:
        raise failure(504, "Agent test timed out.")
    except Exception:
        raise failure(503, "Agent test failed. Check the model and database.")
    return {"output": output, **({"operations": session.receipts} if definition.get("systemAgent") else {})}


@router.post("/prompts", status_code=201)
def save_prompt(body: PromptWrite, library=Depends(library_for)):
    try:
        return library.add(body.name, body.text)
    except StorageConflictError:
        raise HTTPException(409, "Another prompt version was published at the same time. Try saving again.")


@router.get("/prompts")
def read_prompt(name: str, response: Response, version: int | None = Query(None, ge=1), library=Depends(library_for)):
    """Read one prompt's text and version. Omit version to read the latest publication."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return library.get(name, version)
    except NotFoundError as exc:
        raise HTTPException(404, "Prompt version not found.") from exc
