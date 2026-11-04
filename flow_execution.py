"""Plan and execute one immutable flow snapshot, shared by chat and both run APIs."""
import asyncio
import logging
from time import perf_counter

from fastapi import HTTPException

from agent_output import ObservedModel, run_agent
from chat_input import ChatRequest
from flow_graph import FlowSnapshot, path_requirements
from flow_switch import FlowFields
from json_mapping import MappingError, assemble, validate_mappings
from run_store import RUN_TIMEOUT
from model_adapter import provider_model


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
    fields = FlowFields(nodes, incoming, agents, graph.stateObject)
    required_paths = {"api-input": {}}
    ancestors = {"api-input": set()}
    state_accesses = {}

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
        ancestors[node_id] = {edge.source for edge in parents}
        for edge in parents:
            ancestors[node_id].update(ancestors[edge.source])
        if node.type == "state":
            definition = node.data.stateDefinition
            definition.validate_setup(graph.stateObject, parents, agents)
            if len({edge.name or edge.id for edge in parents}) != len(parents):
                raise MappingError("State inputs need distinct wire names.")
            accesses = state_accesses.setdefault((definition.scope, definition.key), [])
            for other in accesses:
                guard = required_paths[other.id]
                exclusive = any(path in guard and guard[path] != port for path, port in required_paths[node_id].items())
                if other.id in ancestors[node_id] or exclusive:
                    continue
                previous = other.data.stateDefinition
                if (definition.operation, previous.operation) != ("read", "read") or definition.initial != previous.initial:
                    raise HTTPException(422, f"Connect the State accesses for '{definition.key}' in execution order.")
            accesses.append(node)
        if node.type in ("agent", "json", "switch", "state", "apiResponse"):
            plan.append(node)
        visiting.remove(node_id)
        completed.add(node_id)

    for node in graph.nodes:
        if node.id in reached and node.id != "api-response":
            visit(node.id)
    visit("api-response")
    return plan, incoming


async def execute(graph, agents, library, studio, value, run, chat_context=None, conversation_state=None,
                  memory=None, personality=None):
    plan, incoming = plan_flow(graph, agents, run)
    state = {"run": {}, "conversation": conversation_state if conversation_state is not None else {}}
    run.node = None
    run.emit("plan.ready", order=["api-input", *[n.id for n in plan]])
    input_node = next(node for node in graph.nodes if node.id == "api-input")
    if memory and input_node.data.recallMemories:
        run.node = "api-input"
        await memory.recall(chat_context, state["conversation"])
        run.emit("memory.recalled", count=len(memory.recalled), memories=memory.recalled,
                 ids=[str(item["id"]) for item in memory.recalled])
        run.node = None
    outputs = {}
    selected_paths = {}
    nodes = {n.id: n for n in graph.nodes}
    for node in [nodes["api-input"], *plan]:
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
            if node.data.chatInput:
                request = ChatRequest.from_wire(value)
                output = request.model_dump()
                if chat_context is None:
                    chat_context = request
        elif node.type == "apiResponse":
            output = inputs[parents[0].id] if parents else ""
        elif node.type == "json":
            output = assemble(node.data.objectDefinition, inputs, {e.id: e.readAs for e in parents}, run.emit)
        elif node.type == "state":
            definition = node.data.stateDefinition
            bucket = state[definition.scope]
            named = {edge.name or edge.id: inputs[edge.id] for edge in parents}
            incoming_value = inputs[parents[0].id] if len(parents) == 1 else named
            write_value = inputs if definition.operation == "map" else incoming_value
            if definition.operation in ("generate", "generateObject"):
                current = graph.stateObject | bucket.get(definition.key, definition.initial)
                payload = {"state": current, "inputs": named}
                if definition.includeChatTurn:
                    chat = chat_context or ChatRequest.from_wire(value)
                    payload["history"] = [turn.model_dump() for turn in chat.history]
                    payload["latestTurn"] = {"user": chat.message, "assistant": outputs["api-response"]}
                    if memory:
                        payload["recalledMemories"] = memory.recalled
                result = await run_agent(library, studio, agents[str(definition.agentId)], payload, run.emit,
                                         memories=memory.recalled if memory else None)
                if memory and definition.includeChatTurn and definition.captureMemories:
                    agent = agents[str(definition.agentId)]
                    selection = agent.get("modelSelection")
                    model = provider_model(agent["endpoint"], selection["model"]) if selection else studio.model
                    before = len(memory.pending)
                    await memory.capture(payload, ObservedModel(model, run.emit), node.id)
                    selected = memory.pending[before:]
                    run.emit("memory.extracted", count=len(selected),
                             memories=[{key: item[key] for key in ("content", "kind", "evidence")} for item in selected])
                write_value = {"result": result} if definition.operation == "generateObject" else result
            current = definition.apply(bucket, write_value, graph.stateObject, run.emit)
            run.emit("state.applied", key=definition.key, scope=definition.scope, operation=definition.operation, value=current)
            output = definition.project(current, inputs, incoming_value)
        elif node.type == "switch":
            output = inputs[parents[0].id]
            path_id, label = node.data.switchDefinition.select(output)
            selected_paths[node.id] = path_id
            run.emit("switch.selected", path=path_id, label=label,
                     wires=[e.id for e in graph.edges if e.source == node.id and e.sourceHandle == path_id])
        else:
            definition = agents[str(node.data.definition.id)]
            output = await run_agent(library, studio, definition, inputs[parents[0].id], run.emit,
                                     selection=node.data.inputSelection, chat_context=chat_context,
                                     memories=memory.recalled if memory else None,
                                     personality=personality if node.data.usePersonality else None)
        outputs[node.id] = output
        run.emit("node.completed", output=output, duration_ms=round((perf_counter()-start)*1000))
    run.node = None
    return outputs["api-response"]


async def perform(document: FlowSnapshot, library, studio, value, run, chat_context=None, turn=None, memory=None):
    run.emit("run.started", input=value)
    try:
        result = await asyncio.wait_for(execute(document.graph, document.agent_definitions(), library, studio,
            value, run, chat_context, turn.objects if turn else None, memory, document.personality), RUN_TIMEOUT)
        run.finish("completed", result=result, turn=turn, memory=memory)
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
    return result


async def run_in_background(*args):
    try:
        await perform(*args)
    except HTTPException:
        pass  # The terminal error is persisted for the Run inspector.
