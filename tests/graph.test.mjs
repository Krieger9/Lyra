import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { allowsConnection, graphDocument, hydrateGraph } from "../flow-editor/graph.ts";

const snapshot = JSON.parse(readFileSync(new URL("./fixtures/router-snapshot.json", import.meta.url)));
const catalog = Object.values(snapshot.agents);

test("State declarations and mapping settings round-trip and accept multiple inputs", () => {
  const state = { id: "context", type: "state", position: {x: 3, y: 4}, data: {label: "Context",
    stateDefinition: {scope: "conversation", key: "context", operation: "map", initial: {},
      mappings:[{edgeId:"trigger", source:".value", target:"/goal"}], output:"property", outputProperty:"goal"}} };
  const graph = {...snapshot.graph, stateObject:{goal:""}, nodes: [...snapshot.graph.nodes, state]};
  const {nodes, wires} = hydrateGraph(graph, catalog);
  const saved = graphDocument(nodes, wires, graph.viewport, graph.panelSize, graph.stateObject);
  assert.deepEqual(saved.stateObject, graph.stateObject);
  assert.deepEqual(saved.nodes.at(-1)?.type, "apiResponse");
  assert.deepEqual(saved.nodes.find(n => n.id === state.id).data.stateDefinition, state.data.stateDefinition);
  const input = {id: "trigger", source: "api-input", target: state.id};
  assert.equal(allowsConnection(nodes, wires, input), true);
  assert.equal(allowsConnection(nodes, [...wires, input], {...input, source: state.id}), false);
  const agent = nodes.find(n => n.type === "agent");
  assert.equal(allowsConnection(nodes, [...wires, input], {...input, source: agent.id}), true);
  assert.equal(allowsConnection(nodes, wires, {source: state.id, target: "api-response"}), true);
  assert.equal(allowsConnection(nodes, wires, {source: "api-response", target: state.id}), true);
  assert.equal(allowsConnection(nodes, wires, {source: "api-response", target: agent.id}), false);
});

test("chat input and per-node history selection survive reopening a saved flow", () => {
  const graph = structuredClone(snapshot.graph);
  graph.nodes.find(n => n.type === "apiInput").data.chatInput = true;
  graph.nodes.find(n => n.type === "apiInput").data.recallMemories = false;
  graph.nodes.find(n => n.type === "apiInput").data.personalityId = "personality-1";
  graph.nodes.find(n => n.type === "agent").data.usePersonality = false;
  graph.nodes.find(n => n.type === "agent").data.inputSelection = { mode: "recent", turns: 3 };
  const {nodes, wires} = hydrateGraph(graph, catalog);
  const saved = graphDocument(nodes, wires, graph.viewport, graph.panelSize);
  assert.equal(saved.nodes.find(n => n.type === "apiInput").data.chatInput, true);
  assert.equal(saved.nodes.find(n => n.type === "apiInput").data.recallMemories, false);
  assert.equal(saved.nodes.find(n => n.type === "apiInput").data.personalityId, "personality-1");
  assert.equal(saved.nodes.find(n => n.type === "agent").data.usePersonality, false);
  assert.deepEqual(saved.nodes.find(n => n.type === "agent").data.inputSelection, {mode: "recent", turns: 3});
});

test("reopen and save the captured router without losing wire IDs, ports or agent references", () => {
  const { nodes, wires } = hydrateGraph(snapshot.graph, catalog);
  const save = (nodes, wires) => graphDocument(nodes, wires, snapshot.graph.viewport, snapshot.graph.panelSize);
  const saved = save(nodes, wires);
  assert.deepEqual(saved.nodes.find(n => n.type === "agent").data.definition, { id: catalog[0].id });
  assert.deepEqual(saved.nodes.find(n => n.type === "switch").data.switchDefinition,
    snapshot.graph.nodes.find(n => n.type === "switch").data.switchDefinition);
  assert.deepEqual(saved.edges.map(e => [e.id, e.source, e.target, e.sourceHandle ?? null]),
    snapshot.graph.edges.map(e => [e.id, e.source, e.target, e.sourceHandle]));
  assert.deepEqual(saved.edges.map(e => e.name), ["input1", "input1", "input1", "input2"]);
  const reopened = hydrateGraph(saved, catalog);
  assert.deepEqual(save(reopened.nodes, reopened.wires), saved);
});

test("switch wires require a valid port and only the wire being replaced is exempt from duplicate checks", () => {
  const { nodes, wires } = hydrateGraph(snapshot.graph, catalog);
  const wire = wires.find(e => e.sourceHandle === "chat-id");
  assert.equal(allowsConnection(nodes, wires, wire), false);
  assert.equal(allowsConnection(nodes, wires, wire, wire.id), true);
  assert.equal(allowsConnection(nodes, wires, { ...wire, sourceHandle: "deleted" }, wire.id), false);
  assert.equal(allowsConnection(nodes, wires, { source: "agent", target: "switch" }), false);
});
