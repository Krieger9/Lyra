"""Lightweight database summary for the admin landing page."""
from fastapi import APIRouter, Depends, Request, Response
from prompt_studio.sqlalchemy_storage import versions
from sqlalchemy import distinct, func, select

from agent_store import definitions
from app_context import library_for
from auth import require_admin_access
from flow_execution import plan_flow
from flow_graph import flows
from memory import memory_enabled
from prompt_sets import sets
from run_store import activation

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin_access)])


def chat_configuration(library, default_model):
    document = activation(library)
    if document is None:
        return {"activeFlow": None, "defaultModel": default_model}

    plan, _ = plan_flow(document.graph, document.agent_definitions())
    assignments = []

    def add_assignment(step, agent):
        selection = agent.modelSelection if agent else None
        assignments.append({"step": step, "agent": agent.name if agent else None,
                            "model": selection.model if selection else default_model,
                            "provider": agent.endpoint.provider if selection and agent.endpoint else None,
                            "usesDefault": selection is None})

    for node in plan:
        label = node.data.label or node.id
        if node.type == "agent":
            add_assignment(label, document.agents[str(node.data.definition.id)])
        elif node.type == "state" and node.data.stateDefinition.operation in ("generate", "generateObject"):
            add_assignment(label, document.agents[str(node.data.stateDefinition.agentId)])
        elif node.type == "remember" and memory_enabled():
            add_assignment(label, document.agents.get(str(node.data.extractorAgentId)))
    if document.designer:
        add_assignment("Flow Designer (when invoked)", document.designer)
    flow = document.model_dump(mode="json", include={"id", "name", "revision"})
    return {"activeFlow": flow | {"assignments": assignments}}


@router.get("/overview")
def workspace_overview(request: Request, response: Response, library=Depends(library_for)):
    response.headers["Cache-Control"] = "no-store"

    def count(table, column=None):
        aggregate = func.count() if column is None else func.count(distinct(column))
        return select(aggregate).select_from(table).where(table.c.library == library.name).scalar_subquery()

    with library.storage.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        totals = connection.execute(select(
            count(flows).label("flows"), count(definitions).label("agents"),
            count(versions, versions.c.name).label("prompts"), count(sets).label("sets"),
        )).mappings().one()
        recent = connection.execute(select(flows.c.id, flows.c.name, flows.c.revision)
            .where(flows.c.library == library.name)
            .order_by(flows.c.updated_at.desc(), flows.c.id).limit(5)).mappings().all()
    return {"counts": dict(totals), "recentFlows": [dict(row) for row in recent],
            **chat_configuration(library, request.app.state.studio.model.model.id)}
