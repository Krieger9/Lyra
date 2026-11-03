"""Plan and execute one immutable flow snapshot, shared by chat and both run APIs."""
import asyncio
import logging
from time import perf_counter

from fastapi import HTTPException

from agent_output import run_agent
from flow_graph import FlowSnapshot, path_requirements
from flow_switch import FlowFields
from json_mapping import MappingError, assemble, validate_mappings
from run_store import RUN_TIMEOUT


def plan_flow(graph, agents, observe=None):
    nodes = {node.id: node for node in graph.nodes}
    incoming = {node.id: [] for node in graph.nodes}
    outgoing = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        incoming[edge.target].append(edge)
        outgoing[edge.source].append(edge.target)
    reached = {"api-input"}
    pending = ["api-input"]
    while pending:
        source = pending.pop()
        for target in outgoing[source]:
            if target not in reached:
                reached.add(target)
                pending.append(target)
    # Every connected branch must be valid before any model is called.
    plan, visiting, completed = [], set(), {"api-input"}
    fields = FlowFields(nodes, incoming, agents)
    required_paths = {"api-input": {}}

    def visit(node_id):
        if observe:
            observe.node = node_id
        if node_id in visiting:
            raise HTTPException(422, "The connected flow contains a cycle.")
        if node_id in completed:
            return
        visiting.add(node_id)
        node = nodes[node_id]
        parents = incoming[node_id]
        if node.type == "json":
            if node.data.objectDefinition is None:
                raise HTTPException(422, "JSON node needs an object definition.")
            validate_mappings(node.data.objectDefinition, {edge.id for edge in parents})
        if node.type == "agent" and (len(parents) != 1 or not node.data.definition):
            raise HTTPException(422, "Each connected Agent needs a definition and exactly one input to run.")
        if node.type == "agent" and str(node.data.definition.id) not in agents:
            raise HTTPException(422, "A connected Agent definition is unavailable.")
        for edge in parents:
            visit(edge.source)
        if observe:
            observe.node = node_id
        if node.type == "switch":
            if len(parents) != 1 or node.data.switchDefinition is None:
                raise HTTPException(422, "Each connected Switch needs one input and a Switch definition.")
            node.data.switchDefinition.validate_fields(fields.output(parents[0].source))
        required_paths[node_id] = path_requirements(node, parents, required_paths)
        if node.type in ("agent", "json", "switch"):
            plan.append(node)
        visiting.remove(node_id)
        completed.add(node_id)

    for node in graph.nodes:
        if node.id in reached or node.id == "api-response":
            visit(node.id)
    return plan, incoming


async def execute(graph, agents, library, studio, value, run):
    plan, incoming = plan_flow(graph, agents, run)
    run.node = None
    run.emit("plan.ready", order=["api-input", *[n.id for n in plan], "api-response"])
    outputs = {}
    selected_paths = {}
    nodes = {n.id: n for n in graph.nodes}
    for node in [nodes["api-input"], *plan, nodes["api-response"]]:
        run.node = node.id
        start = perf_counter()
        connections = incoming[node.id]
        parents = [e for e in connections if e.source in outputs and
                   (e.sourceHandle is None or e.sourceHandle == selected_paths.get(e.source))]
        if node.type == "apiResponse" and connections and len(parents) != 1:
            raise HTTPException(422, "API Response requires exactly one active input. Connect the selected path to the response.")
        if node.type != "apiResponse" and len(parents) != len(connections):
            run.emit("node.skipped", reason="An input path was not selected.")
            continue
        inputs = {e.id: outputs[e.source] for e in parents}
        run.emit("node.started", inputs=[{"wire": e.id, "name": e.name, "source": e.source, "value": inputs[e.id]}
                                          for e in parents])
        if node.type == "apiInput":
            output = value
        elif node.type == "apiResponse":
            output = inputs[parents[0].id] if parents else ""
        elif node.type == "json":
            output = assemble(node.data.objectDefinition, inputs, {e.id: e.readAs for e in parents}, run.emit)
        elif node.type == "switch":
            output = inputs[parents[0].id]
            path_id, label = node.data.switchDefinition.select(output)
            selected_paths[node.id] = path_id
            run.emit("switch.selected", path=path_id, label=label,
                     wires=[e.id for e in graph.edges if e.source == node.id and e.sourceHandle == path_id])
        else:
            definition = agents[str(node.data.definition.id)]
            output = await run_agent(library, studio, definition, inputs[parents[0].id], run.emit)
        outputs[node.id] = output
        run.emit("node.completed", output=output, duration_ms=round((perf_counter()-start)*1000))
    run.node = None
    return outputs["api-response"]


async def perform(document: FlowSnapshot, library, studio, value, run):
    run.emit("run.started", input=value)
    try:
        result = await asyncio.wait_for(execute(document.graph, document.agent_definitions(), library, studio, value, run), RUN_TIMEOUT)
    except HTTPException as exc:
        run.finish("failed", error=exc.detail)
        raise
    except MappingError as exc:
        run.finish("failed", error=str(exc))
        raise HTTPException(422, str(exc)) from exc
    except TimeoutError as exc:
        run.finish("failed", error="Flow execution timed out.")
        raise HTTPException(504, "Flow execution timed out.") from exc
    except asyncio.CancelledError:
        run.finish("interrupted", error="Flow execution interrupted.")
        raise
    except Exception as exc:
        logging.getLogger(__name__).error("Flow run %s failed (%s)", run.id, type(exc).__name__)
        run.finish("failed", error=f"Execution failed ({type(exc).__name__}).")
        raise HTTPException(503, "Flow execution failed. Check the model and database.") from exc
    run.finish("completed", result=result)
    return result


async def run_in_background(*args):
    try:
        await perform(*args)
    except HTTPException:
        pass  # The terminal error is persisted for the Run inspector.
