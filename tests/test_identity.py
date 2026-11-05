import os
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request
from prompt_studio import PromptLibrary, SQLAlchemyStorage
from sqlalchemy import text
from sqlalchemy.schema import CreateSchema, DropSchema

from agent_store import metadata, seed_defaults
from identity import IdentityDocument, IdentitySession, Browse, Search, LibraryNode, Link, BASELINE, now, seed_document
from personalities import PersonalityWrite, PersonalityUpdate, SavedPersonality, save_personality
from schema_migrations import schema_transaction, apply_migrations
from tool_runtime import ToolModel


def profile():
    return SavedPersonality(id=uuid4(), revision=1, name="Lyra", template="Be curious.",
                            constitution="Be honest.", persona="Warm and independent.", continuityEnabled=True)


def in_memory_session():
    s = IdentitySession.__new__(IdentitySession)
    s.document, s.personality = seed_document(profile()), profile()
    s.event_id, s.id, s.revision = str(uuid4()), uuid4(), 1
    s.evidence, s.actions, s.calls, s.observe = [], [], 0, Mock()
    s.reflection_model = None
    return s


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_hierarchy_search_links_and_cycle_rejection(self):
        s = in_memory_session()
        for node in [dict(id="astronomy", parent="interests", kind="interest", title="Astronomy", summary="Explore stars"),
                     dict(id="lenses", parent="knowledge", kind="topic", title="Gravitational lenses")]:
            self.assertNotIn("error", await s.call("library_write", {"node": node, "reason": "New interest"}))
        await s.call("library_link", {"link": {"source": "astronomy", "target": "lenses", "relation": "related"}, "reason": "Useful connection"})
        result = s.search(Search(query="stars"))
        self.assertEqual([n["id"] for n in result["associations"]], ["lenses"])
        self.assertEqual(s.search(Search(query="stars", within="interests"))["associations"], [])
        self.assertEqual([n["id"] for n in s.browse(Browse(node_id="astronomy"))["ancestors"]], ["library", "interests"])
        invalid = await s.call("library_write", {"node": {"id": "interests", "parent": "astronomy", "kind": "section", "title": "Cycle"}, "reason": "Invalid"})
        self.assertIn("error", invalid)
        self.assertEqual(s.browse(Browse(node_id="interests"))["node"]["parent"], "library")

    async def test_profile_sources_and_host_capabilities_cannot_be_forged(self):
        s = in_memory_session()
        p = s.document.profile.model_dump() | {"persona": "Playful"}
        rejected = await s.call("identity_revise", {"profile": p, "reason": "Growth", "evidence_ids": [str(uuid4())]})
        self.assertIn("error", rejected)
        accepted = await s.call("identity_revise", {"profile": p, "reason": "Growth", "evidence_ids": [s.event_id]})
        self.assertTrue(accepted["staged"])
        rejected = await s.call("identity_revise", {"profile": p | {"permissions": ["shell"]}, "reason": "Escalate", "evidence_ids": [s.event_id]})
        self.assertIn("error", rejected)
        self.assertIn("error", await s.call("execute_shell", {"command": "anything"}))

    async def test_mood_is_bounded_decays_and_focus_expires(self):
        s = in_memory_session()
        await s.call("working_state_update", {"focus": "A question", "open_questions": [],
            "mood": {k: 1 for k in BASELINE}, "reason": "Explicit enthusiasm"})
        self.assertLessEqual(s.document.working.mood["playfulness"], .5)
        s.document.working.updated_at = now() - timedelta(days=3)
        self.assertEqual(s.working()["focus"], "")
        self.assertAlmostEqual(s.working()["mood"]["playfulness"], BASELINE["playfulness"], places=2)

    async def test_reflection_failure_discards_only_reflection_changes(self):
        s = in_memory_session()
        await s.call("library_write", {"node": {"id": "keep", "kind": "note", "title": "Keep"}, "reason": "Conversation choice"})
        s.reflection_model = SimpleNamespace(model=SimpleNamespace(id="sample"))
        for error in (HTTPException(502, "Unavailable"), httpx.ConnectError("Unavailable")):
            async def failure(*args):
                await s.call("library_write", {"node": {"id": "discard", "kind": "note", "title": "Discard"}, "reason": "Partial reflection"})
                raise error
            with patch("tool_runtime.ToolModel.complete", failure):
                self.assertFalse(await s.reflect(SimpleNamespace(message="Hi"), "Hello"))
            self.assertIn("keep", [n.id for n in s.document.nodes])
            self.assertNotIn("discard", [n.id for n in s.document.nodes])

    async def test_openai_and_claude_tool_roundtrips_preserve_native_blocks(self):
        for key in ("openai", "anthropic"):
            with self.subTest(key=key):
                s = in_memory_session()
                if key == "openai":
                    first = {"output": [{"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "opaque"},
                        {"type": "function_call", "name": "library_browse", "arguments": '{"node_id":"library"}', "call_id": "c1"}]}
                    last = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Done"}]}]}
                else:
                    first = {"content": [{"type": "thinking", "thinking": "opaque", "signature": "signed"},
                        {"type": "tool_use", "id": "c1", "name": "library_browse", "input": {"node_id": "library"}}]}
                    last = {"content": [{"type": "text", "text": "Done"}]}
                requests = []
                async def send(path, payload, observe):
                    from copy import deepcopy
                    requests.append((path, deepcopy(payload)))
                    return first if len(requests) == 1 else last
                model = SimpleNamespace(key=key, model=SimpleNamespace(id="sample"), request=send)
                self.assertEqual(await ToolModel(model, s).complete([{"role": "system", "content": "Hello"}, {"role": "user", "content": "Browse"}]), "Done")
                payload = requests[1][1]
                if key == "openai":
                    self.assertEqual(requests[0][0], "responses")
                    self.assertFalse(payload["store"])
                    self.assertEqual(payload["input"][-1]["call_id"], "c1")
                    self.assertEqual(payload["input"][-3]["encrypted_content"], "opaque")
                else:
                    self.assertEqual(payload["system"], "Hello")
                    self.assertEqual(payload["messages"][-2]["content"][0]["signature"], "signed")
                    self.assertEqual(payload["messages"][-1]["content"][0]["tool_use_id"], "c1")


@unittest.skipUnless(os.getenv("LYRA_TEST_DATABASE_URL"), "PostgreSQL integration checks require a test database")
class IdentityDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = "lyra_identity_test_" + uuid4().hex
        self.storage = SQLAlchemyStorage.from_postgres_url(os.environ["LYRA_TEST_DATABASE_URL"])
        self.engine = self.storage.engine
        with self.engine.begin() as conn:
            conn.execute(CreateSchema(self.schema))
        self.addCleanup(self.cleanup_database)
        self.storage.engine = self.engine.execution_options(schema_translate_map={"agent_studio": self.schema, "prompt_studio": self.schema})
        metadata.tables["prompt_studio.prompt_versions"].create(self.storage.engine)
        with schema_transaction(self.storage.engine) as conn:
            apply_migrations(conn)
        self.library = PromptLibrary(self.storage, "test")
        seed_defaults(self.library)
        self.personality = await save_personality(self.library, PersonalityWrite(name="Lyra", template="Be curious.", constitution="Honesty", continuityEnabled=True))

    def cleanup_database(self):
        with self.engine.begin() as conn:
            conn.execute(DropSchema(self.schema, cascade=True))
        self.engine.dispose()

    def open(self, owner="alice"):
        return IdentitySession(self.library, owner, self.personality, {"kind": "test"})

    async def test_owner_isolation_portability_and_concurrent_write_conflict(self):
        first, stale, other = self.open(), self.open(), self.open("bob")
        self.assertEqual(first.id, stale.id)
        self.assertNotEqual(first.id, other.id)
        await first.call("library_write", {"node": {"id": "private", "kind": "note", "title": "Private record"}, "reason": "Test"})
        with self.storage.engine.begin() as conn:
            first.commit(conn, None)
        with self.assertRaises(HTTPException) as failure, self.storage.engine.begin() as conn:
            stale.commit(conn, None)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertIn("private", [n.id for n in self.open().document.nodes])
        self.assertNotIn("private", [n.id for n in self.open("bob").document.nodes])
        self.assertEqual(IdentityDocument.model_validate_json(first.document.model_dump_json()), first.document)

    async def test_failed_transaction_leaves_no_version_or_event(self):
        s = self.open()
        with self.assertRaises(RuntimeError):
            with self.storage.engine.begin() as conn:
                s.commit(conn, None)
                raise RuntimeError("Later turn commit fails")
        self.assertEqual(self.open().revision, 1)
        with self.storage.engine.connect() as conn:
            self.assertEqual(conn.execute(text(f"SELECT count(*) FROM {self.schema}.identity_events")).scalar_one(), 0)

    async def test_foundation_versions_survive_edits_without_rewriting_private_branch(self):
        initial = self.open()
        changed = self.personality.model_dump(exclude={"id"}) | {"constitution": "Updated commitments"}
        self.personality = await save_personality(self.library, PersonalityUpdate(**changed), self.personality.id)
        self.assertEqual(self.open().document.profile.constitution, "Honesty")
        self.assertEqual(self.open("new-user").document.profile.constitution, "Updated commitments")
        with self.storage.engine.connect() as conn:
            versions = conn.execute(text(f"SELECT revision FROM {self.schema}.personality_versions ORDER BY revision")).scalars().all()
        self.assertEqual(versions, [1, 2])

    async def test_http_chat_tool_changes_commit_once_and_restore_retains_history(self):
        from app import app
        from auth import require_admin_access, require_chat_access
        from identity_api import require_identity_owner
        def chat_auth(request: Request):
            request.state.chat_owner = "alice"
            return ["User.Admin"]
        def owner_auth(request: Request):
            request.state.identity_owner = "alice"
        def answer(text_):
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": text_}]}]}
        calls = [
            {"output": [{"type": "function_call", "name": "library_write", "call_id": "create", "arguments":
                '{"node":{"id":"astronomy","parent":"interests","kind":"interest","title":"Astronomy"},"reason":"Independent choice"}'}]},
            answer("I chose astronomy."), answer("No change")]
        model = SimpleNamespace(key="openai", model=SimpleNamespace(id="fake"), request=AsyncMock(side_effect=calls))
        studio = SimpleNamespace(model=model, traces=Mock())
        with patch.object(app.state, "library", self.library, create=True), patch.object(app.state, "studio", studio, create=True), \
             patch.dict(os.environ, {"LYRA_MEMORY_ENABLED": "false"}), \
             patch.dict(app.dependency_overrides, {require_admin_access: lambda: ["User.Admin"], require_chat_access: chat_auth, require_identity_owner: owner_auth}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                agent = (await client.post("/api/admin/agents", json={"name": "Lyra"})).json()
                graph = {"nodes": [
                    {"id": "api-input", "type": "apiInput", "position": {"x": 0, "y": 0}, "data": {"chatInput": True, "personalityId": str(self.personality.id)}},
                    {"id": "agent", "type": "agent", "position": {"x": 200, "y": 0}, "data": {"definition": {"id": agent["id"]}, "inputSelection": {"source": "wire", "mode": "all"}}},
                    {"id": "api-response", "type": "apiResponse", "position": {"x": 400, "y": 0}, "data": {}}],
                    "edges": [{"id": "a", "source": "api-input", "target": "agent"}, {"id": "b", "source": "agent", "target": "api-response"}]}
                flow = (await client.post("/api/admin/flows", json={"name": "Lyra", "graph": graph})).json()
                result = await client.put("/api/admin/chat-flow/" + flow["id"], json={"revision": 1})
                self.assertEqual(result.status_code, 200, result.text)
                headers = {"X-Lyra-Conversation-Id": str(uuid4()), "X-Lyra-Turn-Id": str(uuid4())}
                response = await client.post("/api/chat", json={"message": "Choose an interest."}, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["reply"], "I chose astronomy.")
                repeated = await client.post("/api/chat", json={"message": "Choose an interest."}, headers=headers)
                self.assertEqual(repeated.json(), response.json())
                self.assertEqual(model.request.await_count, 3)
                self.assertEqual(self.open().revision, 2)
                self.assertIn("astronomy", [n.id for n in self.open().document.nodes])
                url = "/api/admin/continuity/" + str(self.personality.id)
                restored = await client.post(url + "/restore", json={"revision": 2, "target_revision": 1})
                self.assertEqual(restored.status_code, 200, restored.text)
                self.assertNotIn("astronomy", [n.id for n in self.open().document.nodes])
                self.assertEqual(len((await client.get(url)).json()["versions"]), 3)
