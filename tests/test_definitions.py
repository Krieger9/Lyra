import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
from prompt_studio import PromptLibrary, SQLAlchemyStorage
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from agent_definitions import create_schema, definitions, metadata, seed_defaults
from app import app
from auth import require_admin_access


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
        with self.assertRaises(IntegrityError):
            with self.storage.engine.begin() as connection:
                connection.execute(definitions.update().where(
                    definitions.c.name == "New raw agent").values(prompt_name="request", prompt_version=None))
