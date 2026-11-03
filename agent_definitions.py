"""Admin endpoints for agent definitions and prompt versions."""
import asyncio
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from prompt_studio import StorageConflictError
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent_output import run_agent
from agent_store import (AgentDefinition, AgentUpdate, PromptWrite, agent_values, definitions,
                         execution_agents, lock_model_catalog, serialize_agent, validate_selection)
from auth import require_admin_access
from app_context import library_for
from providers import catalog_entries


router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin_access)])


@router.get("/catalog")
def catalog(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    library = library_for(request)
    with library.storage.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        rows = connection.execute(select(definitions).where(
            definitions.c.library == library.name).order_by(definitions.c.name, definitions.c.id)).mappings().all()
        models = catalog_entries(connection, library)
    prompts = [version for name in library.list_prompts() for version in library.list_versions(name)]
    return {"agents": [serialize_agent(row) for row in rows], "prompts": prompts, **models}


@router.post("/agents", status_code=201)
def create_agent(body: AgentDefinition, request: Request):
    library = library_for(request)
    values = agent_values(body, library) | {"id": uuid4(), "revision": 1}
    with library.storage.engine.begin() as connection:
        lock_model_catalog(connection, library)
        validate_selection(connection, library, body.modelSelection)
        row = connection.execute(definitions.insert().values(**values).returning(definitions)).mappings().one()
    return serialize_agent(row)


@router.put("/agents/{agent_id}")
def update_agent(agent_id: UUID, body: AgentUpdate, request: Request):
    library = library_for(request)
    values = agent_values(body, library) | {"revision": body.revision + 1}
    if "outputSchema" not in body.model_fields_set:
        values.pop("output_schema")
    if "modelSelection" not in body.model_fields_set:
        values.pop("provider_id")
        values.pop("model_name")
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


class AgentTestInput(BaseModel):
    input: str = Field(max_length=8000)


@router.post("/agents/{agent_id}/test")
async def test_agent(agent_id: UUID, body: AgentTestInput, request: Request, response: Response):
    library = library_for(request)
    with library.storage.engine.connect() as connection:
        definition = execution_agents(connection, library, [agent_id]).get(str(agent_id))
    if definition is None:
        raise HTTPException(404, "Agent definition not found.")
    response.headers["Cache-Control"] = "no-store"
    try:
        output = await asyncio.wait_for(run_agent(library, request.app.state.studio, definition, body.input), 120)
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(504, "Agent test timed out.")
    except Exception:
        raise HTTPException(503, "Agent test failed. Check the model and database.")
    return {"output": output}


@router.post("/prompts", status_code=201)
def save_prompt(body: PromptWrite, request: Request):
    try:
        return library_for(request).add(body.name, body.text)
    except StorageConflictError:
        raise HTTPException(409, "Another prompt version was published at the same time. Try saving again.")
