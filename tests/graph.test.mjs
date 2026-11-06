import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { allowsConnection, graphDocument, hydrateGraph } from "../flow-editor/graph.ts";

const snapshot = JSON.parse(readFileSync(new URL("./fixtures/router-snapshot.json", import.meta.url)));
const catalog = Object.values(snapshot.agents);

test("flow prompt defaults survive save and reopen while agent overrides remain on definitions", () => {
  const graph = {...snapshot.graph, promptSetId: "flow-set"};
  const scoped = catalog.map(agent => ({...agent, promptSetId: "agent-set"}));
  const {nodes, wires} = hydrateGraph(graph, scoped);
  const saved = graphDocument(nodes, wires, graph.viewport, graph.panelSize, graph.stateObject, graph.promptSetId);
  assert.equal(saved.promptSetId, "flow-set");
  const reopened = hydrateGraph(saved, scoped);
  assert.equal(reopened.nodes.find(node => node.type === "agent").data.definition.promptSetId, "agent-set");
  assert.equal(graphDocument(nodes, wires, graph.viewport, graph.panelSize, {}, null).promptSetId, undefined);
});

test("saving and reopening a moved layout preserves every node's coordinates by ID", () => {
  const { nodes, wires } = hydrateGraph(snapshot.graph, catalog);
  const moved = nodes.map((node, i) => ({ ...node, position: { x: -230.75 + i * 29, y: 415.5 - i * 91 } }));
  const positions = items => Object.fromEntries(items.map(node => [node.id, node.position]));
  const viewport = { x: -168, y: 302.5, zoom: 0.625 };
  const saved = JSON.parse(JSON.stringify(graphDocument(moved, wires, viewport, snapshot.graph.panelSize)));
  assert.deepEqual(positions(hydrateGraph(saved, catalog).nodes), positions(moved));
  assert.deepEqual(saved.viewport, viewport);
});

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
  assert.equal(saved.nodes.some(n => n.type === "recall"), false);
  assert.equal(saved.nodes.find(n => n.type === "apiInput").data.personalityId, undefined);
  const personality = nodes.find(n => n.type === "personality");
  assert.equal(personality.data.personalityId, "personality-1");
  assert.equal(personality.deletable, true);
  assert.equal(allowsConnection(nodes, wires, {source: personality.id, target: "api-response"}), false);
  personality.position = {x: 35, y: 200};
  personality.data.personalityId = null;
  const cleared = graphDocument(nodes, wires, graph.viewport, graph.panelSize);
  const reopened = hydrateGraph(cleared, catalog).nodes.filter(n => n.type === "personality");
  assert.equal(reopened.length, 1);
  assert.deepEqual(reopened[0].position, personality.position);
  assert.equal(reopened[0].data.personalityId, undefined);
  assert.equal(wires.some(e => e.targetHandle === "personality"), false);
  assert.deepEqual(saved.nodes.find(n => n.type === "agent").data.inputSelection, {mode: "recent", turns: 3});
});

