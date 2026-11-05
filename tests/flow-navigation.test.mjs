import assert from "node:assert/strict";
import test from "node:test";
import { draftSession, prepareFlowView } from "../static/admin/navigation.js";
import { openGraph } from "../flow-editor/graph.ts";

test("returning through Flow View restores the last layout in this tab", async () => {
  const storage = new Map();
  globalThis.sessionStorage = {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
  };
  globalThis.history = { replaceState: (_, __, path) => { location.href = new URL(path, location).href; } };
  async function visit(query = "", account = "admin") {
    globalThis.window?.dispatchEvent(new Event("pagehide"));
    globalThis.window = new EventTarget();
    globalThis.location = new URL("https://lyra.test/admin/flow/" + query);
    await prepareFlowView(account, async () => ({ id: "active-flow" }));
    return draftSession();
  }

  const first = await visit();
  assert.equal(location.searchParams.get("id"), "active-flow");
  const draft = await visit("?new=1");
  const { nodes, wires } = openGraph();
  nodes[0].position = { x: -84.5, y: 122 };
  nodes[1].position = { x: 425, y: -40.25 };
  const viewport = { x: 45, y: -200, zoom: 0.75 };
  draft.save({ items: nodes, edges: wires, viewport, personalityMode: "wired", memoryMode: "wired" });

  const resumed = await visit();
  assert.equal(resumed.id, draft.id);
  assert.equal(location.searchParams.has("id"), false);
  const data = resumed.data;
  assert.deepEqual(openGraph(data.items, data.edges, data.personalityMode, data.memoryMode).nodes, nodes);
  assert.deepEqual(data.viewport, viewport);

  // Saving a new flow assigns its server ID after mount.
  location.searchParams.set("id", "saved-flow");
  assert.equal((await visit()).id, draft.id);
  assert.equal(location.searchParams.get("id"), "saved-flow");
  assert.notEqual((await visit("?id=other&run=trace")).id, draft.id);
  assert.equal(location.searchParams.get("id"), "other");
  assert.equal((await visit("?draft=" + draft.id)).id, draft.id);
  assert.equal((await visit("", "another-admin")).data, null);
  assert.equal(location.searchParams.get("id"), "active-flow");
  assert.notEqual(first.id, draft.id);
});
