import unittest
from uuid import uuid4
from unittest.mock import AsyncMock, Mock, patch
from pydantic import ValidationError

from flow_graph import FlowGraph, FlowSnapshot, snapshot
from flow_execution import execute
from identity import TurnIdentities
from personalities import PersonalityWrite, SavedPersonality, render_personality


class PersonalityTemplateTests(unittest.TestCase):
    def test_flow_personality_selection_in_new_and_legacy_graphs(self):
        profile_id = uuid4()
        position = {"x": 0, "y": 0}
        start = {"type": "apiInput", "id": "api-input", "position": position, "data": {}}
        response = {"type": "apiResponse", "id": "api-response", "position": position, "data": {}}
        card = {"type": "personality", "id": "profile", "position": position, "data": {"personalityId": profile_id}}
        legacy = start | {"data": card["data"]}
        agent = {"type": "agent", "id": "writer", "position": position, "data": {}}
        neutral = agent | {"id": "neutral", "data": {"usePersonality": False}}
        wire = {"id": "reference", "source": "profile", "target": "writer", "targetHandle": "personality"}
        nodes = [start, card, agent, neutral, response]
        self.assertEqual(FlowGraph(nodes=nodes).agent_profile_ids, {})
        self.assertEqual(FlowGraph(nodes=nodes, edges=[wire]).agent_profile_ids, {"writer": profile_id})
        for old in [nodes, [legacy, agent, neutral, response]]:
            self.assertEqual(FlowGraph.from_saved({"nodes": old}).agent_profile_ids, {"writer": profile_id})
        with self.assertRaises(ValidationError):
            FlowGraph(nodes=[*nodes, card | {"id": "second"}], edges=[wire, wire | {"id": "w2", "source": "second"}])
        with self.assertRaises(ValidationError):
            FlowGraph(nodes=[legacy, card, response])
        with self.assertRaises(ValidationError):
            FlowGraph(nodes=[start, card, response], edges=[{"id": "wire", "source": "api-input", "target": "profile"}])

    def test_literal_substitution_and_required_sections(self):
        body = PersonalityWrite(name="Lyra", template="You are {{ name }}.", goals=["Be curious"],
                                memories=[{"content": "A literal {{name}} in a memory"}])
        rendered = render_personality(body)
        self.assertIn("You are Lyra.", rendered)
        self.assertIn("Be curious", rendered)
        self.assertIn("A literal {{name}} in a memory", rendered)
        explicit = body.model_copy(update={"template": "{{name}}\n{{goals}}\n{{memories}}"})
        self.assertEqual(render_personality(explicit).count("Personality goals:"), 1)

    def test_template_code_unknown_variables_and_duplicate_memories_are_rejected(self):
        for template in ("{{request}}", "{{name.__class__}}", "{% for x in goals %}", "{{broken"):
            with self.assertRaises(ValidationError):
                PersonalityWrite(name="Lyra", template=template)
        with self.assertRaises(ValidationError):
            PersonalityWrite(name="Lyra", template="Hi", memories=[{"content": "Tea"}, {"content": " tea "}])
        profile = SavedPersonality(id=uuid4(), revision=1, name="Lyra", template="Hello")
        self.assertIn("None assigned", render_personality(profile))


class WiredPersonalityTests(unittest.IsolatedAsyncioTestCase):
    async def test_profiles_follow_agent_wires_and_do_not_replace_the_data_input(self):
        def node(key, kind, **data):
            return {"id": key, "type": kind, "position": {"x": 0, "y": 0}, "data": data}
        agent_id = uuid4()
        profiles = [SavedPersonality(id=uuid4(), name=name, revision=1, template=name, continuityEnabled=True)
                    for name in ["Lyra", "Reviewer"]]
        nodes = [node("api-input", "apiInput"), node("api-response", "apiResponse")]
        path = ["api-input", "neutral", "writer", "review", "revise", "api-response"]
        nodes.extend(node(key, "agent", definition={"id": agent_id}) for key in path[1:-1])
        edges = [{"id": target, "source": source, "target": target} for source, target in zip(path, path[1:])]
        for target, profile in zip(["writer", "review", "revise"], [*profiles, profiles[0]]):
            key = target + "-profile"
            nodes.append(node(key, "personality", personalityId=profile.id))
            edges.append({"id": key, "source": key, "target": target, "targetHandle": "personality"})
        nodes.append(node("unused-card", "personality", personalityId=uuid4()))
        graph = FlowGraph(nodes=nodes, edges=edges)
        row = {"id": uuid4(), "name": "Writer and reviewer", "revision": 1, "graph": graph.model_dump(mode="json")}
        by_id = {p.id: p for p in profiles}
        with patch("flow_graph.load_flow", return_value=row), patch("flow_graph.graph_agents", return_value={}), \
             patch("flow_graph.load_personality", side_effect=lambda _, key: by_id[key]) as load:
            pinned = snapshot(None, row["id"])
        self.assertEqual(load.call_count, 2)
        reopened = FlowSnapshot.model_validate_json(pinned.model_dump_json(exclude_unset=True))
        self.assertEqual(reopened.graph.agent_profile_ids, graph.agent_profile_ids)
        call = AsyncMock(side_effect=["neutral data", "draft", "comments", "final"])
        identities = TurnIdentities(None, "owner", {}, None)
        writer, reviewer = AsyncMock(), AsyncMock()
        with patch("flow_execution.run_agent", call), patch("identity.IdentitySession", side_effect=[writer, reviewer]) as opened:
            result = await execute(reopened.graph, {str(agent_id): {}}, None, None, "request", Mock(),
                                   profiles=reopened.profiles(), identities=identities)
        self.assertEqual(opened.call_count, 2)
        self.assertEqual([args.kwargs["identity"] for args in call.await_args_list], [None, writer, reviewer, writer])
        chat = Mock()
        await identities.reflect(chat)
        reviewer.reflect.assert_awaited_once_with(chat, "comments")
        writer.reflect.assert_awaited_once_with(chat, "final")
        self.assertEqual(result, "final")
        self.assertEqual([args.args[3] for args in call.await_args_list], ["request", "neutral data", "draft", "comments"])
        self.assertEqual([args.kwargs["personality"] for args in call.await_args_list], [None, *profiles, profiles[0]])
