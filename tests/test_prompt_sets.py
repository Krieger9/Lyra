"""Exercise prompt-set persistence using the isolated PostgreSQL fixture."""
import os
import unittest
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from prompt_studio import PromptLibrary, StorageConflictError

import test_definitions as fixtures
from app import app
from design_session import DesignSession
from flow_graph import snapshot


class PromptSetToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_designer_publishes_json_serializable_set_reference(self):
        set_id = str(uuid4())
        dispatch = AsyncMock(return_value={"status": 201, "data": {"name": "new", "version": 1}})
        session = DesignSession(None, dispatch=dispatch)
        result = await session.call("publish_prompt", {"name": "new", "text": "Hello", "setId": set_id})
        self.assertEqual(result["status"], 201)
        self.assertEqual(dispatch.await_args.args[0].body["setId"], set_id)


@unittest.skipUnless(os.getenv("LYRA_TEST_DATABASE_URL"), "Set LYRA_TEST_DATABASE_URL for PostgreSQL checks")
class PromptSetTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AgentDefinitionTests.asyncSetUp
    cleanup_database = fixtures.AgentDefinitionTests.cleanup_database
    migrate = fixtures.AgentDefinitionTests.migrate

    async def test_flow_defaults_and_agent_overrides_round_trip(self):
        flow_set = (await self.client.post("/api/admin/prompt-sets", json={"name": "Flow default"})).json()["id"]
        agent_set = (await self.client.post("/api/admin/prompt-sets", json={"name": "Agent override"})).json()["id"]
        inherited = (await self.client.post("/api/admin/agents", json=self.body)).json()
        self.assertIsNone(inherited["promptSetId"])
        overridden = (await self.client.post("/api/admin/agents", json=self.body | {"promptSetId": agent_set})).json()
        self.assertEqual(overridden["promptSetId"], agent_set)
        graph = fixtures.AgentDefinitionTests.flow_graph(self) | {"promptSetId": flow_set}
        graph["nodes"].append({"id": "agent", "type": "agent", "position": {"x": 300, "y": 100},
                               "data": {"definition": {"id": overridden["id"]}}})
        created = await self.client.post("/api/admin/flows", json={"name": "Scoped", "graph": graph})
        self.assertEqual(created.status_code, 201, created.text)
        flow = created.json()
        read = await self.client.get("/api/admin/flows/" + flow["id"])
        self.assertEqual(read.json()["graph"]["promptSetId"], flow_set)
        document = snapshot(self.library, flow["id"])
        self.assertEqual(str(document.graph.promptSetId), flow_set)
        self.assertEqual(str(document.agents[overridden["id"]].promptSetId), agent_set)
        # Older clients omitting the agent field keep its stored override.
        updated = await self.client.put("/api/admin/agents/" + overridden["id"], json=self.body | {"revision": 1})
        self.assertEqual(updated.json()["promptSetId"], agent_set)
        self.assertEqual(updated.json()["prompt"], overridden["prompt"])
        restored = await self.client.put("/api/admin/agents/" + overridden["id"],
            json=self.body | {"revision": 2, "promptSetId": None})
        self.assertIsNone(restored.json()["promptSetId"])
        # The inherited choice is not copied into the reusable agent.
        graph["promptSetId"] = agent_set
        response = await self.client.put("/api/admin/flows/" + flow["id"],
            json={"name": "Scoped", "graph": graph, "revision": 1})
        self.assertEqual(response.status_code, 200, response.text)
        document = snapshot(self.library, flow["id"])
        self.assertEqual(str(document.graph.promptSetId), agent_set)
        self.assertIsNone(document.agents[overridden["id"]].promptSetId)
        self.assertEqual(document.agents[overridden["id"]].prompt.version, 1)
        graph["promptSetId"] = None
        cleared = await self.client.put("/api/admin/flows/" + flow["id"],
            json={"name": "Scoped", "graph": graph, "revision": 2})
        self.assertIsNone(cleared.json()["graph"]["promptSetId"])

    async def test_flow_and_agent_sets_reject_missing_and_cross_library_references(self):
        from prompt_sets import sets
        foreign = uuid4()
        with self.storage.engine.begin() as connection:
            connection.execute(sets.insert().values(id=foreign, library="other", name="Other library"))
        graph = fixtures.AgentDefinitionTests.flow_graph(self)
        agent = (await self.client.post("/api/admin/agents", json=self.body)).json()
        flow = (await self.client.post("/api/admin/flows", json={"name": "Existing", "graph": graph})).json()
        for set_id in (str(foreign), str(uuid4())):
            self.assertEqual((await self.client.post("/api/admin/agents", json=self.body | {"promptSetId": set_id})).status_code, 422)
            self.assertEqual((await self.client.put("/api/admin/agents/" + agent["id"],
                json=self.body | {"promptSetId": set_id, "revision": 1})).status_code, 422)
            body = {"name": "Invalid", "graph": graph | {"promptSetId": set_id}}
            self.assertEqual((await self.client.post("/api/admin/flows", json=body)).status_code, 422)
            self.assertEqual((await self.client.put("/api/admin/flows/" + flow["id"],
                json=body | {"revision": 1})).status_code, 422)
        self.assertEqual((await self.client.get("/api/admin/flows/" + flow["id"])).json()["revision"], 1)

    async def test_prompt_set_lifecycle(self):
        url = "/api/admin/prompt-sets"
        created = await self.client.post(url, json={"name": " New flow "})
        self.assertEqual(created.status_code, 201, created.text)
        new_set = created.json()
        self.assertEqual(new_set["name"], "New flow")
        self.assertEqual(new_set["prompts"], [])
        self.assertEqual((await self.client.post(url, json={"name": "New flow"})).status_code, 409)
        self.assertEqual((await self.client.post(url, json={"name": " "})).status_code, 422)
        old_set = (await self.client.post(url, json={"name": "Old flow"})).json()
        body = {"name": "new-input", "text": "New input", "setId": new_set["id"]}
        published = await self.client.post("/api/admin/prompts", json=body)
        self.assertEqual(published.status_code, 201, published.text)
        self.assertEqual(published.json()["version"], 1)
        # Existing clients can still publish without a set; membership covers future versions.
        await self.client.post("/api/admin/prompts", json={"name": "new-input", "text": "Revision"})
        member_url = url + "/" + new_set["id"] + "/members"
        response = await self.client.put(member_url, json={"names": ["instructions", "instructions"]})
        self.assertEqual(response.status_code, 200, response.text)
        await self.client.put(url + "/" + old_set["id"] + "/members", json={"names": ["instructions"]})
        catalog = (await self.client.get("/api/admin/catalog")).json()
        saved_sets = {s["id"]: s for s in catalog["promptSets"]}
        self.assertEqual(saved_sets[new_set["id"]]["prompts"], ["instructions", "new-input"])
        self.assertEqual(saved_sets[old_set["id"]]["prompts"], ["instructions"])
        self.assertEqual((await self.client.put(member_url, json={"names": ["request", "missing"]})).status_code, 422)
        response = await self.client.put(member_url, json={"names": ["instructions"], "remove": True})
        self.assertEqual(response.status_code, 200)
        result = await self.client.get(url)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.assertEqual(next(s for s in result.json() if s["id"] == new_set["id"])["prompts"], ["new-input"])
        self.assertEqual(self.library.get("instructions").version, 1)
        self.assertEqual(self.library.get("new-input").version, 2)
        self.migrate()
        self.assertEqual((await self.client.get(url)).json(), result.json())

    async def test_missing_cross_library_and_failed_publication(self):
        url = "/api/admin/prompt-sets"
        created = (await self.client.post(url, json={"name": "Private"})).json()
        other = PromptLibrary(self.storage, "other")
        with patch.object(app.state, "library", other):
            self.assertEqual((await self.client.get(url)).json(), [])
            self.assertEqual((await self.client.put(url + "/" + created["id"] + "/members",
                json={"names": ["instructions"]})).status_code, 404)
            response = await self.client.post("/api/admin/prompts",
                json={"name": "leak", "text": "No", "setId": created["id"]})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(other.list_prompts(), [])
        response = await self.client.post("/api/admin/prompts",
            json={"name": "missing-set", "text": "No", "setId": str(uuid4())})
        self.assertEqual(response.status_code, 404)
        with patch.object(self.library, "add", side_effect=StorageConflictError("conflict")):
            response = await self.client.post("/api/admin/prompts",
                json={"name": "failed", "text": "No", "setId": created["id"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual((await self.client.get(url)).json()[0]["prompts"], [])
