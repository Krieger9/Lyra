import json
import os
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx
from fastapi import Request
from prompt_studio import PromptLibrary, SQLAlchemyStorage
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from agent_store import definitions, metadata, seed_defaults
from schema_migrations import apply_migrations, schema_transaction
from app import app
from auth import require_admin_access, require_chat_access, require_design_access, require_mcp_access


@unittest.skipUnless(os.getenv("LYRA_TEST_DATABASE_URL"), "Set LYRA_TEST_DATABASE_URL for isolated PostgreSQL checks")
class AgentDefinitionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        memory_config = patch.dict(os.environ, {"LYRA_MEMORY_ENABLED": "false"})
        memory_config.start()
        self.addCleanup(memory_config.stop)
        self.schema = "lyra_definition_test_" + uuid4().hex
        self.storage = SQLAlchemyStorage.from_postgres_url(os.environ["LYRA_TEST_DATABASE_URL"])
        self.engine = self.storage.engine
        with self.engine.begin() as connection:
            connection.execute(CreateSchema(self.schema))
        self.addCleanup(self.cleanup_database)
        self.storage.engine = self.engine.execution_options(schema_translate_map={
            "agent_studio": self.schema, "prompt_studio": self.schema,
        })
        metadata.tables["prompt_studio.prompt_versions"].create(self.storage.engine)
        self.migrate()
        self.library = PromptLibrary(self.storage, "test")
        seed_defaults(self.library)
        self.library.add("instructions", "Be helpful.")
        self.library.add("request", "{{ request }}")
        library_state = patch.object(app.state, "library", self.library, create=True)
        library_state.start()
        self.addCleanup(library_state.stop)
        studio = SimpleNamespace()
        state = patch.object(app.state, "studio", studio, create=True)
        state.start()
        self.addCleanup(state.stop)
        override = patch.dict(app.dependency_overrides, {require_admin_access: lambda: ["User.Admin"]})
        override.start()
        self.addCleanup(override.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.body = {"name": "Planner", "instructions": {"name": "instructions", "version": 1},
                     "prompt": {"name": "request", "version": 1}}

    def cleanup_database(self):
        with self.engine.begin() as connection:
            connection.execute(DropSchema(self.schema, cascade=True))
        self.engine.dispose()

    def migrate(self):
        with schema_transaction(self.storage.engine) as connection:
            apply_migrations(connection)

    async def test_create_edit_and_pin_versions(self):
        created = await self.client.post("/api/admin/agents", json=self.body)
        self.assertEqual(created.status_code, 201, created.text)
        agent = created.json()
        revision = await self.client.post("/api/admin/prompts", json={"name": "request", "text": "Updated"})
        self.assertEqual(revision.json()["version"], 2)
        catalog = (await self.client.get("/api/admin/catalog")).json()
        self.assertEqual(catalog["agents"][0]["prompt"]["version"], 1)
        self.assertEqual(len(catalog["prompts"]), 4)
        update = self.body | {"name": "Reviewer", "revision": agent["revision"],
                              "prompt": {"name": "request", "version": 2}}
        url = "/api/admin/agents/" + agent["id"]
        saved = await self.client.put(url, json=update)
        self.assertEqual(saved.json()["revision"], 2)
        self.assertEqual((await self.client.put(url, json=update)).status_code, 409)
        self.assertEqual((await self.client.get("/api/admin/catalog")).json()["agents"][0]["name"], "Reviewer")

    async def test_reject_missing_references_and_invalid_fields(self):
        for body in [
            self.body | {"instructions": {"name": "absent", "version": 1}},
            self.body | {"prompt": {"name": "request", "version": 999}},
            self.body | {"name": " "},
        ]:
            response = await self.client.post("/api/admin/agents", json=body)
            self.assertEqual(response.status_code, 422)
        self.assertEqual((await self.client.get("/api/admin/catalog")).json()["agents"], [])

    async def test_defaults_are_versioned_and_raw_input_round_trips(self):
        self.assertEqual(self.library.get("Instructions.default").text, "You are a helpful agent")
        self.library.add("Instructions.default", "You are a careful reviewer")
        seed_defaults(self.library)
        seed_defaults(self.library)
        self.assertEqual(len(self.library.list_versions("Instructions.default")), 2)
        created = await self.client.post("/api/admin/agents", json={"name": "Raw input agent"})
        self.assertEqual(created.status_code, 201, created.text)
        agent = created.json()
        self.assertIsNone(agent["prompt"])
        self.assertEqual(agent["instructions"], {"name": "Instructions.default", "version": 2})
        url = "/api/admin/agents/" + agent["id"]
        templated = await self.client.put(url, json={
            "name": agent["name"], "instructions": agent["instructions"],
            "prompt": {"name": "request", "version": 1}, "revision": agent["revision"],
        })
        self.assertEqual(templated.json()["prompt"]["name"], "request")
        raw = await self.client.put(url, json={
            "name": agent["name"], "instructions": agent["instructions"],
            "prompt": None, "revision": templated.json()["revision"],
        })
        self.assertEqual(raw.status_code, 200, raw.text)
        catalog = (await self.client.get("/api/admin/catalog")).json()
        self.assertIsNone(catalog["agents"][0]["prompt"])

    async def test_concurrent_prompt_defaults_create_one_version(self):
        ready = Barrier(2)

        def seed(text):
            ready.wait(timeout=5)
            return self.library.ensure_initial("chat", text)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(seed, ["first worker", "second worker"])
        self.assertEqual(first.text, second.text)
        self.assertEqual([v.version for v in self.library.list_versions("chat")], [1])

    def test_concurrent_publishers_retry_the_same_postgres_version(self):
        ready = Barrier(2)
        worker = local()

        def after_read(conn, cursor, statement, parameters, context, executemany):
            if "SELECT max(" in statement and not getattr(worker, "read_latest", False):
                worker.read_latest = True
                ready.wait(timeout=5)

        engine = self.storage.engine
        event.listen(engine, "after_cursor_execute", after_read)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                published = list(pool.map(lambda text: self.library.add("chat", text), ["first", "second"]))
        finally:
            event.remove(engine, "after_cursor_execute", after_read)
        self.assertEqual(sorted(v.version for v in published), [1, 2])
        self.assertEqual({v.text for v in self.library.list_versions("chat")}, {"first", "second"})

    async def test_existing_schema_upgrades_without_changing_template_agents(self):
        created = (await self.client.post("/api/admin/agents", json=self.body)).json()
        table = self.schema + ".agent_definitions"
        with self.engine.begin() as connection:
            connection.execute(text(f"DROP TABLE {self.schema}.lyra_schema_migrations"))
            connection.execute(text(f"ALTER TABLE {table} DROP COLUMN output_schema"))
            connection.execute(text(f"ALTER TABLE {table} DROP COLUMN provider_id, DROP COLUMN model_name"))
            connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN prompt_name SET NOT NULL, "
                                    "ALTER COLUMN prompt_version SET NOT NULL"))
            connection.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT agent_prompt_reference_complete"))
        self.migrate()
        self.migrate()
        response = await self.client.post("/api/admin/agents", json={"name": "New raw agent"})
        self.assertEqual(response.status_code, 201, response.text)
        catalog = (await self.client.get("/api/admin/catalog")).json()
        old = next(agent for agent in catalog["agents"] if agent["id"] == created["id"])
        self.assertEqual(old["prompt"], self.body["prompt"])
        self.assertIsNone(old["outputSchema"])
        self.assertIsNone(old["modelSelection"])
        with self.assertRaises(IntegrityError):
            with self.storage.engine.begin() as connection:
                connection.execute(definitions.update().where(
                    definitions.c.name == "New raw agent").values(prompt_name="request", prompt_version=None))

    def flow_graph(self):
        return {
            "nodes": [
                {"id": "api-input", "type": "apiInput", "position": {"x": 100, "y": 100}, "data": {"label": "API Input"}},
                {"id": "api-response", "type": "apiResponse", "position": {"x": 700, "y": 100}, "data": {"label": "API Response"}},
            ], "edges": [],
        }

    async def test_personality_storage_is_scoped_atomic_and_keeps_embeddings_current(self):
        from personalities import load_personality
        from fastapi import HTTPException
        from memory_store import save_memories
        from run_store import runs, utcnow
        url = "/api/admin/personalities"
        body = {"name": "Curious Lyra", "template": "You are {{name}}.", "goals": ["Help the user learn"],
                "memories": [{"content": "Lyra enjoys astronomy"}]}
        embedding = [1.0] + [0.0] * 3071
        with patch("personalities.embed", new=AsyncMock(side_effect=lambda texts: [embedding for _ in texts])) as embed:
            response = await self.client.post(url, json=body)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            saved = response.json()
            profile_url = url + "/" + saved["id"]
            self.assertEqual((await self.client.get(profile_url)).json(), saved)
            other = PromptLibrary(self.storage, "another-library")
            with self.assertRaises(HTTPException) as missing:
                load_personality(other, saved["id"])
            self.assertEqual(missing.exception.status_code, 404)
            edit = {key: value for key, value in saved.items() if key != "id"}
            edit["name"] = "Updated Lyra"
            updated = await self.client.put(profile_url, json=edit)
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(embed.await_args.args[0], [])
            self.assertEqual((await self.client.put(profile_url, json=edit)).status_code, 409)
            edit = {key: value for key, value in updated.json().items() if key != "id"}
            edit["memories"][0]["content"] = "Lyra enjoys botany"
            updated = await self.client.put(profile_url, json=edit)
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(embed.await_args.args[0], ["Lyra enjoys botany"])
            with self.storage.engine.connect() as connection:
                self.assertEqual(connection.execute(text(f"SELECT count(*) FROM {self.schema}.memories")).scalar(), 0)
                self.assertEqual(connection.execute(text(f"SELECT count(*) FROM {self.schema}.personality_memory_embeddings")).scalar(), 1)
                self.assertEqual(connection.execute(text(f"SELECT count(*) FROM {self.schema}.personality_memories "
                    "WHERE search_document @@ websearch_to_tsquery('english', 'botany')")).scalar(), 1)
            # Copying from a general memory preserves its origin without moving the original.
            run_id = uuid4()
            with self.storage.engine.begin() as connection:
                connection.execute(runs.insert().values(id=run_id, library=self.library.name, flow_id=uuid4(),
                    revision=1, source="test", status="completed", started_at=utcnow(), snapshot={}))
                source = save_memories(connection, self.library.name, "user", [{"content": "Lyra is my assistant",
                    "kind": "fact", "evidence": "Lyra is my assistant", "source": {}, "embedding": embedding}], run_id)[0]["id"]
            edit = {key: value for key, value in updated.json().items() if key != "id"}
            edit["memories"].append({"content": "Lyra is my assistant", "sourceMemoryId": source})
            updated = await self.client.put(profile_url, json=edit)
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["memories"][1]["sourceMemoryId"], source)
            detail = (await self.client.get("/api/admin/memories/" + source)).json()
            self.assertEqual(detail["personalityAssignments"][0]["id"], saved["id"])
            edit = {key: value for key, value in updated.json().items() if key != "id"}
            edit["memories"].append({"content": "An invalid source", "sourceMemoryId": str(uuid4())})
            self.assertEqual((await self.client.put(profile_url, json=edit)).status_code, 422)
            self.assertEqual((await self.client.get(profile_url)).json(), updated.json())
            edit["memories"] = []
            removed = await self.client.put(profile_url, json=edit)
            self.assertEqual(removed.status_code, 200, removed.text)
            with self.storage.engine.connect() as connection:
                self.assertEqual(connection.execute(text(f"SELECT count(*) FROM {self.schema}.personality_memory_embeddings")).scalar(), 0)
                self.assertEqual(connection.execute(text(f"SELECT count(*) FROM {self.schema}.memories")).scalar(), 1)
        body["name"] = "Failed save"
        with patch("personalities.embed", new=AsyncMock(side_effect=HTTPException(503, "Unavailable"))):
            self.assertEqual((await self.client.post(url, json=body)).status_code, 503)
        self.assertEqual(len((await self.client.get(url)).json()), 1)

    async def test_personality_flows_pin_profiles_and_respect_agent_opt_out(self):
        body = {"name": "Lyra", "template": "Use a curious voice.", "goals": ["Explain clearly"],
                "memories": [{"content": "Lyra enjoys astronomy"}]}
        with patch("personalities.embed", new=AsyncMock(side_effect=lambda texts: [[1.0] + [0.0] * 3071 for _ in texts])):
            profile = (await self.client.post("/api/admin/personalities", json=body)).json()
            agent = (await self.client.post("/api/admin/agents", json={"name": "Personality agent"})).json()
            graph = self.flow_graph()
            graph["nodes"].append({"id": "persona", "type": "personality", "position": {"x": 200, "y": 200},
                                   "data": {"label": "Personality", "personalityId": profile["id"]}})
            graph["nodes"].append({"id": "voice", "type": "agent", "position": {"x": 400, "y": 100},
                                   "data": {"definition": {"id": agent["id"]}}})
            graph["edges"] = [{"id": "in", "source": "api-input", "target": "voice"},
                              {"id": "out", "source": "voice", "target": "api-response"}]
            flow = (await self.client.post("/api/admin/flows", json={"name": "Persona flow", "graph": graph})).json()
            binding = "/api/admin/chat-flow/" + flow["id"]
            activated = await self.client.put(binding, json={"revision": 1})
            self.assertEqual(activated.json()["personality"]["revision"], 1)
            edited = await self.client.put("/api/admin/personalities/" + profile["id"],
                json=body | {"revision": 1, "template": "Use a formal voice.", "memories": []})
            self.assertEqual(edited.status_code, 200, edited.text)
        model = SimpleNamespace(complete=AsyncMock(return_value="Hello"))
        app.state.studio.model, app.state.studio.traces = model, Mock()
        with patch.dict(app.dependency_overrides, {require_chat_access: lambda: ["User.Chat"]}):
            chat = await self.client.post("/api/chat", json={"message": "Hello"})
            self.assertEqual(chat.status_code, 200, chat.text)
            prompt = model.complete.await_args.args[0]
            self.assertIn("curious voice", prompt)
            self.assertIn("Lyra enjoys astronomy", prompt)
            run = (await self.client.get("/api/admin/runs/" + chat.headers["X-Lyra-Run-Id"])).json()
            self.assertEqual(run["snapshot"]["personality"]["revision"], 1)
            event = next(e for e in run["events"] if e["kind"] == "personality.rendered")
            self.assertEqual(event["data"]["memories"][0]["id"], profile["memories"][0]["id"])
            history = (await self.client.get("/api/admin/personalities/" + profile["id"] + "/runs")).json()
            self.assertEqual(history[0]["id"], run["id"])
            await self.client.put(binding, json={"revision": 1})
            self.assertEqual((await self.client.get("/api/admin/chat-flow")).json()["personality"]["revision"], 2)
            await self.client.post("/api/chat", json={"message": "Hello again"})
            self.assertIn("formal voice", model.complete.await_args.args[0])
            self.assertNotIn("astronomy", model.complete.await_args.args[0])
        graph["nodes"][-1]["data"]["usePersonality"] = False
        await self.client.put("/api/admin/flows/" + flow["id"], json={"name": flow["name"], "revision": 1, "graph": graph})
        result = await self.client.post("/api/admin/flows/" + flow["id"] + "/run", json={"input": "Neutral"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertNotIn("formal voice", model.complete.await_args.args[0])
        self.assertNotIn("Explain clearly", model.complete.await_args.args[0])

    async def test_conversation_state_across_turns_retries_and_debug_runs(self):
        graph = self.flow_graph()
        state = {"scope": "conversation", "key": "context", "operation": "read", "initial": {"last": "initial"}}
        graph["nodes"] += [
            {"id": "read", "type": "state", "position": {"x": 1, "y": 1},
             "data": {"label": "Read", "stateDefinition": state}},
            {"id": "next", "type": "json", "position": {"x": 2, "y": 1},
             "data": {"label": "Next", "objectDefinition": {"base": {"last": "", "previous": ""},
                 "mappings": [{"edgeId": "msg", "source": ".value", "target": "/last"},
                              {"edgeId": "before", "source": ".value/last", "target": "/previous"}]}}},
            {"id": "write", "type": "state", "position": {"x": 3, "y": 1},
             "data": {"label": "Write", "stateDefinition": state | {"operation": "replace"}}},
        ]
        graph["edges"] = [{"id": key, "source": source, "target": target} for key, source, target in [
            ("start", "api-input", "read"), ("before", "read", "next"), ("msg", "api-input", "next"),
            ("after", "next", "write"), ("done", "write", "api-response")]]
        saved = await self.client.post("/api/admin/flows", json={"name": "Context", "graph": graph})
        self.assertEqual(saved.status_code, 201, saved.text)
        flow = saved.json()["id"]
        bound = await self.client.put("/api/admin/chat-flow/" + flow, json={"revision": 1})
        self.assertEqual(bound.status_code, 200, bound.text)
        conversation = str(uuid4())
        owner = "one"

        def authenticate(request: Request):
            request.state.chat_owner = owner
            return ["User.Chat"]

        async def send(message, turn=None):
            return await self.client.post("/api/chat", json={"message": message}, headers={
                "X-Lyra-Conversation-Id": conversation, "X-Lyra-Turn-Id": turn or str(uuid4())})

        with patch.dict(app.dependency_overrides, {require_chat_access: authenticate}):
            first_turn = str(uuid4())
            first = await send("first", first_turn)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["reply"], {"last": "first", "previous": "initial"})
            debug = await self.client.post(f"/api/admin/flows/{flow}/run", json={"input": "debug"})
            self.assertEqual(debug.json()["previous"], "initial")
            self.assertEqual((await send("second")).json()["reply"]["previous"], "first")
            replay = await send("first", first_turn)
            self.assertEqual(replay.headers["X-Lyra-Run-Id"], first.headers["X-Lyra-Run-Id"])
            self.assertEqual((await send("changed", first_turn)).status_code, 409)
            from json_mapping import MappingError
            with patch("conversation_state.ConversationTurn.commit", side_effect=MappingError("State too large")):
                failed = await send("uncommitted")
                self.assertEqual(failed.status_code, 422)
                failed_run = await self.client.get("/api/admin/runs/" + failed.headers["X-Lyra-Run-Id"])
                self.assertEqual(failed_run.json()["status"], "failed")
            self.assertEqual((await send("third")).json()["reply"]["previous"], "second")
            owner = "two"
            self.assertEqual((await send("other user")).json()["reply"]["previous"], "initial")
            owner, conversation = "one", str(uuid4())
            self.assertEqual((await send("new chat")).json()["reply"]["previous"], "initial")

    async def test_chat_context_update_failure_rolls_back_and_retry_returns_only_the_reply(self):
        agent = (await self.client.post("/api/admin/agents", json={"name": "Memory writer",
            "outputSchema": {"fields": [{"name": "Context", "type": "text"}]}})).json()
        graph = self.flow_graph()
        graph["stateObject"] = {"Context": "", "other": []}
        graph["nodes"] += [
            {"id": "answer", "type": "json", "position": {"x": 1, "y": 0},
             "data": {"objectDefinition": {"base": {"answer": "public reply"}}}},
            {"id": "memory", "type": "state", "position": {"x": 2, "y": 0}, "data": {"stateDefinition": {
                "scope": "conversation", "key": "memory", "operation": "generateObject", "agentId": agent["id"],
                "includeChatTurn": True, "mappings": [{"edgeId": "result", "source": ".value/Context", "target": "/Context"}]}}}]
        graph["edges"] = [{"id": key, "source": source, "target": target} for key, source, target in [
            ("in", "api-input", "answer"), ("out", "answer", "api-response"), ("reply", "api-response", "memory")]]
        flow = (await self.client.post("/api/admin/flows", json={"name": "Memory", "graph": graph})).json()["id"]
        self.assertEqual((await self.client.put("/api/admin/chat-flow/" + flow, json={"revision": 1})).status_code, 200)
        def authenticate(request: Request):
            request.state.chat_owner = "test owner"
        model = AsyncMock(side_effect=['{"Context":"old"}', '{"wrong":"bad"}', '{"Context":"new"}'])
        app.state.studio.model, app.state.studio.traces = SimpleNamespace(complete=model), Mock()
        headers = {"X-Lyra-Conversation-Id": str(uuid4()), "X-Lyra-Turn-Id": str(uuid4())}
        with patch.dict(app.dependency_overrides, {require_chat_access: authenticate}):
            first = await self.client.post("/api/chat", json={"message": "first"}, headers=headers)
            self.assertEqual(first.json(), {"reply": {"answer": "public reply"}})
            headers["X-Lyra-Turn-Id"] = str(uuid4())
            body = {"message": "correction", "history": [{"role": "user", "content": "first"}]}
            self.assertEqual((await self.client.post("/api/chat", json=body, headers=headers)).status_code, 502)
            success = await self.client.post("/api/chat", json=body, headers=headers)
            self.assertEqual(success.json(), first.json())
            replay = await self.client.post("/api/chat", json=body, headers=headers)
            self.assertEqual(replay.headers["X-Lyra-Run-Id"], success.headers["X-Lyra-Run-Id"])
        self.assertEqual(model.await_count, 3)
        record = (await self.client.get("/api/admin/runs/" + success.headers["X-Lyra-Run-Id"])).json()
        payload = json.loads(next(e["data"]["inputs"]["request"] for e in record["events"]
                                  if e["kind"] == "template.started"))
        self.assertEqual(payload["state"], {"Context": "old", "other": []})
        self.assertEqual(payload["history"], body["history"])
        self.assertEqual(payload["latestTurn"], {"user": "correction", "assistant": {"answer": "public reply"}})

    async def test_state_agent_snapshot_and_structured_property_mapping(self):
        schema = {"fields": [{"name": "recap", "type": "text"}]}
        agent = (await self.client.post("/api/admin/agents", json=self.body | {"outputSchema": schema})).json()
        graph = self.flow_graph()
        graph["stateObject"] = {"summary": "", "other": []}
        graph["nodes"].append({"id": "state", "type": "state", "position": {"x": 1, "y": 1},
            "data": {"stateDefinition": {"key": "context", "scope": "run", "operation": "generateObject",
                "agentId": agent["id"], "mappings": [{"edgeId": "result", "source": ".value/recap", "target": "/summary"}],
                "output": "property", "outputProperty": "summary"}}})
        graph["edges"] = [{"id": "in", "name": "message", "source": "api-input", "target": "state"},
                          {"id": "out", "source": "state", "target": "api-response"}]
        saved = await self.client.post("/api/admin/flows", json={"name": "Summarize", "graph": graph})
        self.assertEqual(saved.status_code, 201, saved.text)
        url = "/api/admin/flows/" + saved.json()["id"]
        self.assertEqual((await self.client.get(url)).json()["graph"]["stateObject"], graph["stateObject"])
        app.state.studio.model = SimpleNamespace(complete=AsyncMock(side_effect=['{"recap":"summary text"}', '{"wrong":42}']))
        app.state.studio.traces = Mock()
        run = await self.client.post(url + "/run", json={"input": "source text"})
        self.assertEqual(run.text, "summary text")
        record = (await self.client.get("/api/admin/runs/" + run.headers["X-Lyra-Run-Id"])).json()
        self.assertIn(agent["id"], record["snapshot"]["agents"])
        value = next(e["data"]["value"] for e in record["events"] if e["kind"] == "state.applied")
        self.assertEqual(value, {"summary": "summary text", "other": []})
        invalid = await self.client.post(url + "/run", json={"input": "source text"})
        self.assertEqual(invalid.status_code, 502, invalid.text)

    async def test_memory_capture_recall_replay_and_transaction_rollback(self):
        agent = (await self.client.post("/api/admin/agents", json={"name": "Answer"})).json()
        updater = (await self.client.post("/api/admin/agents", json={"name": "Context updater"})).json()
        graph = self.flow_graph()
        graph["stateObject"] = {"Context": ""}
        graph["nodes"] += [
            {"id": "answer", "type": "agent", "position": {"x": 1, "y": 0},
             "data": {"definition": {"id": agent["id"], "revision": 1}}},
            {"id": "update", "type": "state", "position": {"x": 2, "y": 0},
             "data": {"stateDefinition": {"scope": "conversation", "key": "context", "operation": "generate",
                 "property": "Context", "agentId": updater["id"], "includeChatTurn": True}}}]
        graph["edges"] = [{"id": key, "source": source, "target": target} for key, source, target in [
            ("in", "api-input", "answer"), ("out", "answer", "api-response"), ("after", "api-response", "update")]]
        saved = await self.client.post("/api/admin/flows", json={"name": "Memories", "graph": graph})
        self.assertEqual(saved.status_code, 201, saved.text)
        flow = saved.json()["id"]
        self.assertEqual((await self.client.put("/api/admin/chat-flow/" + flow, json={"revision": 1})).status_code, 200)
        owner = "one"

        def authenticate(request: Request):
            request.state.chat_owner = owner

        headers = {"X-Lyra-Conversation-Id": str(uuid4()), "X-Lyra-Turn-Id": str(uuid4())}
        body = {"message": "I prefer tea"}
        extraction = json.dumps({"memories": [{"content": "User prefers tea", "kind": "preference",
                                                "evidence": "I prefer tea"}]})
        model = AsyncMock(side_effect=["reply", "context", extraction])
        app.state.studio.model, app.state.studio.traces = SimpleNamespace(complete=model), Mock()
        from memory_store import EMBEDDING_DIMENSIONS
        embedding = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)
        from memory_store import save_memories

        def fail_after_write(*args):
            save_memories(*args)
            raise RuntimeError("Injected commit failure")

        with patch.dict(os.environ, {"LYRA_MEMORY_ENABLED": "true"}), \
                patch.dict(app.dependency_overrides, {require_chat_access: authenticate}), \
                patch("memory.embed", new=AsyncMock(side_effect=lambda texts: [embedding for _ in texts])) as embed:
            with patch("memory.save_memories", side_effect=fail_after_write):
                failed = await self.client.post("/api/chat", json=body, headers=headers)
            self.assertEqual(failed.status_code, 503, failed.text)
            with self.storage.engine.connect() as conn:
                for table in ("memories", "memory_embeddings", "memory_turns", "flow_state_turns"):
                    self.assertEqual(conn.execute(text(f"SELECT count(*) FROM {self.schema}.{table}")).scalar(), 0)
            model.side_effect = ["reply", "context", extraction]
            first = await self.client.post("/api/chat", json=body, headers=headers)
            self.assertEqual(first.status_code, 200, first.text)
            calls = embed.await_count
            replay = await self.client.post("/api/chat", json=body, headers=headers)
            self.assertEqual(replay.headers["X-Lyra-Run-Id"], first.headers["X-Lyra-Run-Id"])
            self.assertEqual(embed.await_count, calls)
            found = await self.client.post("/api/memories/search", json={"query": "tea", "mode": "text"})
            self.assertEqual(len(found.json()["memories"]), 1)
            self.assertEqual(found.json()["memories"][0]["source"]["runId"], first.headers["X-Lyra-Run-Id"])
            memory_id = found.json()["memories"][0]["id"]
            detail = await self.client.get("/api/memories/" + memory_id)
            self.assertEqual(detail.headers["cache-control"], "no-store")
            self.assertEqual(detail.json()["turns"][0]["turn_id"], headers["X-Lyra-Turn-Id"])
            related = await self.client.get("/api/memories/turns/" + first.headers["X-Lyra-Run-Id"])
            self.assertEqual(related.json()["memories"][0]["id"], memory_id)
            inspected = await self.client.get("/api/admin/runs/" + first.headers["X-Lyra-Run-Id"])
            self.assertEqual(inspected.json()["memories"][0]["relation"], "created")
            # Recall crosses conversations for the same owner.
            headers = {"X-Lyra-Conversation-Id": str(uuid4()), "X-Lyra-Turn-Id": str(uuid4())}
            model.side_effect = ["tea", "new context", '{"memories":[]}']
            second = await self.client.post("/api/chat", json={"message": "Which drink?"}, headers=headers)
            self.assertEqual(second.status_code, 200, second.text)
            answer_prompt = model.await_args_list[-3].args[0]
            self.assertIn("User prefers tea", answer_prompt)
            updater_payload = json.loads(model.await_args_list[-1].args[0][1]["content"])
            self.assertEqual(updater_payload["recalledMemories"][0]["content"], "User prefers tea")
            self.assertEqual(updater_payload["latestTurn"], {"user": "Which drink?", "assistant": "tea"})
            owner = "two"
            self.assertEqual((await self.client.get("/api/memories/" + memory_id)).status_code, 404)
            self.assertEqual((await self.client.get("/api/memories/turns/" + first.headers["X-Lyra-Run-Id"])).json(),
                             {"memories": []})
            self.assertEqual((await self.client.post("/api/memories/search",
                json={"query": "tea"})).json()["memories"], [])
            headers["X-Lyra-Turn-Id"] = str(uuid4())
            model.side_effect = ["unknown", "context", '{"memories":[]}']
            other = await self.client.post("/api/chat", json={"message": "Which drink?"}, headers=headers)
            self.assertEqual(other.status_code, 200, other.text)
            self.assertNotIn("User prefers tea", model.await_args_list[-3].args[0])

    async def test_provider_model_selection_for_agent_test_flow_and_pinned_chat(self):
        mids = [(await self.client.post("/api/admin/models", json={"name": name})).json()["id"]
                for name in ("small", "large:v2")]
        group_body = {"name": "Local models", "modelIds": mids}
        group = (await self.client.post("/api/admin/model-groups", json=group_body)).json()
        provider_body = {"name": "Lab Ollama", "uri": "http://lab.test:11434", "groupId": group["id"]}
        provider = (await self.client.post("/api/admin/providers", json=provider_body)).json()
        catalog = (await self.client.get("/api/admin/catalog")).json()
        self.assertEqual(catalog["modelGroups"], [group])
        self.assertEqual(catalog["providers"], [provider])
        choice = {"providerId": provider["id"], "model": "large:v2"}
        agent = (await self.client.post("/api/admin/agents", json=self.body | {"modelSelection": choice})).json()
        agent_url = "/api/admin/agents/" + agent["id"]
        legacy = await self.client.put(agent_url, json=self.body | {"revision": 1})
        self.assertEqual(legacy.json()["modelSelection"], choice)
        for invalid in [choice | {"model": "unknown"}, choice | {"providerId": str(uuid4())}]:
            self.assertEqual((await self.client.put(agent_url, json=self.body | {
                "revision": 2, "modelSelection": invalid})).status_code, 422)
        change = await self.client.put("/api/admin/model-groups/" + group["id"],
            json=group_body | {"modelIds": mids[:1], "revision": 1})
        self.assertEqual(change.status_code, 409)
        other_group = (await self.client.post("/api/admin/model-groups",
            json={"name": "Small only", "modelIds": mids[:1]})).json()
        reassigned = await self.client.put("/api/admin/providers/" + provider["id"],
            json=provider_body | {"groupId": other_group["id"], "revision": 1})
        self.assertEqual(reassigned.status_code, 409)
        app.state.studio.traces = Mock()
        graph = self.flow_graph()
        graph["nodes"].append({"type": "agent", "id": "worker", "position": {"x": 0, "y": 0},
                               "data": {"definition": {"id": agent["id"]}}})
        graph["edges"] = [{"id": "input", "source": "api-input", "target": "worker"},
                          {"id": "reply", "source": "worker", "target": "api-response"}]
        flow = (await self.client.post("/api/admin/flows", json={"name": "Selected model", "graph": graph})).json()
        url = "/api/admin/flows/" + flow["id"]
        self.assertEqual((await self.client.put("/api/admin/chat-flow/" + flow["id"],
                                               json={"revision": 1})).status_code, 200)
        updated = await self.client.put("/api/admin/providers/" + provider["id"],
            json=provider_body | {"uri": "http://new.test:11434", "revision": 1})
        self.assertEqual(updated.status_code, 200, updated.text)

        async def complete(adapter, prompt):
            return adapter.endpoint.uri + " " + adapter.model.id

        async def observed(adapter, prompt, observe):
            return await complete(adapter, prompt), None

        with patch("model_adapter.MeteredOllamaAdapter.complete", complete), \
             patch("model_adapter.MeteredOllamaAdapter.complete_observed", observed):
            tested = await self.client.post(agent_url + "/test", json={"input": "hello"})
            self.assertEqual(tested.json()["output"], "http://new.test:11434 large:v2")
            run = await self.client.post(url + "/run", json={"input": "hello"})
            self.assertEqual(run.text, "http://new.test:11434 large:v2")
            with patch.dict(app.dependency_overrides, {require_chat_access: lambda: ["User.Chat"]}):
                chat = await self.client.post("/api/chat", json={"message": "hello"})
            self.assertEqual(chat.json()["reply"], "http://lab.test:11434 large:v2")
        reset = await self.client.put(agent_url, json=self.body | {"revision": 2, "modelSelection": None})
        self.assertIsNone(reset.json()["modelSelection"])

    async def test_catalog_model_identity_protocol_and_membership(self):
        base = "/api/admin/"
        model = (await self.client.post(base + "models", json={"name": "first"})).json()
        group = (await self.client.post(base + "model-groups", json={"name": "Group", "modelIds": [model["id"]]})).json()
        extra = (await self.client.post(base + "models", json={"name": "second", "addToGroup": group["id"],
                                                              "groupRevision": group["revision"]})).json()
        stale = await self.client.post(base + "models", json={"name": "rolled-back", "addToGroup": group["id"],
                                                              "groupRevision": group["revision"]})
        self.assertEqual(stale.status_code, 409)
        protocol = (await self.client.post(base + "protocols", json={"name": "Compatible", "adapter": "openai"})).json()
        provider = (await self.client.post(base + "providers", json={"name": "Local", "uri": "http://local.test/v1",
                          "groupId": group["id"], "protocolId": protocol["id"]})).json()
        selection = {"providerId": provider["id"], "model": "first"}
        agent = (await self.client.post(base + "agents", json=self.body | {"modelSelection": selection})).json()
        renamed = await self.client.put(base + "models/" + model["id"], json={"name": "renamed", "revision": 1})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        catalog = (await self.client.get(base + "catalog")).json()
        self.assertEqual(catalog["modelGroups"][0]["modelIds"], [model["id"], extra["id"]])
        self.assertEqual(catalog["modelGroups"][0]["models"], ["renamed", "second"])
        self.assertEqual(catalog["agents"][0]["modelSelection"]["model"], "renamed")
        self.assertEqual(len(catalog["models"]), 2)
        app.state.studio.traces = Mock()
        with patch("model_adapter.ChatCompletionsAdapter.complete", new_callable=AsyncMock, return_value="compatible") as call:
            run = await self.client.post(base + "agents/" + agent["id"] + "/test", json={"input": "hi"})
            self.assertEqual(run.json()["output"], "compatible")
            call.assert_awaited_once()
        invalid = await self.client.post(base + "protocols", json={"name": "bad", "adapter": "missing"})
        self.assertEqual(invalid.status_code, 422)

    async def test_upgrade_existing_ollama_provider_and_group(self):
        base = "/api/admin/"
        model = (await self.client.post(base + "models", json={"name": "old-model"})).json()
        group = (await self.client.post(base + "model-groups", json={"name": "Local models", "modelIds": [model["id"]]})).json()
        await self.client.post(base + "providers", json={"name": "Local", "uri": "http://local.test", "groupId": group["id"]})
        with self.storage.engine.begin() as connection:
            connection.execute(text(f"DROP TABLE {self.schema}.lyra_schema_migrations"))
            connection.execute(text(f"ALTER TABLE {self.schema}.providers DROP COLUMN protocol_id"))
            connection.execute(text(f"DROP TABLE {self.schema}.protocols"))
            connection.execute(text(f"DROP TABLE {self.schema}.models"))
        self.migrate()
        self.migrate()
        catalog = (await self.client.get(base + "catalog")).json()
        self.assertEqual([row["name"] for row in catalog["models"]], ["old-model"])
        self.assertEqual(catalog["modelGroups"][0]["modelIds"], [catalog["models"][0]["id"]])
        native = next(p for p in catalog["protocols"] if p["adapter"] == "ollama")
        self.assertEqual(catalog["providers"][0]["protocolId"], native["id"])

    async def test_switch_picker_save_reopen_and_run_history(self):
        schema = {"fields": [{"name": "category", "type": "choice", "required": False,
                              "choices": ["chat", "task"]}, {"name": "reason", "type": "text"}]}
        agent = (await self.client.post("/api/admin/agents", json=self.body | {"outputSchema": schema})).json()
        graph = self.flow_graph()
        graph["nodes"] += [
            {"id": "agent", "type": "agent", "position": {"x": 1, "y": 0}, "data": {"definition": agent}},
            {"id": "switch", "type": "switch", "position": {"x": 2, "y": 0}, "data": {"switchDefinition": {
                "paths": [{"id": "chat-id", "label": "Conversation", "checks": [
                    {"field": "/category", "operator": "equals", "value": "chat"}]}]}}},
        ]
        graph["edges"] = [{"id": "in", "source": "api-input", "target": "agent"},
                          {"id": "classified", "source": "agent", "target": "switch"},
                          *[{"id": port, "source": "switch", "sourceHandle": port, "target": "api-response"}
                            for port in ("chat-id", "otherwise")]]
        fields = await self.client.post("/api/admin/flows/switch-fields/switch", json=graph)
        self.assertEqual(fields.json(), {"/category": ["chat", "task"], "/reason": []})
        created = await self.client.post("/api/admin/flows", json={"name": "Router", "graph": graph})
        self.assertEqual(created.status_code, 201, created.text)
        url = "/api/admin/flows/" + created.json()["id"]
        saved = (await self.client.get(url)).json()["graph"]
        self.assertEqual([wire["sourceHandle"] for wire in saved["edges"][-2:]], ["chat-id", "otherwise"])
        model = SimpleNamespace(complete=AsyncMock(return_value='{"reason":"No category"}'))
        app.state.studio.model, app.state.studio.traces = model, Mock()
        result = await self.client.post(url + "/run", json={"input": "hello"})
        self.assertEqual(result.json(), {"reason": "No category"})
        run = (await self.client.get("/api/admin/runs/" + result.headers["X-Lyra-Run-Id"])).json()
        decision = next(e for e in run["events"] if e["kind"] == "switch.selected")
        self.assertEqual(decision["data"]["path"], "otherwise")
        schema["fields"][0]["choices"] = ["other", "task"]
        updated = await self.client.put("/api/admin/agents/" + agent["id"],
                                       json=self.body | {"revision": 1, "outputSchema": schema})
        self.assertEqual(updated.status_code, 200, updated.text)
        invalid = await self.client.post(url + "/run", json={"input": "hello"})
        self.assertEqual(invalid.status_code, 422)
        self.assertIn("no longer an available choice", invalid.json()["detail"])
        model.complete.assert_awaited_once()

    async def test_chat_runs_preexisting_snapshot_without_current_agent_definitions(self):
        from run_store import active

        fixture = Path(__file__).with_name("fixtures") / "router-snapshot.json"
        saved = json.loads(fixture.read_text(encoding="utf-8"))
        with self.storage.engine.begin() as conn:
            conn.execute(active.insert().values(library=self.library.name, snapshot=saved))
        model = SimpleNamespace(complete=AsyncMock(return_value='{"category":"chat","reason":"fixture"}'))
        app.state.studio.model, app.state.studio.traces = model, Mock()
        with patch.dict(app.dependency_overrides, {require_chat_access: lambda: ["User.Chat"]}):
            reply = await self.client.post("/api/chat", json={"message": "hello"})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.json()["reply"]["category"], "chat")
        run = (await self.client.get("/api/admin/runs/" + reply.headers["X-Lyra-Run-Id"])).json()
        self.assertEqual(run["snapshot"], saved)
        self.assertEqual(next(e["data"]["path"] for e in run["events"] if e["kind"] == "switch.selected"), "chat-id")

    async def test_debug_export_and_chat_pin_the_executed_graph(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from app import mcp_server

        graph = self.flow_graph()
        graph["nodes"].append({"id": "merge", "type": "json", "position": {"x": 2, "y": 1},
            "data": {"label": "Merge", "objectDefinition": {"base": {"original": None},
                "mappings": [{"edgeId": "raw", "source": ".value", "target": "/original"}]}}})
        graph["edges"] = [{"id": "raw", "name": "request", "source": "api-input", "target": "merge"},
                          {"id": "out", "source": "merge", "target": "api-response"}]
        flow = (await self.client.post("/api/admin/flows", json={"name": "Original", "graph": graph})).json()
        url = "/api/admin/flows/" + flow["id"]
        self.assertEqual((await self.client.post(url + "/runs", json={"revision": 2})).status_code, 409)
        debug = await self.client.post(url + "/runs", json={"input": "  hello  ", "revision": 1})
        self.assertEqual(debug.status_code, 202, debug.text)
        run_id = debug.json()["id"]
        read = "/api/admin/runs/" + run_id
        record = (await self.client.get(read)).json()
        self.assertEqual(record["result"], {"original": "  hello  "})
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["events"][-1]["kind"], "run.completed")
        binding = "/api/admin/chat-flow/" + flow["id"]
        self.assertEqual((await self.client.put(binding, json={"revision": 1})).status_code, 200)
        graph["nodes"][-1]["data"]["objectDefinition"]["base"]["later"] = True
        await self.client.put(url, json={"name": "Later", "graph": graph, "revision": 1})
        with patch.dict(app.dependency_overrides, {require_chat_access: lambda: ["User.Chat"]}):
            chat = await self.client.post("/api/chat", json={"message": "  chat  "})
        self.assertEqual(chat.json(), {"reply": {"original": "  chat  "}})
        chat_run = (await self.client.get("/api/admin/runs/" + chat.headers["X-Lyra-Run-Id"])).json()
        self.assertEqual((chat_run["source"], chat_run["revision"]), ("chat", 1))
        self.assertEqual((await self.client.delete(binding + "?revision=2")).status_code, 409)
        self.assertEqual((await self.client.delete(binding + "?revision=1")).status_code, 200)
        grant = patch.dict(app.dependency_overrides, {require_mcp_access: lambda: ["User.Admin"]})
        grant.start()
        self.addCleanup(grant.stop)
        async with mcp_server.session_manager.run(), streamable_http_client(
            "http://localhost:8765/mcp", http_client=self.client) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                listing = await session.list_tools()
                self.assertEqual({tool.name for tool in listing.tools}, {"list_flow_runs", "inspect_flow_run"})
                reply = await session.call_tool("inspect_flow_run", {"run_id": run_id, "limit": 2})
                self.assertFalse(reply.isError, reply.content)
                page = reply.structuredContent
                self.assertTrue(page["hasMore"])
                self.assertEqual(page["snapshot"]["name"], "Original")
                rest = await session.call_tool("inspect_flow_run", {"run_id": run_id, "after": page["events"][-1]["seq"],
                                                                     "include_snapshot": False})
                self.assertNotIn("snapshot", rest.structuredContent)
                self.assertEqual(page["events"] + rest.structuredContent["events"], record["events"])

    async def test_structured_classifier_test_and_flow_use_same_output(self):
        schema = {"fields": [
            {"name": "category", "type": "choice", "choices": ["conversation", "intelligence"]},
            {"name": "explanation", "type": "text", "description": "Explain the chosen category."},
        ]}
        agent = (await self.client.post("/api/admin/agents", json=self.body | {"outputSchema": schema})).json()
        url = "/api/admin/agents/" + agent["id"]
        legacy_edit = await self.client.put(url, json=self.body | {"revision": 1})
        self.assertEqual(legacy_edit.json()["outputSchema"], agent["outputSchema"])
        model = SimpleNamespace(complete=AsyncMock(return_value='{"category":"conversation","explanation":"A greeting."}'))
        app.state.studio.model, app.state.studio.traces = model, Mock()
        tested = await self.client.post(url + "/test", json={"input": "Hello"})
        self.assertEqual(tested.json()["output"], {"category": "conversation", "explanation": "A greeting."})
        self.assertIn("Explain the chosen category.", model.complete.await_args.args[0])
        graph = self.flow_graph()
        graph["nodes"] += [
            {"id": "agent", "type": "agent", "position": {"x": 1, "y": 2}, "data": {"definition": agent}},
            {"id": "json", "type": "json", "position": {"x": 2, "y": 2}, "data": {"objectDefinition": {
                "base": {"Category": None}, "mappings": [{"edgeId": "classification", "source": ".value/category", "target": "/Category"}]}}},
        ]
        graph["edges"] = [{"id": key, "source": source, "target": target} for key, source, target in [
            ("input", "api-input", "agent"), ("classification", "agent", "json"), ("response", "json", "api-response")]]
        flow = (await self.client.post("/api/admin/flows", json={"name": "Classifier", "graph": graph})).json()
        run = "/api/admin/flows/" + flow["id"] + "/run"
        self.assertEqual((await self.client.post(run, json={"input": "Hello"})).json(), {"Category": "conversation"})
        model.complete.return_value = '{"category":"chat","explanation":"A greeting."}'
        for endpoint in [run, url + "/test"]:
            bad = await self.client.post(endpoint, json={"input": "Hello"})
            self.assertEqual(bad.status_code, 502)
            for part in ["Planner", "category", "conversation", "intelligence", "chat"]:
                self.assertIn(part, bad.json()["detail"])
        latest = (await self.client.get("/api/admin/runs", params={"flow_id": flow["id"]})).json()[0]
        failure = (await self.client.get("/api/admin/runs/" + latest["id"])).json()
        self.assertEqual(failure["status"], "failed")
        evidence = failure["events"]
        self.assertEqual([e["data"]["actual"] for e in evidence if e["kind"] == "gate.evaluated"
                          and not e["data"]["passed"]], ["chat"])
        self.assertTrue(any(e["kind"] == "template.started" and e["data"]["template"] == "{{ request }}" for e in evidence))
        self.assertFalse(any(e["kind"] == "node.started" and e["node"] == "json" for e in evidence))
        reset = await self.client.put(url, json=self.body | {"revision": 2, "outputSchema": None})
        self.assertIsNone(reset.json()["outputSchema"])

    async def test_flow_save_open_update_and_conflict(self):
        graph = self.flow_graph()
        created = await self.client.post("/api/admin/flows", json={"name": "Test flow", "graph": graph})
        self.assertEqual(created.status_code, 201, created.text)
        saved = created.json()
        url = "/api/admin/flows/" + saved["id"]
        opened = await self.client.get(url)
        self.assertEqual(opened.json()["graph"]["nodes"], saved["graph"]["nodes"])
        self.assertEqual((await self.client.get("/api/admin/flows")).json()[0]["id"], saved["id"])
        updated = await self.client.put(url, json={"name": "Renamed", "graph": graph, "revision": 1})
        self.assertEqual(updated.json()["revision"], 2)
        conflict = await self.client.put(url, json={"name": "Old edit", "graph": graph, "revision": 1})
        self.assertEqual(conflict.status_code, 409)
        success = await self.client.post(url + "/run", json={"input": "hello"})
        self.assertEqual((success.status_code, success.text), (200, ""))

    async def test_mcp_design_changes_are_visible_in_admin(self):
        from app import design_server
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        with patch.dict(app.dependency_overrides, {require_design_access: lambda: None}):
            async with design_server.session_manager.run(), streamable_http_client(
                "http://localhost:8765/mcp/design", http_client=self.client) as (reader, writer, _):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    self.assertIn("update_flow", {tool.name for tool in listing.tools})

                    async def call(name, **arguments):
                        reply = await session.call_tool(name, arguments)
                        self.assertFalse(reply.isError, reply.content)
                        return reply.structuredContent

                    prompt = await call("publish_prompt", body={"name": "request", "text": "Updated"})
                    self.assertEqual(prompt["version"], 2)
                    agent = await call("create_agent", body=self.body)
                    edit = self.body | {"name": "Reviewer", "revision": 1, "prompt": {"name": "request", "version": 2}}
                    await call("update_agent", agent_id=agent["id"], body=edit)
                    catalog = await call("get_catalog")
                    self.assertEqual(catalog["agents"][0]["prompt"], edit["prompt"])
                    self.assertEqual(catalog, (await self.client.get("/api/admin/catalog")).json())

                    draft = {"name": "MCP draft", "graph": self.flow_graph()}
                    flow = await call("create_flow", body=draft)
                    args = {"flow_id": flow["id"], "body": draft | {"revision": 1, "name": "Edited"}}
                    updated = await call("update_flow", **args)
                    conflict = await session.call_tool("update_flow", args)
                    self.assertTrue(conflict.isError)
                    self.assertIn("Reopen", conflict.content[0].text)
                    self.assertEqual(updated["revision"], 2)
                    self.assertEqual((await call("list_flows"))["result"][0]["name"], "Edited")
                    self.assertEqual(await call("get_flow", flow_id=flow["id"]), updated)
                    self.assertEqual((await self.client.get("/api/admin/flows/" + flow["id"])).json(), updated)

    async def test_flow_response_returns_exact_agent_output_and_errors(self):
        agent = (await self.client.post("/api/admin/agents", json={"name": "Raw agent"})).json()
        graph = self.flow_graph()
        graph["nodes"].append({"id": "agent1", "type": "agent", "position": {"x": 400, "y": 100},
                               "data": {"definition": {"id": agent["id"]}}})
        graph["edges"] = [{"id": "e1", "source": "api-input", "target": "agent1"},
                          {"id": "e2", "source": "agent1", "target": "api-response"}]
        self.studio_model = SimpleNamespace(complete=AsyncMock(return_value="  Exact output\n"))
        app.state.studio.model = self.studio_model
        app.state.studio.traces = Mock()
        saved = (await self.client.post("/api/admin/flows", json={"name": "Agent flow", "graph": graph})).json()
        url = "/api/admin/flows/" + saved["id"] + "/run"
        response = await self.client.post(url, json={"input": "Raw request"})
        self.assertEqual((response.status_code, response.text), (200, "  Exact output\n"))
        self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(self.studio_model.complete.await_args.args[0], "You are a helpful agent\n\nRaw request")
        app.state.studio.traces.save.assert_called_once()
        self.studio_model.complete.side_effect = RuntimeError("Internal model details")
        failed = await self.client.post(url, json={"input": "Raw request"})
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn("Internal model details", failed.text)

    async def test_flow_draft_validation_and_unconfigured_agents(self):
        graph = self.flow_graph()
        graph["nodes"].pop()
        bad = await self.client.post("/api/admin/flows", json={"name": "Missing response", "graph": graph})
        self.assertEqual(bad.status_code, 422)
        graph = self.flow_graph()
        graph["nodes"].append({"id": "agent1", "type": "agent", "position": {"x": 400, "y": 100},
                               "data": {"label": "Unconfigured agent", "definition": None}})
        graph["edges"] = [{"id": "e1", "source": "api-input", "target": "agent1"},
                          {"id": "e2", "source": "agent1", "target": "api-response"}]
        saved = await self.client.post("/api/admin/flows", json={"name": "Work in progress", "graph": graph})
        self.assertEqual(saved.status_code, 201, saved.text)
        response = await self.client.post("/api/admin/flows/" + saved.json()["id"] + "/run", json={"input": "hi"})
        self.assertEqual(response.status_code, 422)
        graph["edges"].append({"id": "e3", "source": "api-input", "target": "api-response"})
        self.assertEqual((await self.client.post("/api/admin/flows", json={"name": "Bad wire", "graph": graph})).status_code, 422)

    async def test_flow_agent_chain_and_cycle_validation(self):
        agent = (await self.client.post("/api/admin/agents", json=self.body)).json()
        graph = self.flow_graph()
        for node_id in ("agent1", "agent2"):
            graph["nodes"].append({"id": node_id, "type": "agent", "position": {"x": 400, "y": 100},
                                   "data": {"definition": {"id": agent["id"]}}})
        graph["edges"] = [{"id": "e1", "source": "api-input", "target": "agent1"},
                          {"id": "e2", "source": "agent1", "target": "agent2"},
                          {"id": "e3", "source": "agent2", "target": "api-response"}]
        model = SimpleNamespace(complete=AsyncMock(side_effect=["Intermediate", "Final raw text"]))
        app.state.studio.model, app.state.studio.traces = model, Mock()
        saved = (await self.client.post("/api/admin/flows", json={"name": "Chain", "graph": graph})).json()
        url = "/api/admin/flows/" + saved["id"]
        response = await self.client.post(url + "/run", json={"input": "Original"})
        self.assertEqual((response.status_code, response.text), (200, "Final raw text"))
        self.assertEqual(model.complete.await_args_list[1].args[0], "Be helpful.\n\nIntermediate")
        graph["edges"][0] = {"id": "e1", "source": "agent2", "target": "agent1"}
        updated = await self.client.put(url, json={"name": "Cycle", "graph": graph, "revision": 1})
        self.assertEqual(updated.status_code, 200)
        model.complete.reset_mock()
        response = await self.client.post(url + "/run", json={"input": "Original"})
        self.assertEqual(response.status_code, 422)
        model.complete.assert_not_awaited()
        graph["edges"].append({"id": "e4", "source": "agent1", "target": "api-response"})
        self.assertEqual((await self.client.post("/api/admin/flows", json={"name": "Ambiguous response", "graph": graph})).status_code, 422)

    async def test_json_multiple_inputs_and_connection_ids_round_trip(self):
        agent = (await self.client.post("/api/admin/agents", json=self.body)).json()
        graph = self.flow_graph()
        graph["nodes"].extend([
            {"id": "agent1", "type": "agent", "position": {"x": 200, "y": 100},
             "data": {"definition": {"id": agent["id"]}}},
            {"id": "json1", "type": "json", "position": {"x": 400, "y": 100}, "data": {"label": "JSON"}},
        ])
        graph["edges"] = [
            {"id": "request-to-agent", "source": "api-input", "target": "agent1"},
            {"id": "request-input", "source": "api-input", "target": "json1"},
            {"id": "agent-output", "source": "agent1", "target": "json1"},
            {"id": "json-response", "source": "json1", "target": "api-response"},
        ]
        saved = await self.client.post("/api/admin/flows", json={"name": "JSON design", "graph": graph})
        self.assertEqual(saved.status_code, 201, saved.text)
        url = "/api/admin/flows/" + saved.json()["id"]
        opened = (await self.client.get(url)).json()
        self.assertEqual([{key: edge[key] for key in ("id", "source", "target")}
                          for edge in opened["graph"]["edges"]], graph["edges"])
        self.assertEqual(opened["graph"]["nodes"][-1]["type"], "json")
        app.state.studio.model = SimpleNamespace(complete=AsyncMock())
        failed = await self.client.post(url + "/run", json={"input": "request"})
        self.assertEqual(failed.status_code, 422)
        self.assertIn("JSON", failed.json()["detail"])
        app.state.studio.model.complete.assert_not_awaited()
        graph["edges"] = [graph["edges"][1], graph["edges"][3]]
        graph["nodes"][-1]["data"]["label"] = "Renamed JSON"
        updated = await self.client.put(url, json={"name": "JSON design", "graph": graph, "revision": 1})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual([edge["id"] for edge in updated.json()["graph"]["edges"]], ["request-input", "json-response"])

    async def test_json_preview_saved_chain_and_agent_boundaries(self):
        graph = self.flow_graph()
        agent = (await self.client.post("/api/admin/agents", json={"name": "Writer"})).json()
        mapping = {"base": {"session": {"id": None}, "reply": "", "phase": "open"}, "mappings": [
            {"edgeId": "request", "source": ".json/meta/id", "target": "/session/id"},
            {"edgeId": "reply", "source": ".text", "target": "/reply"},
        ]}
        final = {"base": {"snapshot": None}, "mappings": [
            {"edgeId": "snapshot", "source": ".json", "target": "/snapshot"}]}
        graph["nodes"] += [
            {"id": "agent", "type": "agent", "position": {"x": 1, "y": 1}, "data": {"definition": {"id": agent["id"]}}},
            {"id": "state", "type": "json", "position": {"x": 2, "y": 2}, "data": {"objectDefinition": mapping}},
            {"id": "wrapped", "type": "json", "position": {"x": 3, "y": 3}, "data": {"objectDefinition": final}},
        ]
        graph["edges"] = [{"id": edge, "source": source, "target": target} for edge, source, target in [
            ("start", "api-input", "agent"), ("request", "api-input", "state"), ("reply", "agent", "state"),
            ("snapshot", "state", "wrapped"), ("end", "wrapped", "api-response")]]
        payload = {"meta": {"id": 42}}
        answer = "  reply\n"
        model = SimpleNamespace(complete=AsyncMock(return_value=answer))
        app.state.studio.model, app.state.studio.traces = model, Mock()
        preview = await self.client.post("/api/admin/flows/preview-json", json={
            "definition": mapping, "inputs": {"request": payload, "reply": answer}})
        self.assertEqual(preview.json(), {"session": {"id": 42}, "reply": answer, "phase": "open"})
        model.complete.assert_not_awaited()
        saved = await self.client.post("/api/admin/flows", json={"name": "Session envelope", "graph": graph})
        self.assertEqual(saved.status_code, 201, saved.text)
        url = "/api/admin/flows/" + saved.json()["id"]
        opened = (await self.client.get(url)).json()
        self.assertEqual(opened["graph"]["nodes"][-2]["data"]["objectDefinition"], mapping)
        actual = await self.client.post(url + "/run", json={"input": payload})
        self.assertEqual(actual.status_code, 200, actual.text)
        self.assertEqual(actual.headers["content-type"], "application/json")
        self.assertEqual(actual.json(), {"snapshot": preview.json()})
        self.assertIn('{"meta":{"id":42}}', model.complete.await_args.args[0])

        # A stale mapping cannot read an unrelated edge, even if its ID exists in the graph.
        mapping["mappings"][0]["edgeId"] = "start"
        updated = await self.client.put(url, json={"name": "Incomplete draft", "revision": 1, "graph": graph})
        self.assertEqual(updated.status_code, 200, updated.text)
        model.complete.reset_mock()
        invalid = await self.client.post(url + "/run", json={"input": payload})
        self.assertEqual(invalid.status_code, 422)
        model.complete.assert_not_awaited()

    async def test_named_wire_bindings_survive_rename_and_parse_explicitly(self):
        graph = self.flow_graph()
        agent = (await self.client.post("/api/admin/agents", json={"name": "Classifier"})).json()
        mapping = {"base": {"Category": None, "Text": None}, "mappings": [
            {"edgeId": "stable-A", "source": ".value/category", "target": "/Category"},
            {"edgeId": "stable-B", "source": ".value", "target": "/Text"},
        ]}
        graph["nodes"] += [
            {"id": "classifier", "type": "agent", "position": {"x": 1, "y": 1}, "data": {"definition": agent}},
            {"id": "contract", "type": "json", "position": {"x": 2, "y": 2}, "data": {"objectDefinition": mapping}},
        ]
        graph["edges"] = [
            {"id": "start", "source": "api-input", "target": "classifier", "name": "request"},
            {"id": "stable-B", "source": "api-input", "target": "contract", "name": "request"},
            {"id": "stable-A", "source": "classifier", "target": "contract", "name": "classification", "readAs": "json"},
            {"id": "end", "source": "contract", "target": "api-response"},
        ]
        app.state.studio.model = SimpleNamespace(complete=AsyncMock(return_value='{"category":42}'))
        app.state.studio.traces = Mock()
        saved = await self.client.post("/api/admin/flows", json={"name": "Classifier", "graph": graph})
        self.assertEqual(saved.status_code, 201, saved.text)
        url = "/api/admin/flows/" + saved.json()["id"]
        raw = '  {"keep":"as text"}\n'
        expected = {"Category": 42, "Text": raw}
        graph["edges"][2]["name"] = "request"
        body = {"name": "Classifier", "graph": graph, "revision": 1}
        self.assertEqual((await self.client.put(url, json=body)).status_code, 422)
        graph["edges"][2]["name"] = "result"
        self.assertEqual((await self.client.put(url, json=body)).status_code, 200)
        opened = (await self.client.get(url)).json()["graph"]
        self.assertEqual((opened["edges"][2]["name"], opened["edges"][2]["id"]), ("result", "stable-A"))
        self.assertEqual(opened["nodes"][-1]["data"]["objectDefinition"], mapping)
        self.assertEqual((await self.client.post(url + "/run", json={"input": raw})).json(), expected)
        preview = {"definition": mapping, "inputs": {"stable-A": '{"category":42}', "stable-B": raw}}
        self.assertEqual((await self.client.post("/api/admin/flows/preview-json", json=preview)).status_code, 422)
        preview["readAs"] = {"stable-A": "json"}
        self.assertEqual((await self.client.post("/api/admin/flows/preview-json", json=preview)).json(), expected)
