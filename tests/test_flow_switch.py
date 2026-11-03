import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from flow_execution import execute
from flow_graph import FlowGraph
from flow_switch import SwitchDefinition
from json_mapping import MappingError


def node(key, kind, **data):
    return {"id": key, "type": kind, "position": {"x": 0, "y": 0}, "data": {"label": key, **data}}


def edge(key, source, target, port=None):
    return {"id": key, "source": source, "target": target, "sourceHandle": port}


class Recorder:
    def __init__(self):
        self.events = []
        self.node = None

    def emit(self, kind, **data):
        self.events.append((self.node, kind, data))


class SwitchTests(unittest.TestCase):
    def test_order_presence_choice_and_otherwise(self):
        paths = SwitchDefinition(paths=[
            {"id": "incomplete", "label": "Incomplete", "match": "any", "checks": [
                {"field": "/Text", "operator": "empty"}, {"field": "/reason", "operator": "empty"}]},
            {"id": "yes", "label": "Intelligence", "checks": [
                {"field": "/Text", "operator": "notEmpty"},
                {"field": "/category", "operator": "equals", "value": "intelligence"}]},
            {"id": "other", "label": "Other category", "checks": [
                {"field": "/category", "operator": "notEquals", "value": "intelligence"}]}])
        paths.validate_fields({"/Text": [], "/reason": [], "/category": ["intelligence", "conversation"]})
        complete = {"Text": "hi", "reason": "classified", "category": "intelligence"}
        for change, expected in [({}, "yes"), ({"reason": "  \n"}, "incomplete"), ({"Text": None}, "incomplete"),
                                 ({"category": "conversation"}, "other"), ({"category": None}, "otherwise")]:
            self.assertEqual(paths.select(complete | change)[0], expected)
        del complete["category"]
        self.assertEqual(paths.select(complete)[0], "otherwise")
        with self.assertRaisesRegex(MappingError, "expected text"):
            paths.select(complete | {"Text": 0})

    def test_run_and_preflight_through_mapped_classifier(self):
        import asyncio
        agent_id = str(uuid4())
        agent = {"id": agent_id}
        agents = {agent_id: {"outputSchema": {"fields": [
            {"name": "category", "choices": ["conversation", "intelligence"]}, {"name": "reason", "choices": []}]}}}
        routing = {"paths": [{"id": "special", "label": "Special", "checks": [
            {"field": "/Text", "operator": "notEmpty"}, {"field": "/result/category", "operator": "equals",
                                                         "value": "intelligence"}]}]}
        graph = {"nodes": [
            node("api-input", "apiInput"), node("api-response", "apiResponse"),
            node("classifier", "agent", definition=agent),
            node("payload", "json", objectDefinition={"base": {"Text": "", "result": None}, "mappings": [
                {"edgeId": "raw", "source": ".value", "target": "/Text"},
                {"edgeId": "classified", "source": ".value", "target": "/result"}]}),
            node("route", "switch", switchDefinition=routing), node("work", "agent", definition=agent),
        ], "edges": [edge("input", "api-input", "classifier"), edge("raw", "api-input", "payload"),
                     edge("classified", "classifier", "payload"), edge("ready", "payload", "route"),
                     edge("run", "route", "work", "special"), edge("done", "work", "api-response"),
                     edge("fallback", "route", "api-response", "otherwise")]}
        async def run(category, count):
            record = Recorder()
            classification = {"category": category, "reason": "sample"}
            with patch("flow_execution.run_agent", new=AsyncMock(side_effect=[classification, "worked"])) as model:
                try:
                    output = await execute(FlowGraph.model_validate(graph), agents, None, None, "hi", record)
                finally:
                    self.assertEqual(model.await_count, count)
            return output, record.events

        result, events = asyncio.run(run("conversation", 1))
        self.assertEqual(result, {"Text": "hi", "result": {"category": "conversation", "reason": "sample"}})
        self.assertIn(("work", "node.skipped"), [(n, k) for n, k, _ in events])
        selected = next(data for _, kind, data in events if kind == "switch.selected")
        self.assertEqual(selected["wires"], ["fallback"])
        self.assertEqual(asyncio.run(run("intelligence", 2))[0], "worked")
        graph["edges"].append(edge("ambiguous", "payload", "api-response"))
        with self.assertRaisesRegex(ValidationError, "mutually exclusive"):
            asyncio.run(run("conversation", 0))
        graph["edges"].pop()
        graph["edges"][-1]["sourceHandle"] = "deleted"
        with self.assertRaisesRegex(ValidationError, "existing Switch path"):
            FlowGraph.model_validate(graph)
        graph["edges"].pop()
        with self.assertRaisesRegex(HTTPException, "one active input"):
            asyncio.run(run("conversation", 1))
        agents[agent_id]["outputSchema"]["fields"][0]["choices"].remove("intelligence")
        with self.assertRaisesRegex(MappingError, "no longer an available choice"):
            asyncio.run(run("conversation", 0))
