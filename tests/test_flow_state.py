import unittest
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from chat_input import ChatRequest

from flow_execution import execute, plan_flow
from flow_graph import FlowGraph
from flow_state import StateDefinition
from json_mapping import MappingError


class StateTests(unittest.TestCase):
    def test_initial_update_replace_and_invalid_write(self):
        objects = {}
        definition = StateDefinition(key="ctx", scope="run", operation="read", initial={"language": "en", "old": 1})
        self.assertEqual(definition.apply(objects, "trigger"), {"language": "en", "old": 1})
        update = definition.model_copy(update={"operation": "update"})
        previous = objects["ctx"]
        self.assertEqual(update.apply(objects, {"old": 2}), {"language": "en", "old": 2})
        self.assertEqual(previous["old"], 1)
        with self.assertRaises(MappingError):
            update.apply(objects, ["invalid"])
        replace = definition.model_copy(update={"operation": "replace"})
        self.assertEqual(replace.apply(objects, {"fresh": True}), {"fresh": True})

    def test_unordered_state_access_fails_preflight(self):
        def node(key, kind, **data):
            return {"id": key, "type": kind, "position": {"x": 0, "y": 0}, "data": {"label": key, **data}}

        state = {"scope": "conversation", "key": "context", "operation": "read"}
        graph = FlowGraph(nodes=[node("api-input", "apiInput"), node("api-response", "apiResponse"),
                                 node("read", "state", stateDefinition=state),
                                 node("write", "state", stateDefinition=state | {"operation": "update"})],
                          edges=[{"id": key, "source": "api-input", "target": key} for key in ("read", "write")])
        with self.assertRaisesRegex(HTTPException, "execution order"):
            plan_flow(graph, {})


class StateFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent_id = str(uuid4())
        self.agents = {self.agent_id: {"outputSchema": {"fields": [{"name": "summary", "type": "text"}]}}}
        self.properties = {"summary": "", "decisions": [], "count": 0}
        self.objects = {"ctx": {"summary": "earlier", "decisions": ["keep"]}}

    def graph(self, **settings):
        def node(key, kind, **data):
            return {"id": key, "type": kind, "position": {"x": 0, "y": 0}, "data": {"label": key, **data}}

        definition = {"scope": "conversation", "key": "ctx", **settings}
        return FlowGraph(stateObject=self.properties, nodes=[node("api-input", "apiInput"), node("api-response", "apiResponse"),
            node("response", "json", objectDefinition={"base": {"body": {"reply": "the full response"}}}),
            node("state", "state", stateDefinition=definition)], edges=[
            {"id": key, "name": name, "source": source, "target": target} for key, name, source, target in [
                ("trigger", "trigger", "api-input", "response"), ("in", "question", "api-input", "state"),
                ("out", "answer", "response", "state"), ("return", "result", "state", "api-response")]])

    async def run_state(self, **settings):
        return await execute(self.graph(**settings), self.agents, None, None, " original text ", Mock(),
                             conversation_state=self.objects)

    async def test_map_two_inputs_then_pass_through_or_project(self):
        mappings = [{"edgeId": "out", "source": ".value/body/reply", "target": "/summary"},
                    {"edgeId": "in", "source": ".value", "target": "/decisions"}]
        result = await self.run_state(operation="map", mappings=mappings, output="input", outputWire="in")
        self.assertEqual(result, " original text ")
        self.assertEqual(self.objects["ctx"], {"summary": "the full response", "decisions": result, "count": 0})
        self.assertEqual(await self.run_state(operation="read", output="property", outputProperty="count"), 0)
        obj = await self.run_state(operation="read", output="input", outputWire="out")
        self.assertEqual(obj, {"body": {"reply": "the full response"}})

    async def test_generate_property_or_map_validated_agent_object(self):
        with patch("flow_execution.run_agent", new=AsyncMock(return_value="generated")) as run_agent:
            reply = await self.run_state(operation="generate", agentId=self.agent_id, property="summary",
                                         output="property", outputProperty="summary")
            self.assertEqual(reply, "generated")
            payload = run_agent.await_args.args[3]
            self.assertEqual(payload["state"]["summary"], "earlier")
            self.assertEqual(payload["inputs"]["answer"]["body"]["reply"], "the full response")
            self.assertEqual(payload["inputs"]["question"], " original text ")
            run_agent.return_value = {"summary": "updated"}
            await self.run_state(operation="generateObject", agentId=self.agent_id,
                mappings=[{"edgeId": "result", "source": ".value/summary", "target": "/summary"}])
            self.assertEqual(self.objects["ctx"]["summary"], "updated")
            self.assertEqual(self.objects["ctx"]["decisions"], ["keep"])
            self.agents[self.agent_id]["outputSchema"] = None
            with self.assertRaisesRegex(MappingError, "structured"):
                await self.run_state(operation="generateObject", agentId=self.agent_id)
            self.assertEqual(run_agent.await_count, 2)

    async def test_invalid_mapping_leaves_state_intact(self):
        for source, target in [(".value/missing", "/summary"), (".value", "/undeclared")]:
            with self.assertRaises(MappingError):
                await self.run_state(operation="map", mappings=[{"edgeId": "out", "source": source, "target": target}])
            self.assertEqual(self.objects["ctx"]["summary"], "earlier")

    async def test_completed_turn_uses_selected_reply_and_previous_context_on_both_routes(self):
        snapshot = json.loads((Path(__file__).parent / "fixtures/router-snapshot.json").read_text())
        graph = snapshot["graph"]
        graph["stateObject"] = {"Context": "", "unrelated": True}
        graph["nodes"].insert(0, {"id": "remember", "type": "state", "position": {"x": 1, "y": 1},
            "data": {"stateDefinition": {"key": "memory", "scope": "conversation", "operation": "generate",
                "property": "Context", "agentId": self.agent_id, "includeChatTurn": True}}})
        graph["edges"].append({"id": "done", "name": "reply", "source": "api-response", "target": "remember"})
        agents = snapshot["agents"] | self.agents
        objects = {}
        graph = FlowGraph.model_validate(graph)
        for index, category in enumerate(["chat", "task"]):
            chat = ChatRequest(message=f"message {index}", history=[{"role": "assistant", "content": "older"}])
            reply = {"category": category, "reason": "example"}
            with patch("flow_execution.run_agent", new=AsyncMock(side_effect=[reply, f"context {index}"])) as model:
                result = await execute(graph, agents, None, None, chat.message, Mock(), chat, objects)
                self.assertEqual(result, reply)
                payload = model.await_args.args[3]
                self.assertEqual(payload["state"], {"Context": "context 0" if index else "", "unrelated": True})
                self.assertEqual(payload["history"], [{"role": "assistant", "content": "older"}])
                self.assertEqual(payload["latestTurn"], {"user": chat.message, "assistant": reply})
                self.assertEqual(objects["memory"], {"Context": f"context {index}", "unrelated": True})

    async def test_flow_memory_controls_disable_recall_and_capture(self):
        graph = self.graph(operation="generate", agentId=self.agent_id, property="summary")
        graph.nodes[0].data.recallMemories = False
        state = next(node for node in graph.nodes if node.type == "state")
        state.data.stateDefinition.includeChatTurn = True
        state.data.stateDefinition.captureMemories = False
        graph.edges = [edge.model_copy(update={"source": "api-response"}) if edge.target == "state" else edge
                       for edge in graph.edges if edge.source != "state"]
        from flow_graph import FlowEdge
        graph.edges.append(FlowEdge(id="reply", source="response", target="api-response"))
        memory = Mock(recalled=[], pending=[], recall=AsyncMock(), capture=AsyncMock())
        with patch("flow_execution.run_agent", new=AsyncMock(return_value="summary")):
            await execute(graph, self.agents, None, None, "hello", Mock(), ChatRequest(message="hello"), {}, memory)
        memory.recall.assert_not_awaited()
        memory.capture.assert_not_awaited()
