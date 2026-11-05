"""Plan and execute one immutable flow snapshot, shared by chat and both run APIs."""
import asyncio
import logging
from time import perf_counter

from fastapi import HTTPException
from starlette.responses import Response

from agent_output import ObservedModel, run_agent
from design_session import DesignSession
from chat_input import ChatRequest
from flow_graph import FlowSnapshot, path_requirements
from flow_switch import FlowFields
from json_mapping import MappingError, assemble, json_text, validate_mappings
from run_store import RUN_TIMEOUT
from model_adapter import provider_model
from tool_sets import FLOW_DESIGN


def plan_flow(graph, agents, observe=None):
    nodes = {node.id: node for node in graph.nodes}
    incoming = {node.id: [] for node in graph.nodes}
    outgoing = {node.id: [] for node in graph.nodes}
    for edge in graph.data_edges:
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
    memory_sources = graph.memory_sources

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
        if node.type in ("recall", "remember") and len(parents) != 1:
            raise MappingError(f"{node.type.title()} requires exactly one input.")
        if node.type == "remember":
            if node.data.extractorAgentId and str(node.data.extractorAgentId) not in agents:
                raise MappingError("The Remember extractor agent is unavailable.")
            source = nodes[parents[0].source]
            if node.data.memoryEvidence == "exchange" and not (source.type == "apiResponse" or
                    source.type == "state" and source.data.stateDefinition.includeChatTurn):
                raise MappingError("For completed exchange evidence, connect API Response or a State that includes the completed turn.")
            if node.data.rememberWhen == "afterResponse":
                visit("api-response")
        if node_id in memory_sources:
            visit(memory_sources[node_id])
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
        if node.type in ("agent", "json", "switch", "state", "apiResponse", "recall", "remember"):
            plan.append(node)
        visiting.remove(node_id)
        completed.add(node_id)

    for node in graph.nodes:
        if node.id in reached and node.type == "remember" and node.data.rememberWhen == "onEvidence":
            visit(node.id)
    visit("api-response")
    for node in graph.nodes:
        if node.id in reached and node.id != "api-response":
            visit(node.id)
    return plan, incoming


async def extract_memories(memory, payload, agent, studio, run, node_id, chat=None):
    selection = agent.get("modelSelection")
    model = provider_model(agent["endpoint"], selection["model"]) if selection else studio.model
    before = len(memory.pending)
    await memory.capture(payload, ObservedModel(model, run.emit), node_id, chat)
    selected = memory.pending[before:]
    run.emit("memory.extracted", count=len(selected),
             memories=[{key: item[key] for key in ("content", "kind", "evidence")} for item in selected])
    return len(selected)


