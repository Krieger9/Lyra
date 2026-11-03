import unittest

from pydantic import ValidationError

from output_contract import OutputSchema


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
