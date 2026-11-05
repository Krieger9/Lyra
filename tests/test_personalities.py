import unittest
from uuid import uuid4
from pydantic import ValidationError

from flow_graph import FlowGraph
from personalities import PersonalityWrite, SavedPersonality, render_personality


class PersonalityTemplateTests(unittest.TestCase):
    def test_flow_personality_selection_in_new_and_legacy_graphs(self):
        profile_id = uuid4()
        position = {"x": 0, "y": 0}
        start = {"type": "apiInput", "id": "api-input", "position": position, "data": {}}
        response = {"type": "apiResponse", "id": "api-response", "position": position, "data": {}}
        card = {"type": "personality", "id": "profile", "position": position, "data": {"personalityId": profile_id}}
        legacy = start | {"data": card["data"]}
        self.assertEqual(FlowGraph(nodes=[start, card, response]).personality_id, profile_id)
        self.assertEqual(FlowGraph(nodes=[legacy, response]).personality_id, profile_id)
        self.assertIsNone(FlowGraph(nodes=[start, card | {"data": {}}, response]).personality_id)
        with self.assertRaises(ValidationError):
            FlowGraph(nodes=[start, card, card | {"id": "second"}, response])
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