async def execution_steps(graph, agents, library, studio, value, run, chat_context=None, conversation_state=None,
                  memory=None, profiles=None, identities=None, design_session=None, designer=None):
    plan, incoming = plan_flow(graph, agents, run)
    profile_ids = graph.agent_profile_ids
    memory_sources = graph.memory_sources
    legacy_memory = graph.memoryMode == "legacy"
    exchange_payloads = {}
    state = {"run": {}, "conversation": conversation_state if conversation_state is not None else {}}
    run.node = None
    run.emit("plan.ready", order=["api-input", *[n.id for n in plan]])
    input_node = next(node for node in graph.nodes if node.id == "api-input")
    if memory and legacy_memory and input_node.data.recallMemories:
        run.node = "api-input"
        await memory.recall(chat_context, state["conversation"])
        run.emit("memory.recalled", count=len(memory.recalled), memories=memory.recalled,
                 ids=[str(item["id"]) for item in memory.recalled])
        run.node = None
    outputs = {}
    initial_context = dict(state["conversation"])
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
        recalled = memory.recalled if memory and legacy_memory else outputs.get(memory_sources.get(node.id), [])
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
        elif node.type == "recall":
            query = inputs[parents[0].id]
            if isinstance(query, dict) and isinstance(query.get("message"), str):
                query = query["message"]
            if not isinstance(query, str):
                query = json_text(query)
            output = await memory.search(query, initial_context if node.data.includeQueryState else None, node.id) if memory else []
            run.emit("memory.recalled", count=len(output), memories=output, ids=[item["id"] for item in output],
                     enabled=memory is not None)
        elif node.type == "remember":
            output = 0
            if memory:
                chat = chat_context or ChatRequest.from_wire(value)
                if node.data.memoryEvidence == "exchange":
                    payload = exchange_payloads.get(parents[0].source) or {
                        "history": [turn.model_dump() for turn in chat.history],
                        "latestTurn": {"user": chat.message, "assistant": outputs["api-response"]}}
                else:
                    payload = {"evidence": inputs[parents[0].id]}
                agent = agents.get(str(node.data.extractorAgentId), {})
                output = await extract_memories(memory, payload, agent, studio, run, node.id, chat)
            else:
                run.emit("memory.extracted", count=0, memories=[], enabled=False)
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
                        payload["recalledMemories"] = recalled
                    exchange_payloads[node.id] = payload
                result = await run_agent(library, studio, agents[str(definition.agentId)], payload, run.emit,
                                         memories=recalled, design_session=design_session)
                if memory and legacy_memory and definition.includeChatTurn and definition.captureMemories:
                    await extract_memories(memory, payload, agents[str(definition.agentId)], studio, run, node.id)
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
            profile = (profiles or {}).get(profile_ids.get(node.id))
            identity = identities.for_profile(profile) if identities and profile else None
            output = await run_agent(library, studio, definition, inputs[parents[0].id], run.emit,
                                     selection=node.data.inputSelection, chat_context=chat_context,
                                     memories=recalled,
                                     personality=profile, identity=identity, design_session=design_session,
                                     designer=designer if node.id in graph.designer_targets or
                                     FLOW_DESIGN in definition.get("toolSets", []) else None)
            if identity:
                identities.replies[profile.id] = output
        outputs[node.id] = output
        run.emit("node.completed", output=output, duration_ms=round((perf_counter()-start)*1000))
        if node.type == "apiResponse":
            yield output
    run.node = None


async def execute(*args, **kwargs):
    steps = execution_steps(*args, **kwargs)
    reply = await anext(steps)
    await anext(steps, None)
    return reply


async def run_steps(document: FlowSnapshot, library, studio, value, run, chat_context=None, turn=None, memory=None, identities=None,
                    *, design_request=None, render_response=None, designer=None):
    run.emit("run.started", input=value)
    try:
        design = DesignSession(design_request, run.emit) if design_request is not None else None
        if design and document.designer and not design_request.scope.get("lyra.system_agent_call"):
            from delegated_designer import DesignerTurn
            designer = DesignerTurn(design_request, document, False, run.emit, read_only=True)
        steps = execution_steps(document.graph, document.agent_definitions(), library, studio,
            value, run, chat_context, turn.objects if turn else None, memory, document.profiles(), identities, design, designer)
        started = perf_counter()
        timeout = RUN_TIMEOUT * 5 if designer else RUN_TIMEOUT
        result = await asyncio.wait_for(anext(steps), timeout)
        remaining = timeout - (perf_counter() - started)
        if render_response:
            response = render_response(result)
            run.publish_reply(result, turn)
            yield response
        else:
            yield result
        await asyncio.wait_for(anext(steps, None), remaining)
        if identities and chat_context:
            await identities.reflect(chat_context)
        sessions = [identities.sessions[key] for key in sorted(identities.sessions)] if identities else []
        run.finish("completed", result=result, turn=turn, memory=memory, identities=sessions, designer=designer)
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


async def perform(*args, **kwargs):
    steps = run_steps(*args, **kwargs)
    result = await anext(steps)
    await anext(steps, None)
    return result


class FlowResponse(Response):
    """Send the HTTP body before continuing the same run under the ASGI server's lifecycle."""
    def __init__(self, response, steps):
        super().__init__(response.body, status_code=response.status_code, headers=response.headers)
        self.steps = steps

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also finish if the peer disconnects during send. The run owns errors from here on.
            try:
                await anext(self.steps, None)
            except HTTPException:
                pass


async def respond(*args, render_response, **kwargs):
    steps = run_steps(*args, render_response=render_response, **kwargs)
    return FlowResponse(await anext(steps), steps)


async def run_in_background(*args, **kwargs):
    try:
        await perform(*args, **kwargs)
    except HTTPException:
        pass  # The terminal error is persisted for the Run inspector.
