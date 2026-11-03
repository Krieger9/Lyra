import unittest

from fastapi import HTTPException

from flow_execution import plan_flow
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
