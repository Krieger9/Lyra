import unittest
import json

from pydantic import ValidationError

from output_contract import OutputSchema
from json_mapping import ObjectDefinition, assemble


class OutputSchemaTests(unittest.TestCase):
    def test_object_contract(self):
        schema = OutputSchema(fields=[{"name": "explanation", "type": "text"},
                                      {"name": "tag", "type": "choice", "choices": ["a", "b"], "required": False}])
        self.assertEqual(schema.parse_output('{"explanation":"hello"}'), {"explanation": "hello"})
        for value in ['{}', '[]', '{"explanation":42}', '{"explanation":"ok","tag":"c"}',
                      '{"explanation":"ok","unknown":null}', 'prose', '```json\n{}\n```']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                schema.parse_output(value)

    def test_invalid_schema(self):
        text = {"name": "foo", "type": "text"}
        for fields in [[], [text, text], [text | {"type": "choice"}], [text | {"choices": ["a"]}],
                       [text | {"type": "choice", "choices": ["a", " a "]}], [text | {"name": ""}]]:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                OutputSchema(fields=fields)

    def test_history_shape_and_wire_selections(self):
        turn = {"type": "object", "fields": [
            {"name": "role", "type": "choice", "choices": ["user", "assistant"]},
            {"name": "content", "type": "text"},
        ]}
        schema = OutputSchema(fields=[{"name": "history", "type": "array", "items": turn},
                                      {"name": "latest", **turn, "required": False}])
        first = {"role": "user", "content": "Hello"}
        second = {"role": "assistant", "content": "Hi!"}
        data = {"history": [first, second]}
        self.assertEqual(schema.parse_output(json.dumps(data)), data)
        self.assertEqual(schema.validate_value({"history": []}), {"history": []})
        self.assertEqual(schema.text_paths(), {"/latest/role": ["user", "assistant"], "/latest/content": []})
        items = schema.json_schema()["properties"]["history"]["items"]
        self.assertEqual(items["properties"]["role"]["enum"], ["user", "assistant"])
        self.assertFalse(items["additionalProperties"])
        self.assertEqual(items["required"], ["role", "content"])
        for path, expected in [("", data), ("/history", [first, second]), ("/history/0", first),
                               ("/history/1/content", "Hi!")]:
            mapping = ObjectDefinition(base={"selection": None}, mappings=[
                {"edgeId": "result", "source": ".value" + path, "target": "/selection"}])
            self.assertEqual(assemble(mapping, {"result": data}), {"selection": expected})
        for invalid, path in [([first, second | {"role": "system"}], "history[1].role"),
                              ([{"role": "user"}], "history[0].content"),
                              ([first | {"extra": True}], "history[0]"),
                              ([None], "history[0]"), ("[]", "history")]:
            evidence = []
            with self.assertRaises(ValueError) as failure:
                schema.parse_output(json.dumps({"history": invalid}), lambda _, **event: evidence.append(event))
            self.assertIn(path, str(failure.exception))
            self.assertEqual(evidence[-1]["field"], path)
            self.assertFalse(evidence[-1]["passed"])

    def test_invalid_nested_definitions(self):
        text = {"name": "content", "type": "text"}
        for shape in [{"type": "array"}, {"type": "object"}, {"type": "object", "fields": [text, text]},
                      {"type": "text", "items": {"type": "text"}}, {"type": "array", "items": text},
                      {"type": "array", "items": {"type": "choice"}}]:
            with self.subTest(shape=shape), self.assertRaises(ValidationError):
                OutputSchema(fields=[{"name": "history", **shape}])