test("legacy opt-ins become profile wires and a saved disconnection stays disconnected", () => {
  const graph = structuredClone(snapshot.graph);
  const profile = {id: "voice", type: "personality", position: {x: 0, y: 200},
    data: {label: "Writer voice", personalityId: "shared"}};
  const agent = graph.nodes.find(n => n.type === "agent");
  graph.nodes.push(profile, {...agent, id: "neutral", data: {...agent.data, usePersonality: false}});
  const {nodes, wires} = hydrateGraph(graph, catalog);
  const refs = wires.filter(e => e.targetHandle === "personality");
  assert.deepEqual(refs.map(e => [e.source, e.target]), [["voice", agent.id]]);
  nodes.push({...profile, id: "also-voice"});
  assert.equal(allowsConnection(nodes, wires, {...refs[0], source: "also-voice"}), false);
  assert.equal(allowsConnection(nodes, wires, {...refs[0], target: "neutral"}), true);
  assert.equal(allowsConnection(nodes, wires, {...refs[0], source: "api-input", target: "neutral"}), false);
  assert.equal(allowsConnection(nodes, wires, {...refs[0], targetHandle: null}), false);
  const save = edges => graphDocument(nodes, edges, graph.viewport, graph.panelSize);
  assert.deepEqual(hydrateGraph(save(wires), catalog).wires.filter(e => e.targetHandle)[0].id, refs[0].id);
  const disconnected = hydrateGraph(save(wires.filter(e => !e.targetHandle)), catalog);
  assert.equal(disconnected.nodes.filter(n => n.type === "personality").length, 2);
  assert.equal(disconnected.wires.some(e => e.targetHandle), false);
  const single = save(wires.filter(e => !e.targetHandle));
  single.nodes = single.nodes.filter(n => n.id !== "also-voice");
  assert.equal(hydrateGraph(single, catalog).wires.some(e => e.targetHandle), false);
});

test("reopen and save the captured router without losing wire IDs, ports or agent references", () => {
  const { nodes, wires } = hydrateGraph(snapshot.graph, catalog);
  const save = (nodes, wires) => graphDocument(nodes, wires, snapshot.graph.viewport, snapshot.graph.panelSize);
  const saved = save(nodes, wires);
  assert.deepEqual(saved.nodes.find(n => n.type === "agent").data.definition, { id: catalog[0].id });
  assert.deepEqual(saved.nodes.find(n => n.type === "switch").data.switchDefinition,
    snapshot.graph.nodes.find(n => n.type === "switch").data.switchDefinition);
  const recall = nodes.find(n => n.type === "recall");
  assert.ok(recall.data.includeQueryState);
  assert.ok(saved.edges.some(e => e.source === "api-input" && e.target === recall.id));
  assert.ok(saved.edges.some(e => e.source === recall.id && e.target === "agent" && e.targetHandle === "memories"));
  const originalEdges = saved.edges.filter(e => e.source !== recall.id && e.target !== recall.id);
  assert.deepEqual(originalEdges.map(e => [e.id, e.source, e.target, e.sourceHandle ?? null]),
    snapshot.graph.edges.map(e => [e.id, e.source, e.target, e.sourceHandle]));
  assert.deepEqual(originalEdges.map(e => e.name), ["input1", "input1", "input1", "input2"]);
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

test("legacy State capture migrates once and disconnected memory ports stay disconnected", () => {
  const state = {id: "context", type: "state", position: {x: 3, y: 4}, data: {label: "Context",
    stateDefinition: {operation: "generate", includeChatTurn: true, agentId: catalog[0].id}}};
  const graph = {...snapshot.graph, nodes: [...snapshot.graph.nodes, state]};
  const {nodes, wires} = hydrateGraph(graph, catalog);
  const recall = nodes.find(n => n.type === "recall");
  const remember = nodes.find(n => n.type === "remember");
  assert.equal(remember.data.extractorAgentId, catalog[0].id);
  assert.equal(remember.data.memoryEvidence, "exchange");
  assert.ok(wires.some(e => e.source === state.id && e.target === remember.id));
  const connection = {source: recall.id, target: state.id, targetHandle: "memories"};
  const connected = wires.find(e => e.target === state.id && e.targetHandle === "memories");
  assert.ok(connected);
  assert.equal(allowsConnection(nodes, wires, connection), false);
  const disconnected = wires.filter(e => e.id !== connected.id);
  assert.equal(allowsConnection(nodes, disconnected, connection), true);
  assert.equal(allowsConnection(nodes, disconnected, {...connection, source: "api-input"}), false);
  const saved = graphDocument(nodes, disconnected, graph.viewport, graph.panelSize);
  const reopened = hydrateGraph(saved, catalog);
  assert.deepEqual(graphDocument(reopened.nodes, reopened.wires, graph.viewport, graph.panelSize), saved);
});
