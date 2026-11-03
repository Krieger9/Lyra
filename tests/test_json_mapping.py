import unittest

from json_mapping import MappingError, ObjectDefinition, assemble


def definition(base, *rules):
    return ObjectDefinition(base=base, mappings=[
        {"edgeId": edge, "source": source, "target": target} for edge, source, target in rules
    ])


class JsonMappingTests(unittest.TestCase):
    def test_literals_text_whole_values_and_escaped_paths(self):
        template = definition({"literal": "ready", "text": "", "payload": None, "flags": [None]},
                              ("message", ".text", "/text"), ("data", ".json", "/payload"),
                              ("data", ".json/a~1b/0/~0flag", "/flags/0"))
        payload = {"a/b": [{"~flag": False, "count": 0, "value": None}]}
        result = assemble(template, {"data": payload, "message": "  {literal}\n"})
        self.assertEqual(result, {"literal": "ready", "text": "  {literal}\n", "payload": payload, "flags": [False]})
        self.assertEqual(template.base["flags"], [None])

    def test_text_and_typed_object_boundaries(self):
        template = definition({"raw": "", "value": None}, ("src", ".text", "/raw"), ("src", ".json/items/1", "/value"))
        text = ' { "items": [0, null] } '
        self.assertEqual(assemble(template, {"src": text}), {"raw": text, "value": None})
        self.assertEqual(assemble(template, {"src": {"items": [0, 42]}}), {"raw": '{"items":[0,42]}', "value": 42})

    def test_missing_paths_invalid_data_and_colliding_writes(self):
        for expression, target, inputs in [
            (".text", "/value", {}), (".json", "/value", {"src": "plain text"}),
            (".json/missing", "/value", {"src": {}}), (".json/3", "/value", {"src": [1]}),
            (".text", "/absent", {"src": "x"}), (".json/a~2b", "/value", {"src": {}}),
            ("eval(1)", "/value", {"src": "x"}),
        ]:
            with self.subTest(expression=expression, target=target, inputs=inputs), self.assertRaises(MappingError):
                assemble(definition({"value": None}, ("src", expression, target)), inputs)
        for second in ["/object", "/object/field"]:
            with self.assertRaisesRegex(MappingError, "overlap"):
                assemble(definition({"object": {"field": 0}}, ("src", ".json", "/object"),
                                    ("src", ".text", second)), {"src": "1"})
