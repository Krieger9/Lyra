import type { Connection, Edge, Node, Viewport } from "@xyflow/react";
import type { AgentDefinition } from "./catalog";
import type { JsonNodeData } from "./json-mapping";
import type { SwitchItem } from "./switch";
import type { StateItem } from "./state";
import type { InputSelection } from "./chat-input";

export type GenericItem = Node<{ label: string }, "item">;
export type JsonItem = Node<JsonNodeData, "json">;
export type ApiInput = Node<{ label: string; chatInput?: boolean; recallMemories?: boolean; personalityId?: string | null }, "apiInput">;
export type ApiResponse = Node<{ label: string }, "apiResponse">;
export type Personality = Node<{ label: string; personalityId?: string | null }, "personality">;
export type Designer = Node<{ label: string }, "designer">;
export type Recall = Node<{label: string; includeQueryState?: boolean}, "recall">;
export type Remember = Node<{label: string; rememberWhen?: "afterResponse" | "onEvidence";
  memoryEvidence?: "value" | "exchange"; extractorAgentId?: string | null}, "remember">;
export type Agent = Node<{ label: string; definition: AgentDefinition | null; inputSelection?: InputSelection | null; usePersonality?: boolean }, "agent">;
export type FlowItem = GenericItem | JsonItem | ApiInput | ApiResponse | Personality | Designer | Agent | SwitchItem | StateItem | Recall | Remember;
export type PanelSize = { width: number; height: number };
type SavedNode = Exclude<FlowItem, Agent> | Node<Omit<Agent["data"], "definition"> & { definition: { id: string } | null }, "agent">;
export type SavedGraph = { nodes: SavedNode[]; edges: Wire[]; viewport: Viewport; panelSize: PanelSize; stateObject?: Record<string, unknown>; personalityMode?: "wired" | "legacy"; memoryMode?: "wired" | "legacy" };

export type Wire = Edge & { name?: string | null; readAs?: "value" | "json" };

export function nextWireName(used: Set<string>) {
  let number = 1;
  while (used.has("input" + number)) number++;
  return "input" + number;
}

export function nameExistingWires(wires: Wire[]): Wire[] {
  const used = new Map<string, Set<string>>();
  for (const wire of wires) {
    if (!used.has(wire.target)) used.set(wire.target, new Set());
    if (wire.name) used.get(wire.target)!.add(wire.name);
  }
  return wires.map(wire => {
    if (wire.name || wire.targetHandle) return wire;
    const names = used.get(wire.target)!;
    const name = nextWireName(names);
    names.add(name);
    return { ...wire, name };
  });
}

export const otherwise = { id: "otherwise", label: "Otherwise" };
export const ports = (data: SwitchItem["data"]) => [...(data.switchDefinition?.paths ?? []), otherwise];

export function graphDocument(items: FlowItem[], edges: Wire[], viewport: Viewport, panelSize: PanelSize, stateObject = {}) {
  return {
    personalityMode: "wired" as const,
    memoryMode: "wired" as const,
    ...(Object.keys(stateObject).length ? {stateObject} : {}),
    nodes: items.map(node => ({
      id: node.id, type: node.type, position: node.position,
      data: { label: node.data.label, definition: node.type === "agent" && node.data.definition
        ? { id: node.data.definition.id } : null,
        ...(node.type === "apiInput" && node.data.chatInput ? { chatInput: true } : {}),
        ...(node.type === "recall" ? {includeQueryState: node.data.includeQueryState ?? false} : {}),
        ...(node.type === "remember" ? {rememberWhen: node.data.rememberWhen ?? "afterResponse",
          memoryEvidence: node.data.memoryEvidence ?? "value", extractorAgentId: node.data.extractorAgentId ?? null} : {}),
        ...(node.type === "personality" && node.data.personalityId ? { personalityId: node.data.personalityId } : {}),
        ...(node.type === "agent" && node.data.inputSelection ? { inputSelection: node.data.inputSelection } : {}),
        ...(node.type === "json" ? { objectDefinition: node.data.objectDefinition ?? null } : {}),
        ...(node.type === "state" ? { stateDefinition: node.data.stateDefinition } : {}),
        ...(node.type === "switch" ? { switchDefinition: node.data.switchDefinition } : {}) },
    })),
    edges: edges.map(edge => ({ id: edge.id, source: edge.source, target: edge.target, name: edge.name,
      ...(edge.sourceHandle ? {sourceHandle: edge.sourceHandle} : {}),
      ...(edge.targetHandle ? {targetHandle: edge.targetHandle} : {}), readAs: edge.readAs ?? "value" })),
    viewport, panelSize,
  };
}

