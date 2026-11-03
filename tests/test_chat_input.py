import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from pydantic import ValidationError

from app import app
from auth import require_chat_access
from chat_input import ChatRequest, InputSelection
from flow_graph import FlowSnapshot
from json_mapping import MappingError


class ChatInputTests(unittest.TestCase):
    def test_original_chat_uses_only_the_supplied_run_context(self):
        routed = {"request": {"text": "Hello Lyra", "category": "conversational"}}
        turns = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]
        chat = ChatRequest(message="Hello Lyra", history=turns)
        for options, expected in [({"mode": "all"}, turns), ({"mode": "recent", "turns": 1}, turns[-1:]),
                                  ({"mode": "message"}, [])]:
            selection = InputSelection(source="chat", **options)
            self.assertEqual(selection.select(routed, chat), ("Hello Lyra", expected))
            with self.assertRaisesRegex(MappingError, "unavailable"):
                selection.select(routed)
        with self.assertRaisesRegex(MappingError, "Expected chat input"):
            InputSelection(mode="all").select(routed, chat)

    def test_explicit_projection_of_wire_values(self):
        turns = [{"role": "user", "content": "First"}, {"role": "assistant", "content": "Second"}]
        value = {"message": "Current", "history": turns}
        for options, expected in [({"mode": "message"}, []), ({"mode": "all"}, turns),
                                  ({"mode": "recent", "turns": 1}, turns[-1:]),
                                  ({"mode": "recent", "turns": 8}, turns)]:
            selection = InputSelection(**options)
            self.assertEqual(selection.select(value), ("Current", expected))
            self.assertEqual(selection.select("plain string"), ("plain string", []))
        for invalid in [[], {"history": turns}, value | {"history": [{"role": "system", "content": "bad"}]}]:
            with self.assertRaises(MappingError):
                selection.select(invalid)
        for options in [{"mode": "recent"}, {"mode": "recent", "turns": 0}, {"mode": "recent", "turns": 13},
                        {"mode": "all", "turns": 1}, {"mode": "unknown"}]:
            with self.assertRaises(ValidationError):
                InputSelection(**options)


class ActivatedChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_activated_chat_uses_snapshot_input_and_node_selection(self):
        fixture = Path(__file__).with_name("fixtures") / "router-snapshot.json"
        saved = json.loads(fixture.read_text(encoding="utf-8"))
        library = Mock()
        library.render.side_effect = lambda name, params, version: (
            "Classify the message." if name == "instructions" else "Request: " + params["request"])
        model = SimpleNamespace(complete=AsyncMock(return_value='{"category":"chat","reason":"ok"}'))
        studio = SimpleNamespace(model=model, traces=Mock())
        turns = [{"role": "user", "content": "prior question"}, {"role": "assistant", "content": "prior answer"}]
        body = {"message": "continue", "history": turns}
        with patch.dict(app.dependency_overrides, {require_chat_access: lambda: ["User.Chat"]}), \
                patch.object(app.state, "library", library, create=True), patch.object(app.state, "studio", studio, create=True), \
                patch("app.Run"), patch("app.activation") as activation:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                activation.return_value = FlowSnapshot.model_validate(saved)
                agent = next(n for n in saved["graph"]["nodes"] if n["type"] == "agent")
                response = await client.post("/api/chat", json=body)
                self.assertEqual(response.status_code, 200, response.text)
                legacy = model.complete.await_args.args[0]
                self.assertIsInstance(legacy, str)
                self.assertNotIn("prior question", legacy)

                next(n for n in saved["graph"]["nodes"] if n["type"] == "apiInput")["data"]["chatInput"] = True
                for options, expected in [({"mode": "recent", "turns": 1}, turns[-1:]),
                                          ({"mode": "all"}, turns), ({"mode": "message"}, [])]:
                    agent["data"]["inputSelection"] = options
                    activation.return_value = FlowSnapshot.model_validate(saved)
                    response = await client.post("/api/chat", json=body)
                    self.assertEqual(response.status_code, 200, response.text)
                    messages = model.complete.await_args.args[0]
                    self.assertEqual(messages[0]["role"], "system")
                    self.assertTrue(messages[0]["content"].startswith("Classify the message."))
                    self.assertIn("Return only a JSON object", messages[0]["content"])
                    self.assertEqual(messages[1:], [*expected, {"role": "user", "content": "Request: continue"}])
                    self.assertEqual(studio.traces.save.call_args.args[0]["llm_input"], messages)

                # Route the classifier object to another agent with the API input still set to text.
                next(n for n in saved["graph"]["nodes"] if n["type"] == "apiInput")["data"]["chatInput"] = False
                agent["data"].pop("inputSelection")
                saved["graph"]["nodes"].append(agent | {"id": "answer", "data": agent["data"] | {
                    "inputSelection": {"source": "chat", "mode": "all"}}})
                edges = saved["graph"]["edges"]
                next(e for e in edges if e["id"] == "chat-id")["target"] = "answer"
                edges.append({"id": "answer-out", "source": "answer", "target": "api-response"})
                activation.return_value = FlowSnapshot.model_validate(saved)
                response = await client.post("/api/chat", json=body)
                self.assertEqual(response.status_code, 200, response.text)
                messages = model.complete.await_args.args[0]
                self.assertEqual(messages[1:], [*turns, {"role": "user", "content": "Request: continue"}])
                params = library.render.call_args.args[1]
                self.assertEqual(params["wire"], {"category": "chat", "reason": "ok"})
