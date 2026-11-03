import type { Connection, Edge, Node, Viewport } from "@xyflow/react";
import type { AgentDefinition } from "./catalog";
import type { JsonNodeData } from "./json-mapping";
import type { SwitchItem } from "./switch";

export type GenericItem = Node<{ label: string }, "item">;
export type JsonItem = Node<JsonNodeData, "json">;
export type ApiInput = Node<{ label: string }, "apiInput">;
export type ApiResponse = Node<{ label: string }, "apiResponse">;
export type Agent = Node<{ label: string; definition: AgentDefinition | null }, "agent">;
export type FlowItem = GenericItem | JsonItem | ApiInput | ApiResponse | Agent | SwitchItem;
export type PanelSize = { width: number; height: number };
type SavedNode = Exclude<FlowItem, Agent> | Node<{ label: string; definition: { id: string } | null }, "agent">;
export type SavedGraph = { nodes: SavedNode[]; edges: Wire[]; viewport: Viewport; panelSize: PanelSize };

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
    if (wire.name) return wire;
    const names = used.get(wire.target)!;
    const name = nextWireName(names);
    names.add(name);
    return { ...wire, name };
  });
}

export const otherwise = { id: "otherwise", label: "Otherwise" };
export const ports = (data: SwitchItem["data"]) => [...(data.switchDefinition?.paths ?? []), otherwise];

export function graphDocument(items: FlowItem[], edges: Wire[], viewport: Viewport, panelSize: PanelSize) {
  return {
    nodes: items.map(node => ({
      id: node.id, type: node.type, position: node.position,
      data: { label: node.data.label, definition: node.type === "agent" && node.data.definition
        ? { id: node.data.definition.id } : null,
        ...(node.type === "json" ? { objectDefinition: node.data.objectDefinition ?? null } : {}),
        ...(node.type === "switch" ? { switchDefinition: node.data.switchDefinition } : {}) },
    })),
    edges: edges.map(edge => ({ id: edge.id, source: edge.source, target: edge.target, name: edge.name,
      ...(edge.sourceHandle ? {sourceHandle: edge.sourceHandle} : {}), readAs: edge.readAs ?? "value" })),
    viewport, panelSize,
  };
}

// Only port IDs and upstream schemas affect the field picker. Editing a check
// or dragging a card should not invalidate an in-progress selection.
export function switchFieldGraph(id: string, items: FlowItem[], edges: Wire[]) {
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
  return { nodes: nodes.map(node => ({ ...node, position: { x: 0, y: 0 }, data: { ...node.data, label: "",
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

export function withEndpoints(nodes: FlowItem[] = []) {
  const input = nodes.find(node => node.type === "apiInput");
  const response = nodes.find(node => node.type === "apiResponse");
  return [
    { ...startingNode, ...input, id: startingNode.id, deletable: false, data: startingNode.data },
    ...nodes.filter(node => node.type !== "apiInput" && node.type !== "apiResponse"),
    { ...responseNode, ...response, id: responseNode.id, deletable: false, data: responseNode.data },
  ] as FlowItem[];
}

export function hydrateGraph(graph: SavedGraph, agents: AgentDefinition[]) {
  const items = graph.nodes.map((node): FlowItem => {
    if (node.type !== "agent") return node;
    const definition = agents.find(agent => agent.id === node.data.definition?.id) ?? null;
    return { ...node, data: { label: definition?.name ?? "Unconfigured agent", definition } };
  });
  return { nodes: withEndpoints(items), wires: nameExistingWires(graph.edges) };
}

export function allowsConnection(items: FlowItem[], edges: Wire[], connection: Connection | Edge, replacedEdgeId?: string) {
  const source = items.find(item => item.id === connection.source);
  const target = items.find(item => item.id === connection.target);
  if (!source || !target || source.id === target.id) return false;
  if (!["apiInput", "agent", "json", "switch"].includes(source.type!)) return false;
  if (source.type === "switch" && !ports(source.data).some(port => port.id === connection.sourceHandle)) return false;
  if (target.type === "apiResponse") {
    if (source.type === "apiInput") return false;
  } else if (target.type === "switch") {
    if (edges.some(edge => edge.id !== replacedEdgeId && edge.target === target.id)) return false;
  } else if (target.type !== "agent" && target.type !== "json") return false;
  return !edges.some(edge => edge.id !== replacedEdgeId && edge.source === source.id && edge.target === target.id &&
    (edge.sourceHandle ?? null) === (connection.sourceHandle ?? null));
}
