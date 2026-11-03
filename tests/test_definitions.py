import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx
from prompt_studio import PromptLibrary, SQLAlchemyStorage
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from agent_store import create_schema, definitions, metadata, seed_defaults
from app import app
from auth import require_admin_access, require_chat_access, require_mcp_access


@unittest.skipUnless(os.getenv("LYRA_TEST_DATABASE_URL"), "Set LYRA_TEST_DATABASE_URL for isolated PostgreSQL checks")
class AgentDefinitionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = "lyra_definition_test_" + uuid4().hex
        self.storage = SQLAlchemyStorage.from_postgres_url(os.environ["LYRA_TEST_DATABASE_URL"])
        self.engine = self.storage.engine
        with self.engine.begin() as connection:
            connection.execute(CreateSchema(self.schema))
        self.addCleanup(self.cleanup_database)
        self.storage.engine = self.engine.execution_options(schema_translate_map={
            "agent_studio": self.schema, "prompt_studio": self.schema,
        })
        metadata.create_all(self.storage.engine)
        self.library = PromptLibrary(self.storage, "test")
        seed_defaults(self.library)
        self.library.add("instructions", "Be helpful.")
        self.library.add("request", "{{ request }}")
        studio = SimpleNamespace(prompts=SimpleNamespace(library=self.library))
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

    async def test_existing_schema_upgrades_without_changing_template_agents(self):
        created = (await self.client.post("/api/admin/agents", json=self.body)).json()
        table = self.schema + ".agent_definitions"
        with self.engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table} DROP COLUMN output_schema"))
            connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN prompt_name SET NOT NULL, "
                                    "ALTER COLUMN prompt_version SET NOT NULL"))
            connection.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT agent_prompt_reference_complete"))
        create_schema(self.storage.engine)
        create_schema(self.storage.engine)
        response = await self.client.post("/api/admin/agents", json={"name": "New raw agent"})
        self.assertEqual(response.status_code, 201, response.text)
        catalog = (await self.client.get("/api/admin/catalog")).json()
        old = next(agent for agent in catalog["agents"] if agent["id"] == created["id"])
        self.assertEqual(old["prompt"], self.body["prompt"])
        self.assertIsNone(old["outputSchema"])
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