// Only port IDs and upstream schemas affect the field picker. Editing a check
// or dragging a card should not invalidate an in-progress selection.
export function switchFieldGraph(id: string, items: FlowItem[], edges: Wire[], stateObject = {}) {
  edges = edges.filter(edge => !edge.targetHandle);
  const ancestors = new Set<string>();
  function visit(key: string) {
    if (ancestors.has(key)) return;
    ancestors.add(key);
    edges.filter(edge => edge.target === key).forEach(edge => visit(edge.source));
  }
  visit(id);
  const upstream = items.filter(node => ancestors.has(node.id) || node.type === "apiInput" || node.type === "apiResponse");
  const { nodes, edges: connections } = graphDocument(upstream, edges.filter(edge => ancestors.has(edge.target)),
    { x: 0, y: 0, zoom: 1 }, { width: 0, height: 0 });
  return { stateObject, nodes: nodes.map(node => ({ ...node, position: { x: 0, y: 0 }, data: { ...node.data, label: "",
    switchDefinition: node.data.switchDefinition && {
      paths: node.data.switchDefinition.paths.map(p => ({ id: p.id, label: "" }))
    } } })), edges: connections };
}

const startingNode: ApiInput = {
  id: "api-input", type: "apiInput", data: { label: "API Input" },
  position: { x: 112, y: 68 }, deletable: false, selected: true,
  ariaLabel: "API Input, flow start",
};
const responseNode: ApiResponse = {
  id: "api-response", type: "apiResponse", data: { label: "API Response" },
  position: { x: 700, y: 68 }, deletable: false, ariaLabel: "API Response, flow response",
};

export function receivesMemories(node: FlowItem) {
  return node.type === "agent" || (node.type === "state" && ["generate", "generateObject"].includes(node.data.stateDefinition.operation));
}

export function openGraph(nodes: FlowItem[] = [], edges: Wire[] = [], mode?: SavedGraph["personalityMode"], memoryMode?: SavedGraph["memoryMode"]) {
  const input = nodes.find(node => node.type === "apiInput");
  const response = nodes.find(node => node.type === "apiResponse");
  const { personalityId, recallMemories, ...inputData } = input?.data ?? startingNode.data;
  const start = { ...startingNode, ...input, id: startingNode.id, deletable: false, data: { ...inputData, ...startingNode.data } };
  function newPersonality(): Personality {
    const position = { x: start.position.x + 240, y: start.position.y + 110 };
    while (nodes.some(node => Math.abs(node.position.x - position.x) < 220 && Math.abs(node.position.y - position.y) < 100)) {
      position.y += 110;
    }
    let id = "personality";
    for (let i = 2; nodes.some(node => node.id === id); i++) id = "personality-" + i;
    return { id, type: "personality", position, data: { label: "Personality", personalityId } };
  }
  const cards = nodes.filter(node => node.type === "personality");
  if (!cards.length && personalityId && mode !== "wired") cards.push(newPersonality());
  const wires = nameExistingWires(edges);
  const legacyCard = cards[0];
  if (mode !== "wired" && legacyCard?.data.personalityId && cards.length === 1 && !edges.some(edge => edge.targetHandle === "personality")) {
    for (const node of nodes) {
      if (node.type === "agent" && node.data.usePersonality !== false) {
        wires.push({ id: crypto.randomUUID(), source: legacyCard.id, target: node.id, targetHandle: "personality" });
      }
    }
  }
  const memoryCards: (Recall | Remember)[] = [];
  function near(node: FlowItem) {
    const position = {x: node.position.x, y: node.position.y + 125};
    const occupied = [...nodes, ...cards, ...memoryCards];
    while (occupied.some(n => Math.abs(n.position.x - position.x) < 220 && Math.abs(n.position.y - position.y) < 100)) {
      position.y += 125;
    }
    return position;
  }
  if (input && memoryMode !== "wired" && !nodes.some(n => n.type === "recall" || n.type === "remember")) {
    if (recallMemories ?? true) {
      const id = crypto.randomUUID();
      memoryCards.push({id, type: "recall", position: near(input), data: {label: "Recall", includeQueryState: true}});
      wires.push({id: crypto.randomUUID(), source: input.id, target: id, name: "query"});
      for (const target of nodes.filter(receivesMemories)) {
        wires.push({id: crypto.randomUUID(), source: id, target: target.id, targetHandle: "memories"});
      }
    }
    for (const state of nodes) {
      if (state.type !== "state" || !receivesMemories(state)) continue;
      const def = state.data.stateDefinition;
      if (!def.includeChatTurn || def.captureMemories === false) continue;
      const id = crypto.randomUUID();
      memoryCards.push({id, type: "remember", position: near(state), data: {label: "Remember", memoryEvidence: "exchange",
        rememberWhen: "afterResponse", extractorAgentId: def.agentId}});
      wires.push({id: crypto.randomUUID(), source: state.id, target: id, name: "evidence"});
    }
  }
  return {nodes: [
    start,
    ...cards.map(card => ({ ...card, deletable: true, ariaLabel: card.data.label || "Personality" })),
    ...memoryCards,
    ...nodes.filter(node => node.type !== "apiInput" && node.type !== "apiResponse" && node.type !== "personality"),
    { ...responseNode, ...response, id: responseNode.id, deletable: false, data: responseNode.data },
  ] as FlowItem[], wires};
}

