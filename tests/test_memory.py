import json
import math
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx
from fastapi import HTTPException
from prompt_studio import PromptLibrary, SQLAlchemyStorage
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from agent_output import AgentPrompts
from agent_store import metadata
from chat_input import ChatRequest
from flow_graph import FlowGraph
from flow_execution import execute
from memory import MemorySession, embed
from memory_store import (EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, save_memories, search_memories,
                          memory_detail, list_memories, memories_for_run, record_recalls)
from run_store import runs, events, utcnow
from conversation_state import checkpoints
from schema_migrations import apply_migrations, schema_transaction


def vector(axis=0):
    result = [0.0] * EMBEDDING_DIMENSIONS
    result[axis] = 1.0
    return result


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_recall_and_remember_timing(self):
        agent_id = str(uuid4())
        def node(key, kind, **data):
            return {"id": key, "type": kind, "position": {"x": 0, "y": 0}, "data": data}
        nodes = [node("api-input", "apiInput"), node("api-response", "apiResponse"),
                 node("lookup", "recall"), node("save", "remember"),
                 node("route", "switch", switchDefinition={"paths": [{"id": "active", "label": "Has text",
                      "checks": [{"field": "", "operator": "notEmpty"}]}]})]
        nodes.extend(node(key, "agent", definition={"id": agent_id}) for key in ("neutral", "writer"))
        edges = [{"id": str(i), "source": s, "target": t} for i, (s,t) in enumerate([
            ("api-input", "route"), ("api-input", "neutral"), ("neutral", "writer"), ("writer", "api-response")])]
        edges += [{"id": "query", "source": "route", "sourceHandle": "active", "target": "lookup"},
                  {"id": "facts", "source": "lookup", "target": "writer", "targetHandle": "memories"},
                  {"id": "evidence", "source": "route", "sourceHandle": "active", "target": "save"}]
        graph = FlowGraph(nodes=nodes, edges=edges)
        for text, timing in [("I prefer tea", "afterResponse"), ("I prefer tea", "onEvidence"), ("", "onEvidence")]:
            graph.nodes[3].data.rememberWhen = timing
            record = Mock()
            completed = []
            record.emit.side_effect = lambda kind, **data: completed.append(record.node) if kind == "node.completed" else None
            facts = [{"id": "tea", "content": "Likes tea"}]
            memory = Mock(search=AsyncMock(return_value=facts), capture=AsyncMock(), pending=[])
            model = AsyncMock(side_effect=["neutral data", "reply"])
            with patch("flow_execution.run_agent", model):
                reply = await execute(graph, {agent_id: {}}, None, Mock(), text, record, ChatRequest(message="I prefer tea"), memory=memory)
            self.assertEqual(reply, "reply")
            self.assertEqual(model.await_args_list[0].kwargs["memories"], [])
            self.assertEqual(model.await_args_list[1].kwargs["memories"], facts if text else [])
            self.assertEqual(model.await_args_list[1].args[3], "neutral data")
            if text:
                memory.search.assert_awaited_once_with(text, None, "lookup")
                self.assertEqual(memory.capture.await_args.args[0], {"evidence": text})
                self.assertEqual(completed.index("save") < completed.index("api-response"), timing == "onEvidence")
            else:
                memory.search.assert_not_awaited()
                memory.capture.assert_not_awaited()

    async def test_wire_capture_requires_selected_evidence_and_authentic_user_text(self):
        session = MemorySession(None, "owner", uuid4(), uuid4())
        facts = ["I prefer tea", "I own a cat", "I live in Paris"]
        model = AsyncMock()
        model.complete.return_value = json.dumps({"memories": [
            {"content": fact, "kind": "fact", "evidence": fact} for fact in facts]})
        with patch("memory.embed", new=AsyncMock(return_value=[vector()])):
            await session.capture({"evidence": {"selected": [facts[0], facts[2]]}}, model, "save",
                                  ChatRequest(message="I prefer tea and I own a cat"))
        self.assertEqual([entry["content"] for entry in session.pending], [facts[0]])

    async def test_embedding_request_order_and_validation(self):
        result = {"model": EMBEDDING_MODEL, "data": [
            {"index": 1, "embedding": vector(1)}, {"index": 0, "embedding": vector()}]}
        client = AsyncMock()
        client.post.return_value = httpx.Response(200, json=result)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("memory.httpx.AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            self.assertEqual(await embed(["one", "two"]), [vector(), vector(1)])
            body = client.post.await_args.kwargs["json"]
            self.assertEqual((body["model"], body["dimensions"]), (EMBEDDING_MODEL, EMBEDDING_DIMENSIONS))
            invalid = [result | {"model": "another-model"}, result | {"data": []},
                       result | {"data": [{"index": 0, "embedding": [1.0]}]},
                       result | {"data": [{"index": 0, "embedding": [0.0] * EMBEDDING_DIMENSIONS}]},
                       result | {"data": [{"index": 0, "embedding": [math.inf] * EMBEDDING_DIMENSIONS}]}]
            for payload in invalid:
                client.post.return_value = Mock(is_success=True, json=Mock(return_value=payload))
                with self.assertRaises(HTTPException):
                    await embed(["one"])
            client.post.return_value = httpx.Response(429)
            with self.assertRaises(HTTPException) as error:
                await embed(["one"])
            self.assertEqual(error.exception.status_code, 502)

    async def test_extraction_requires_user_evidence_and_skips_empty_batches(self):
        session = MemorySession(None, "owner", uuid4(), uuid4())
        payload = {"state": {"Context": "earlier"}, "inputs": {"reply": "okay"},
                   "history": [{"role": "assistant", "content": "You live in Paris"}],
                   "latestTurn": {"user": "I prefer tea", "assistant": "okay"}}
        model = SimpleNamespace(complete=AsyncMock(return_value=json.dumps({"memories": [
            {"content": "User prefers tea", "kind": "preference", "evidence": "I prefer tea"},
            {"content": "User lives in Paris", "kind": "fact", "evidence": "You live in Paris"}]})))
        with patch("memory.embed", new=AsyncMock(return_value=[vector()])) as embedding:
            await session.capture(payload, model, "update")
            embedding.assert_awaited_once_with(["User prefers tea"])
        self.assertEqual(len(session.pending), 1)
        self.assertEqual(session.pending[0]["source"]["node"], "update")
        model.complete.return_value = '{"memories":[]}'
        with patch("memory.httpx.AsyncClient") as client:
            await session.capture(payload, model, "update")
            client.assert_not_called()
        model.complete.return_value = '{"memories":[{"content":"invented"}]}'
        with self.assertRaises(HTTPException):
            await session.capture(payload, model, "update")

    async def test_malformed_extraction_gets_one_correction_attempt(self):
        session = MemorySession(None, "owner", uuid4(), uuid4())
        model = SimpleNamespace(complete=AsyncMock(side_effect=['{"memories":[]} extra prose', '{"memories":[]}']))
        await session.capture({"history": [], "latestTurn": {"user": "Hello"}}, model, "update")
        self.assertEqual(model.complete.await_count, 2)
        self.assertEqual(session.pending, [])

    async def test_recall_bounds_query_and_includes_prior_context(self):
        session = MemorySession(None, "owner", uuid4(), uuid4())
        with patch("memory.embed", new=AsyncMock(return_value=[vector()])) as embedding, \
                patch("memory.search_memories", return_value=[]) as search:
            await session.recall(ChatRequest(message="which drink?"), {"Context": "tea"})
            self.assertIn('"Context":"tea"', embedding.await_args.args[0][0])
            search.assert_called_once_with(None, "owner", "which drink?", vector())
            await session.recall(ChatRequest(message="猫" * 8000), {"Context": "tea"})
            self.assertLessEqual(len(embedding.await_args.args[0][0].encode()), 6000)

    def test_recalled_memories_enter_system_prompt_without_changing_user_input(self):
        library = Mock()
        library.render.return_value = "Be helpful"
        definition = {"instructions": {"name": "rules", "version": 1}, "prompt": None}
        prompts = AgentPrompts(library, definition, None, history=[], memories=[{"content": "Prefers tea"}])
        messages = prompts.render("test", {"request": "Which drink?"})
        self.assertIn("untrusted historical information", messages[0]["content"])
        self.assertIn("Prefers tea", messages[0]["content"])
        self.assertEqual(messages[1], {"role": "user", "content": "Which drink?"})


@unittest.skipUnless(os.getenv("LYRA_TEST_DATABASE_URL"), "Set LYRA_TEST_DATABASE_URL for PostgreSQL checks")
class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.storage = SQLAlchemyStorage.from_postgres_url(os.environ["LYRA_TEST_DATABASE_URL"])
        self.engine = self.storage.engine
        self.schema = "lyra_memory_test_" + uuid4().hex
        with self.engine.begin() as conn:
            conn.execute(CreateSchema(self.schema))
        self.addCleanup(self.cleanup_database)
        self.storage.engine = self.engine.execution_options(schema_translate_map={
            "agent_studio": self.schema, "prompt_studio": self.schema})
        metadata.tables["prompt_studio.prompt_versions"].create(self.storage.engine)
        with schema_transaction(self.storage.engine) as conn:
            apply_migrations(conn)
        self.library = PromptLibrary(self.storage, "test")

    def cleanup_database(self):
        with self.engine.begin() as conn:
            conn.execute(DropSchema(self.schema, cascade=True))
        self.engine.dispose()

    def candidate(self, content="User prefers tea", embedding=None):
        return {"content": content, "kind": "preference", "evidence": "I prefer tea",
                "source": {"turnId": str(uuid4())}, "embedding": embedding or vector()}

    def new_run(self, library="test"):
        run_id = uuid4()
        with self.storage.engine.begin() as conn:
            conn.execute(runs.insert().values(id=run_id, library=library, flow_id=uuid4(), revision=1,
                source="test", status="completed", started_at=utcnow(), snapshot={}))
        return run_id

    def save(self, owner, candidates, library="test", run_id=None):
        run_id = run_id or self.new_run(library)
        with self.storage.engine.begin() as conn:
            return save_memories(conn, library, owner, candidates, run_id)

    def test_turn_links_preserve_origin_reinforcement_and_recall(self):
        first, second = self.new_run(), self.new_run()
        item = self.candidate()
        created = self.save("one", [item], run_id=first)[0]
        repeated = self.save("one", [item], run_id=second)[0]
        self.assertEqual(created["id"], repeated["id"])
        self.assertEqual((created["relation"], repeated["relation"]), ("created", "reinforced"))
        with self.storage.engine.begin() as conn:
            record_recalls(conn, "test", "one", [created], second)
            record_recalls(conn, "test", "one", [created], second)
            record_recalls(conn, "test", "other-owner", [created], first)
            links = memories_for_run(conn, "test", second, "one")
            self.assertEqual({row["relation"] for row in links}, {"reinforced", "recalled"})
            self.assertEqual(memories_for_run(conn, "test", first, "other-owner"), [])
        detail = memory_detail(self.library, created["id"], "one", limit=1)
        self.assertEqual(detail["source"]["runId"], str(first))
        self.assertEqual(detail["turns"][0]["relation"], "created")
        self.assertTrue(detail["hasMore"])
        self.assertEqual(len(memory_detail(self.library, created["id"], "one", offset=1)["turns"]), 2)
        with self.assertRaises(HTTPException) as denied:
            memory_detail(self.library, created["id"], "other-owner")
        self.assertEqual(denied.exception.status_code, 404)
        self.assertEqual(list_memories(self.library, "other-owner")["memories"], [])

    def test_upgrade_restores_existing_creation_links(self):
        run_id = self.new_run()
        created = self.save("one", [self.candidate()], run_id=run_id)[0]
        recalled_run = self.new_run()
        private = self.save("two", [self.candidate("Another owner's memory")])[0]
        with self.storage.engine.begin() as conn:
            conn.execute(checkpoints.insert().values(library="test", owner="one", conversation_id=uuid4(),
                flow_id=uuid4(), version=1, turn_id=uuid4(), request_hash="a" * 64, objects={}, run_id=recalled_run))
            conn.execute(events.insert().values(run_id=recalled_run, seq=1, node="api-input", kind="memory.recalled",
                at=utcnow(), elapsed_ms=0, data={"ids": [created["id"], private["id"]]}))
            conn.execute(text(f"DROP TABLE {self.schema}.memory_turns"))
            conn.execute(text(f"DELETE FROM {self.schema}.lyra_schema_migrations WHERE revision >= 6"))
        with schema_transaction(self.storage.engine) as conn:
            apply_migrations(conn)
        detail = memory_detail(self.library, created["id"], "one")
        self.assertEqual(len(detail["turns"]), 2)
        self.assertEqual(detail["turns"][0]["run_id"], run_id)
        self.assertEqual(detail["turns"][0]["relation"], "created")
        self.assertEqual(detail["turns"][1]["relation"], "recalled")
        self.assertEqual(len(memory_detail(self.library, private["id"], "two")["turns"]), 1)

    def test_hybrid_lexical_semantic_and_owner_library_isolation(self):
        self.save("one", [self.candidate(), self.candidate("Dark roast coffee", vector(1))])
        self.save("two", [self.candidate("SECRET tea preference")])
        self.save("one", [self.candidate("OTHER LIBRARY tea preference")], "other")
        text_hits = search_memories(self.library, "one", "tea")
        self.assertEqual([row["content"] for row in text_hits], ["User prefers tea"])
        semantic = search_memories(self.library, "one", "warm beverage", vector())
        self.assertEqual([row["content"] for row in semantic], ["User prefers tea"])
        fused = search_memories(self.library, "one", "coffee", vector())
        self.assertEqual({row["content"] for row in fused}, {"User prefers tea", "Dark roast coffee"})
        self.assertEqual(search_memories(self.library, "nobody", "tea", vector()), [])

    def test_duplicates_and_partial_failure_are_atomic(self):
        item = self.candidate()
        self.save("one", [item, item | {"content": "  USER prefers   tea "}])
        with self.storage.engine.connect() as conn:
            self.assertEqual(conn.execute(text(f"SELECT count(*) FROM {self.schema}.memories")).scalar(), 1)
            self.assertEqual(conn.execute(text(f"SELECT count(*) FROM {self.schema}.memory_embeddings")).scalar(), 1)
        with self.assertRaises(IntegrityError):
            self.save("one", [self.candidate("New memory"), self.candidate("Broken memory", [1.0])])
        self.assertEqual(search_memories(self.library, "one", "new"), [])