export function hydrateGraph(graph: SavedGraph, agents: AgentDefinition[]) {
  const items = graph.nodes.map((node): FlowItem => {
    if (node.type !== "agent") return node;
    const definition = agents.find(agent => agent.id === node.data.definition?.id) ?? null;
    return { ...node, data: { ...node.data, label: definition?.name ?? "Unconfigured agent", definition } };
  });
  return openGraph(items, graph.edges, graph.personalityMode, graph.memoryMode);
}

export function allowsConnection(items: FlowItem[], edges: Wire[], connection: Connection | Edge, replacedEdgeId?: string) {
  const source = items.find(item => item.id === connection.source);
  const target = items.find(item => item.id === connection.target);
  if (!source || !target || source.id === target.id) return false;
  if (connection.targetHandle) {
    if (connection.targetHandle === "designer") return source.type === "designer" && target.type === "agent" &&
      !edges.some(edge => edge.id !== replacedEdgeId && edge.target === target.id && edge.targetHandle === "designer");
    if (connection.targetHandle === "memories") return source.type === "recall" && receivesMemories(target) &&
      !edges.some(edge => edge.id !== replacedEdgeId && edge.target === target.id && edge.targetHandle === "memories");
    return connection.targetHandle === "personality" && target.type === "agent" && source.type === "personality" &&
      !edges.some(edge => edge.id !== replacedEdgeId && edge.target === target.id && edge.targetHandle === "personality");
  }
  if (source.type === "apiResponse" && !["state", "recall", "remember"].includes(target.type!)) return false;
  if (!["apiInput", "apiResponse", "agent", "json", "switch", "state"].includes(source.type!)) return false;
  if (source.type === "switch" && !ports(source.data).some(port => port.id === connection.sourceHandle)) return false;
  if (target.type === "apiResponse") {
    if (source.type === "apiInput") return false;
  } else if (target.type === "switch" || target.type === "recall" || target.type === "remember") {
    if (edges.some(edge => edge.id !== replacedEdgeId && edge.target === target.id)) return false;
  } else if (target.type !== "agent" && target.type !== "json" && target.type !== "state") return false;
  return !edges.some(edge => edge.id !== replacedEdgeId && edge.source === source.id && edge.target === target.id &&
    (edge.sourceHandle ?? null) === (connection.sourceHandle ?? null));
}
