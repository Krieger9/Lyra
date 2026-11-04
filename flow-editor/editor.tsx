import { useEffect, useRef, useState, type CSSProperties } from "react";
import { createRoot } from "react-dom/client";
import {
  addEdge, Background, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, SelectionMode,
  useEdgesState, useNodesState, useReactFlow, type Connection, type Edge, type NodeProps, type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./editor.css";
import { loadCatalog, sampleObject, type AgentDefinition, type Catalog, type PromptReference } from "./catalog";
import { JsonMappingInspector } from "./json-mapping";
import { AgentOutput } from "./agent-output";
import { SelectionEditor } from "./chat-input";
import { RunView } from "./run-view";
import { MemoryStatus } from "./memory-status";
import { PersonalityPicker } from "./personality";
import { SwitchCard, SwitchInspector, newSwitch } from "./switch";
import { StateCard, StateInspector, StateObjectEditor, newState } from "./state";
import { WireSettings } from "./wires";
import { allowsConnection, graphDocument, hydrateGraph, nameExistingWires, nextWireName, ports, switchFieldGraph,
  withEndpoints, type Agent, type ApiInput, type FlowItem, type GenericItem, type JsonItem, type PanelSize,
  type SavedGraph, type Wire } from "./graph";
import { draftSession, linkWithReturn, renderBreadcrumbs } from "../static/admin/navigation.js";

type FlowDraft = { items: FlowItem[]; edges: Wire[]; tool: string; nextNumber: number; viewport: Viewport; panelSize: PanelSize;
  stateObject?: Record<string, unknown>;
  flowId?: string; revision?: number; name?: string; lastSaved?: string };
type FlowSummary = { id: string; name: string; revision: number };
type SavedFlow = FlowSummary & { graph: SavedGraph };


function agentConfigurationError(definition: AgentDefinition | null) {
  if (!definition) return "Select an agent definition before this flow can run.";
  if (!definition.name.trim() || !definition.instructions.name.trim() || definition.instructions.version < 1 ||
      (definition.prompt && (!definition.prompt.name.trim() || definition.prompt.version < 1))) {
    return "This agent definition needs a name, instructions and a valid input configuration.";
  }
  return null;
}

function ItemCard({ data }: NodeProps<GenericItem>) {
  return <div className="item-card">
    <span className="item-kind">Generic item</span>
    <strong>{data.label || "Untitled item"}</strong>
  </div>;
}

function JsonCard({ data }: NodeProps<JsonItem>) {
  return <div className="item-card json-card">
    <Handle type="target" position={Position.Left} title="JSON inputs" className="flow-port" />
    <span className="json-icon" aria-hidden="true">{"{ }"}</span>
    <div><span className="item-kind">JSON · Multiple inputs</span><strong>{data.label}</strong></div>
    <Handle type="source" position={Position.Right} title="JSON output" className="flow-port" />
  </div>;
}

function ApiInputCard({ data }: NodeProps<ApiInput>) {
  return <div className="item-card api-input-card">
    <span className="api-input-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
        strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 4h5a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-5M3 12h12m-5-5 5 5-5 5" />
      </svg>
    </span>
    <div><span className="item-kind">Flow start</span><strong>{data.label}</strong>
      {(data.recallMemories ?? true) && <small>Recall long-term memories</small>}
      {data.personalityId && <small>Load personality + goals + memories</small>}</div>
    <Handle type="source" position={Position.Right} title="API Input output" className="flow-port" />
  </div>;
}

function AgentCard({ data }: NodeProps<Agent>) {
  const error = agentConfigurationError(data.definition);
  return <div className={`item-card agent-card${error ? " invalid-agent" : ""}`}>
    <Handle type="target" position={Position.Left} title="Agent input" className="flow-port" />
    <span className="agent-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
        <path d="M12 4v3M3 12v4m18-4v4" /><rect x="5" y="7" width="14" height="13" rx="4" />
        <path d="M9 12v1m6-1v1m-5 3h4" />
      </svg>
    </span>
    <div>
      <span className="item-kind">Agent</span><strong>{data.label}</strong>
      {error && <span className="agent-validation" title={error}>⚠ Configuration required</span>}
    </div>
    <Handle type="source" position={Position.Right} title="Agent output" className="flow-port" />
  </div>;
}

function ApiResponseCard() {
  return <div className="item-card api-response-card">
    <Handle type="target" position={Position.Left} title="API Response input" className="flow-port" />
    <span className="api-input-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M10 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h5M9 12h12m-5-5 5 5-5 5" />
      </svg>
    </span>
    <div><span className="item-kind">Flow response · 200 / error</span><strong>API Response</strong></div>
    <Handle type="source" position={Position.Right} title="Selected reply for State" className="flow-port" />
  </div>;
}

const nodeTypes = { item: ItemCard, json: JsonCard, switch: SwitchCard, state: StateCard, apiInput: ApiInputCard, apiResponse: ApiResponseCard, agent: AgentCard };
const palette = [
  { type: "agent", title: "Agent" },
  { type: "json", title: "JSON" },
  { type: "switch", title: "Switch" },
  { type: "state", title: "State" },
  { type: "item", title: "Generic item" },
] as const;
const edgeOptions = { markerEnd: { type: MarkerType.ArrowClosed }, style: { strokeWidth: 2 } };

function Editor() {
  const [draft] = useState(() => draftSession<FlowDraft>());
  const [stateObject, setStateObject] = useState(draft.data?.stateObject ?? {});
  const [items, setItems, onNodesChange] = useNodesState<FlowItem>(withEndpoints(draft.data?.items));
  const [edges, setEdges, onEdgesChange] = useEdgesState<Wire>(nameExistingWires(draft.data?.edges ?? []));
  const { updateNodeData, deleteElements, fitView, getViewport, setViewport: setCanvasViewport } = useReactFlow<FlowItem>();
  const requestedFlow = new URLSearchParams(location.search).get("id") ?? undefined;
  const [flowId, setFlowId] = useState(draft.data?.flowId);
  const [revision, setRevision] = useState(draft.data?.revision);
  const [flowName, setFlowName] = useState(draft.data?.name ?? "");
  const [lastSaved, setLastSaved] = useState(draft.data?.lastSaved ?? "");
  const [savedFlows, setSavedFlows] = useState<FlowSummary[]>([]);
  const [openFlowId, setOpenFlowId] = useState(requestedFlow ?? draft.data?.flowId ?? "");
  const [flowStatus, setFlowStatus] = useState("");
  const [runDesign, setRunDesign] = useState<SavedFlow>();
  const [savingFlow, setSavingFlow] = useState(false);
  const [loadingFlow, setLoadingFlow] = useState(Boolean(requestedFlow &&
    (draft.data?.flowId !== requestedFlow || !draft.data?.revision)));
  const [tool, setTool] = useState<string>(draft.data?.tool ?? palette[0].type);
  const [viewport, setViewport] = useState<Viewport>(draft.data?.viewport ?? { x: 0, y: 0, zoom: 1 });
  const [panelSize, setPanelSize] = useState<PanelSize>(draft.data?.panelSize ?? { width: 280, height: 240 });
  const [stacked, setStacked] = useState(() => matchMedia("(max-width: 900px)").matches);
  const [gridSize, setGridSize] = useState({ width: 0, height: 0 });
  const [resizing, setResizing] = useState(false);
  const grid = useRef<HTMLDivElement>(null);
  const panelDrag = useRef<{ pointerId: number; x: number; y: number; size: number } | null>(null);
  const [catalog, setCatalog] = useState<Catalog>({ agents: [], prompts: [], providers: [] });
  const [catalogStatus, setCatalogStatus] = useState("Loading agents…");
  const [catalogReady, setCatalogReady] = useState(false);
  const [catalogRefresh, setCatalogRefresh] = useState(0);
  const nextNumber = useRef(draft.data?.nextNumber ?? 1);
  const selectedItems = items.filter(item => item.selected);
  const selected = selectedItems.length === 1 ? selectedItems[0] : undefined;
  const selectedEdge = selectedItems.length === 0 ? edges.find(edge => edge.selected) : undefined;
  const removable = selected?.deletable === false ? undefined : selected ?? selectedEdge;
  const sources = items.filter(item => item.id !== selected?.id).flatMap(item => {
    if (item.type === "switch") return ports(item.data).map(p => ({ source: item.id, sourceHandle: p.id as string | null,
      label: item.data.label + " · " + p.label }));
    if (item.type === "agent" || item.type === "json" || item.type === "state" || (selected?.type !== "apiResponse" && item.type === "apiInput")
        || (item.type === "apiResponse" && selected?.type === "state"))
      return [{ source: item.id, sourceHandle: null, label: item.data.label }];
    return [];
  });
  const inputEdges = edges.filter(edge => edge.target === selected?.id);
  const invalidBase = items.find(node => (node.type === "json" && node.data.baseError) ||
    (node.type === "state" && (node.data.initialError || !/^[A-Za-z_][A-Za-z0-9_.-]*$/.test(node.data.stateDefinition.key))));
  const maximumPanelSize = stacked
    ? Math.max(0, gridSize.height - 130)
    : Math.max(0, gridSize.width - 250);
  const minimumPanelSize = Math.min(stacked ? 120 : 220, maximumPanelSize);
  const clampPanelSize = (value: number) => Math.max(minimumPanelSize, Math.min(maximumPanelSize, value));
  const currentPanelSize = clampPanelSize(stacked ? panelSize.height : panelSize.width);
  const hasChanges = Boolean(invalidBase) || lastSaved !== JSON.stringify({ name: flowName.trim(), graph: graphDocument(items, edges, viewport, panelSize, stateObject) });

  function resizePanel(value: number) {
    const size = Math.round(clampPanelSize(value));
    setPanelSize(current => stacked ? { ...current, height: size } : { ...current, width: size });
  }

  useEffect(() => {
    const media = matchMedia("(max-width: 900px)");
    const updateOrientation = () => setStacked(media.matches);
    media.addEventListener("change", updateOrientation);
    const observer = new ResizeObserver(([entry]) => {
      setGridSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    if (grid.current) observer.observe(grid.current);
    return () => { observer.disconnect(); media.removeEventListener("change", updateOrientation); };
  }, []);

  useEffect(() => { renderBreadcrumbs(); }, []);
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (!params.get("run") || !params.get("id")) return;
    let live = true;
    window.lyraAdminRequest<SavedFlow>("/api/admin/flows/" + encodeURIComponent(params.get("id")!))
      .then(saved => { if (live) setRunDesign(saved); })
      .catch(e => { if (live) setFlowStatus(e.message); });
    return () => { live = false; };
  }, []);
  useEffect(() => {
    draft.save({ items, edges, tool, nextNumber: nextNumber.current, viewport, panelSize, flowId, revision, name: flowName, lastSaved, stateObject });
  }, [draft, items, edges, tool, viewport, panelSize, flowId, revision, flowName, lastSaved, stateObject]);
  useEffect(() => {
    function saveBeforeLeaving() {
      draft.save({ items, edges, tool, nextNumber: nextNumber.current, viewport: getViewport(), panelSize, flowId, revision, name: flowName, lastSaved, stateObject });
    }
    window.addEventListener("pagehide", saveBeforeLeaving);
    return () => window.removeEventListener("pagehide", saveBeforeLeaving);
  }, [draft, items, edges, tool, getViewport, panelSize, flowId, revision, flowName, lastSaved, stateObject]);

  useEffect(() => {
    window.lyraAdminRequest<FlowSummary[]>("/api/admin/flows").then(setSavedFlows)
      .catch(error => setFlowStatus(error.message));
  }, []);

  useEffect(() => {
    if (!loadingFlow || !catalogReady || !requestedFlow) return;
    let active = true;
    window.lyraAdminRequest<SavedFlow>("/api/admin/flows/" + requestedFlow).then(saved => {
      if (!active) return;
      const { nodes, wires } = hydrateGraph(saved.graph, catalog.agents);
      setItems(nodes);
      setStateObject(saved.graph.stateObject ?? {});
      setEdges(wires);
      setViewport(saved.graph.viewport);
      void setCanvasViewport(saved.graph.viewport);
      setPanelSize(saved.graph.panelSize);
      setFlowId(saved.id);
      setRevision(saved.revision);
      setFlowName(saved.name);
      setLastSaved(JSON.stringify({ name: saved.name, graph: graphDocument(nodes, wires, saved.graph.viewport, saved.graph.panelSize, saved.graph.stateObject) }));
      setLoadingFlow(false);
      setFlowStatus("Flow opened.");
      nextNumber.current = nodes.length;
    }).catch(error => { if (active) setFlowStatus(error.message); });
    return () => { active = false; };
  }, [loadingFlow, catalogReady, requestedFlow, catalog, setItems, setEdges, setCanvasViewport]);

  async function saveFlow(event?: React.FormEvent) {
    event?.preventDefault();
    if (invalidBase) return;
    setSavingFlow(true);
    setFlowStatus("Saving flow…");
    try {
      const payload = { name: flowName.trim(), graph: graphDocument(items, edges, getViewport(), panelSize, stateObject) };
      const saved = await window.lyraAdminRequest<SavedFlow>("/api/admin/flows" + (flowId ? "/" + flowId : ""), {
        method: flowId ? "PUT" : "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, revision }),
      });
      setFlowId(saved.id);
      setRevision(saved.revision);
      setFlowName(saved.name);
      setLastSaved(JSON.stringify(payload));
      setSavedFlows(current => [...current.filter(flow => flow.id !== saved.id), saved]);
      setOpenFlowId(saved.id);
      const url = new URL(location.href);
      url.searchParams.delete("new");
      url.searchParams.set("id", saved.id);
      history.replaceState(history.state, "", url.pathname + url.search);
      renderBreadcrumbs();
      setFlowStatus("Saved to PostgreSQL.");
      return saved;
    } catch (error) {
      setFlowStatus(error instanceof Error ? error.message : "Could not save the flow.");
    } finally { setSavingFlow(false); }
  }

  async function openRun() {
    if (hasChanges || !flowId || !revision) {
      const saved = await saveFlow();
      if (saved) setRunDesign(saved);
    } else {
      setRunDesign({id: flowId, name: flowName, revision, graph: {nodes: items, edges, viewport, panelSize, stateObject}});
    }
  }

  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const result = await loadCatalog();
        if (!active) return;
        setCatalog(result);
        setCatalogReady(true);
        setCatalogStatus(result.agents.length ? "" : "No agents defined yet. Create an agent, then select it here.");
        setItems(current => current.map(node => {
          if (node.type !== "agent" || !node.data.definition) return node;
          const definition = result.agents.find(agent => agent.id === node.data.definition?.id) ?? null;
          return { ...node, data: { ...node.data, label: definition?.name ?? "Unconfigured agent", definition } };
        }));
      } catch (error) {
        if (active) {
          setCatalogReady(false);
          setCatalogStatus(error instanceof Error ? error.message : "Could not load agents.");
        }
      }
    }
    void refresh();
    window.addEventListener("focus", refresh);
    return () => { active = false; window.removeEventListener("focus", refresh); };
  }, [setItems, catalogRefresh]);

  function promptText(reference: PromptReference) {
    return catalog.prompts.find(prompt => prompt.name === reference.name &&
      prompt.version === reference.version)?.text ?? "Prompt preview unavailable.";
  }

  function validConnection(connection: Connection | Edge, replacedEdgeId?: string) {
    return allowsConnection(items, edges, connection, replacedEdgeId);
  }

  function sampleOutput(id: string, visited = new Set<string>()): unknown {
    const node = items.find(item => item.id === id);
    if (!node || visited.has(id)) return undefined;
    const next = new Set(visited).add(id);
    if (node.type === "apiInput") return node.data.chatInput ? {message:"Hello", history:[{role:"user", content:"Earlier message"}]} : undefined;
    if (node.type === "json") return node.data.objectDefinition?.base;
    if (node.type === "agent" && node.data.definition?.outputSchema) return sampleObject(node.data.definition.outputSchema.fields);
    const parents = edges.filter(edge => edge.target === id);
    if (node.type === "switch" && parents.length) return sampleOutput(parents[0].source, next);
    if (node.type !== "state") return undefined;
    const def = node.data.stateDefinition;
    if (def.output !== "input") {
      const sample = {...stateObject, ...def.initial};
      return def.output === "property" ? sample[def.outputProperty ?? ""] : sample;
    }
    if (!def.outputWire && parents.length > 1)
      return Object.fromEntries(parents.map(edge => [edge.name ?? edge.id, sampleOutput(edge.source, next)]));
    const parent = parents.find(edge => !def.outputWire || edge.id === def.outputWire);
    return parent && sampleOutput(parent.source, next);
  }

  function connect(connection: Connection) {
    if (!validConnection(connection)) return;
    setEdges(current => {
      const names = new Set(current.filter(edge => edge.target === connection.target).map(edge => edge.name ?? ""));
      return addEdge<Wire>({ ...connection, id: crypto.randomUUID(), name: nextWireName(names) }, current);
    });
    setItems(current => current.map(node => ({ ...node, selected: node.id === connection.target })));
  }

  function sourceOptions(edge?: Wire) {
    return sources.filter(s => validConnection({ ...s, target: selected!.id, targetHandle: null }, edge?.id))
      .map(s => <option key={JSON.stringify([s.source, s.sourceHandle])} value={JSON.stringify([s.source, s.sourceHandle])}>{s.label}</option>);
  }

  function changeInput(edge: Edge, option: string) {
    const [source, sourceHandle] = JSON.parse(option);
    const connection = { ...edge, source, sourceHandle };
    if (!validConnection(connection, edge.id)) return;
    setEdges(current => current.map(other => other.id === edge.id ? connection : other));
  }

  async function deleteSelection() {
    if (!removable) return;
    const target = { id: removable.id };
    const { deletedEdges, deletedNodes } = await deleteElements(selected ? { nodes: [target] } : { edges: [target] });
    if (!deletedNodes.length && !deletedEdges.length) return;
    const removedWires = new Set(deletedEdges.map(edge => edge.id));
    const needsRepair = items.some(node => node.type === "json" && node.id !== selected?.id &&
      node.data.objectDefinition?.mappings.some(rule => removedWires.has(rule.edgeId)));
    const name = selected?.data.label || selectedEdge?.name || "Wire";
    setFlowStatus(`Removed ${name} from the flow.${needsRepair ? " Reassign or remove the disconnected JSON mappings." : ""}`);
  }

  function wireSettings(wire: Wire) {
    return <WireSettings key={wire.id + ":" + wire.name} wire={wire} wires={edges}
      jsonInput={items.some(item => item.id === wire.target && item.type === "json")}
      change={patch => setEdges(current => current.map(edge => edge.id === wire.id ? { ...edge, ...patch } : edge))} />;
  }

  function addItem() {
    const entry = palette.find(option => option.type === tool)!;
    const anchor = selected ?? items[0];
    const position = { x: anchor.position.x + 260, y: anchor.position.y };
    while (items.some(node => Math.abs(node.position.x - position.x) < 200 && Math.abs(node.position.y - position.y) < 90)) {
      position.y += 110;
    }
    const common = { id: crypto.randomUUID(), position, selected: true };
    let item: FlowItem;
    if (entry.type === "agent") {
      item = { ...common, type: "agent", data: { label: "Unconfigured agent", definition: null } };
    } else if (entry.type === "json") {
      item = { ...common, type: "json", data: { label: `JSON ${nextNumber.current++}`, objectDefinition: { base: {}, mappings: [] } } };
    } else if (entry.type === "state") {
      item = { ...common, type: "state", data: { label: `State ${nextNumber.current++}`, stateDefinition: newState() } };
    } else if (entry.type === "switch") {
      item = { ...common, type: "switch", data: { label: `Switch ${nextNumber.current++}`, switchDefinition: newSwitch() } };
    } else {
      item = { ...common, type: entry.type, data: { label: `Item ${nextNumber.current++}` } };
    }
    setItems(current => [...current.map(node => ({ ...node, selected: false })), item]);
    void fitView({ nodes: [anchor, item], padding: 0.18, maxZoom: 1 });
  }

  return <div className="flow-editor" style={{ "--panel-size": `${currentPanelSize}px` } as CSSProperties}>
    {runDesign && <RunView design={runDesign} onClose={() => {
      const url = new URL(location.href);
      url.searchParams.delete("run");
      history.replaceState(history.state, "", url);
      setRunDesign(undefined);
    }} />}
    <div className="flow-toolbar">
      <div className="flow-management" role="group" aria-label="Flow management">
      <div><h2>Flow View</h2><small>{flowId ? `Revision ${revision} · ${hasChanges ? "Unsaved changes" : "Saved"}` : "Unsaved draft"}</small></div>
      <form className="flow-actions" onSubmit={saveFlow}>
        <label htmlFor="flow-name">Flow name</label>
        <input id="flow-name" value={flowName} onChange={event => setFlowName(event.target.value)} required maxLength={120} />
        <button type="submit" disabled={savingFlow || loadingFlow || Boolean(invalidBase)}>Save flow</button>
        <button type="button" disabled={savingFlow || loadingFlow || Boolean(invalidBase) || !flowName.trim()}
          onClick={openRun}>{hasChanges ? "Save and run…" : "Run flow…"}</button>
      </form>
      <div className="flow-actions">
        <label htmlFor="saved-flow">Saved flows</label>
        <select id="saved-flow" value={openFlowId} onChange={event => setOpenFlowId(event.target.value)}>
          <option value="">Choose a flow…</option>
          {savedFlows.map(flow => <option key={flow.id} value={flow.id}>{flow.name}</option>)}
        </select>
        <button type="button" disabled={!openFlowId}
          onClick={() => location.assign("/admin/flow/?id=" + openFlowId)}>Open flow</button>
        <a href="/admin/flow/?new=1">New flow</a>
      </div>
      <p className="flow-save-status" role="status">{invalidBase ? `Fix the ${invalidBase.type === "state" ? "key or initial object" : "base object"} in ${invalidBase.data.label} to save.` : loadingFlow ? flowStatus || "Loading flow…" : flowStatus}</p>
      </div>
      <div className="flow-item-tools" role="group" aria-label="Add flow items">
        <label htmlFor="flow-type">Item type</label>
        <select id="flow-type" value={tool} onChange={event => setTool(event.target.value)}>
          {palette.map(entry => <option key={entry.type} value={entry.type}>{entry.title}</option>)}
        </select>
        <button className="add-item" type="button" onClick={addItem}>+ Add to canvas</button>
      </div>
    </div>
    {(items.some(node => node.type === "state") || Object.keys(stateObject).length > 0) &&
      <StateObjectEditor properties={stateObject} change={setStateObject} />}
    <MemoryStatus />
    <div className={`flow-grid${resizing ? " resizing" : ""}`} ref={grid}>
      <div className="flow-canvas" onKeyDown={event => {
        if (!removable || event.repeat || event.defaultPrevented || (event.key !== "Delete" && event.key !== "Backspace")) return;
        if ((event.target as Element).closest("input, textarea, select, [contenteditable=true]")) return;
        event.preventDefault();
        void deleteSelection();
      }}>
        <ReactFlow<FlowItem> nodes={items} nodeTypes={nodeTypes} onNodesChange={onNodesChange}
          defaultViewport={draft.data?.viewport} onMoveEnd={(_, value) => setViewport(value)}
          edges={edges.map(edge => ({
            ...edge, label: edge.name,
            ariaLabel: `Connection ${edge.name} (${edge.id})`, labelStyle: { fontSize: 11, fill: "#40336f" },
            labelBgStyle: { fill: "#f1edf8" }, labelBgBorderRadius: 4, labelBgPadding: [6, 4],
          }))} onEdgesChange={onEdgesChange} defaultEdgeOptions={edgeOptions}
          onEdgeClick={(_, edge) => {
            setItems(current => current.map(node => ({ ...node, selected: false })));
            setEdges(current => current.map(other => ({ ...other, selected: other.id === edge.id })));
          }}
          onNodeClick={() => setEdges(current => current.map(edge => ({ ...edge, selected: false })))}
          isValidConnection={validConnection} onConnect={connect}
          nodeOrigin={[0.5, 0.5]} selectionOnDrag selectionMode={SelectionMode.Partial} panOnDrag={[1]} multiSelectionKeyCode={null}
          selectionKeyCode={null} deleteKeyCode={null} ariaLabelConfig={{ "node.a11yDescription.default":
            "Press Enter or Space to select a flow item. Use the arrow keys to move it." }}>
          <Background gap={20} color="#cfccda" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <div className="panel-divider" role="separator" tabIndex={0}
        aria-label="Resize data panel" aria-controls="flow-data-panel"
        aria-orientation={stacked ? "horizontal" : "vertical"}
        aria-valuemin={minimumPanelSize} aria-valuemax={maximumPanelSize}
        aria-valuenow={currentPanelSize} aria-valuetext={`${currentPanelSize} pixels`}
        title={stacked ? "Drag to resize data panel height" : "Drag to resize data panel width"}
        onPointerDown={event => {
          if (event.button !== 0) return;
          event.preventDefault();
          event.currentTarget.focus();
          event.currentTarget.setPointerCapture(event.pointerId);
          panelDrag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, size: currentPanelSize };
          setResizing(true);
        }}
        onPointerMove={event => {
          const drag = panelDrag.current;
          if (!drag || event.pointerId !== drag.pointerId) return;
          resizePanel(drag.size + (stacked ? drag.y - event.clientY : drag.x - event.clientX));
        }}
        onPointerUp={event => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onLostPointerCapture={() => { panelDrag.current = null; setResizing(false); }}
        onPointerCancel={() => { panelDrag.current = null; setResizing(false); }}
        onKeyDown={event => {
          const increaseKey = stacked ? "ArrowUp" : "ArrowLeft";
          const decreaseKey = stacked ? "ArrowDown" : "ArrowRight";
          if (![increaseKey, decreaseKey, "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          if (event.key === "Home") resizePanel(minimumPanelSize);
          else if (event.key === "End") resizePanel(maximumPanelSize);
          else resizePanel(currentPanelSize + (event.key === increaseKey ? 20 : -20));
        }} />
      <aside id="flow-data-panel" className="flow-inspector" aria-label="Item inspector">
        <div className="inspector-heading">
          <h3>Properties</h3>
          {removable && <button type="button" className="delete-element" onClick={deleteSelection}
            aria-keyshortcuts="Delete Backspace">{selected ? "Delete item" : "Delete wire"}</button>}
        </div>
        {selected?.type === "apiInput" && <>
          <strong>{selected.data.label}</strong>
          <p>This endpoint is the required starting point for the flow.</p>
          <h3>Endpoint</h3>
          {flowId ? <code>POST /api/admin/flows/{flowId}/run</code> : <p>Save this flow to create its run endpoint.</p>}
          <label>Input format<select value={selected.data.chatInput ? "chat" : "default"}
            onChange={e => updateNodeData(selected.id, { chatInput: e.target.value === "chat" })}>
            <option value="default">As received (current message for chat)</option>
            <option value="chat">Chat request with history</option>
          </select></label>
          {selected.data.chatInput && <p>Outputs <code>message</code> and an ordered <code>history</code> of prior turns.</p>}
          <label><input type="checkbox" checked={selected.data.recallMemories ?? true}
            onChange={e => updateNodeData(selected.id, {recallMemories:e.target.checked})} />Recall long-term memories</label>
          <p>Search saved memories using the latest message and prior context. Relevant facts are supplied to agents
            before they answer, including in a new chat. Inspect API Input in run history to see the recalled memories.</p>
          <PersonalityPicker value={selected.data.personalityId} change={personalityId => updateNodeData(selected.id, {personalityId})} />
        </>}
        {selected?.type === "apiResponse" && <>
          <strong>API Response</strong>
          <p>Returns HTTP 200 on success, or an error status if the flow fails.</p>
          <p>Connect one output, or outputs from mutually exclusive Switch paths. Exactly one connected output must be active per run. An unconnected response has an empty body.</p>
          <p>Connect the right port to State to update memory with the selected reply before the turn commits.</p>
        </>}
        {selectedEdge && <>
          <h3>Connection</h3>
          {wireSettings(selectedEdge)}
          <h3>From</h3><p>{items.find(node => node.id === selectedEdge.source)?.data.label}</p>
          <h3>To</h3><p>{items.find(node => node.id === selectedEdge.target)?.data.label}</p>
          <p>This ID is preserved when the flow is saved or the connection is rewired.</p>
        </>}
        {selected && selected.type !== "apiInput" && <>
          {selected.type === "agent" && <>
            <label><input type="checkbox" checked={selected.data.usePersonality ?? true}
              onChange={e => updateNodeData(selected.id, {usePersonality: e.target.checked})} />Use the flow’s personality</label>
            <label htmlFor="agent-definition">Defined agent</label>
            <select id="agent-definition" value={selected.data.definition?.id ?? ""} disabled={!catalogReady}
              onChange={event => {
                const definition = catalog.agents.find(agent => agent.id === event.target.value) ?? null;
                updateNodeData(selected.id, { label: definition?.name ?? "Unconfigured agent", definition });
              }}>
              <option value="">Unconfigured — choose an agent…</option>
              {catalog.agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
            </select>
            <a href={linkWithReturn("/admin/agents/?new=1", { node: selected.id })}>Create agent</a>
            <button type="button" onClick={() => setCatalogRefresh(current => current + 1)}>Refresh agents</button>
            {catalogStatus && <p className="catalog-status" role="status">{catalogStatus}</p>}
            {agentConfigurationError(selected.data.definition) && <p className="agent-error" role="alert">
              {agentConfigurationError(selected.data.definition)}
            </p>}
          </>}
          {selected.type !== "apiResponse" && <>
          <label htmlFor="item-name">{selected.type === "switch" ? "Switch" : selected.type === "agent" ? "Agent" : selected.type === "json" ? "JSON" : "Item"} name</label>
          <input id="item-name" value={selected.type === "agent" ? selected.data.definition?.name ?? "" : selected.data.label}
            maxLength={120} autoComplete="off"
            readOnly={selected.type === "agent"}
            onChange={event => updateNodeData(selected.id, { label: event.target.value })}
            onBlur={() => {
              if ((selected.type === "item" || selected.type === "json") && !selected.data.label.trim()) {
                updateNodeData(selected.id, { label: selected.type === "json" ? "Untitled JSON" : "Untitled item" });
              }
            }} />
          </>}
          {selected.type === "state" && <StateInspector key={selected.id} data={selected.data} properties={stateObject}
            agents={catalog.agents} ports={inputEdges.map(edge => ({id:edge.id, name:edge.name!, base:sampleOutput(edge.source)}))}
            change={patch => updateNodeData(selected.id, patch)} />}
          {selected.type === "switch" && <SwitchInspector key={selected.id} node={selected}
            graphBody={JSON.stringify(switchFieldGraph(selected.id, items, edges, stateObject))}
            catalogVersion={JSON.stringify(catalog.agents.map(a => [a.id, a.revision]))}
            change={(switchDefinition, removed) => {
              updateNodeData(selected.id, { switchDefinition });
              if (removed) setEdges(current => current.filter(e => e.source !== selected.id || e.sourceHandle !== removed));
            }} />}
          {selected.type === "json" && <JsonMappingInspector key={selected.id} data={selected.data}
            ports={inputEdges.map(edge => {
              const source = items.find(node => node.id === edge.source);
              const base = sampleOutput(edge.source);
              return { id: edge.id, name: edge.name!, readAs: edge.readAs, label: source?.data.label ?? "Input",
                structured: base !== undefined && typeof base !== "string" || source?.type === "json", base };
            })}
            onChange={patch => updateNodeData(selected.id, patch)} />}
          {selected.type === "agent" && selected.data.definition && <>
            <SelectionEditor selection={selected.data.inputSelection}
              change={inputSelection => updateNodeData(selected.id, {inputSelection})} />
            <a href={linkWithReturn(`/admin/agents/?id=${selected.data.definition.id}`, { node: selected.id })}>Edit agent definition</a>
            <h3>Request model</h3>
            <p>{selected.data.definition.modelSelection
              ? [catalog.providers.find(p => p.id === selected.data.definition?.modelSelection?.providerId)?.name,
                 selected.data.definition.modelSelection.model].filter(Boolean).join(" · ")
              : "Default chat model"}</p>
            <h3>Instructions</h3>
            <p>{selected.data.definition.instructions.name} · v{selected.data.definition.instructions.version}</p>
            <pre>{promptText(selected.data.definition.instructions)}</pre>
            <h3>Prompt</h3>
            {selected.data.definition.prompt ? <>
              <p>{selected.data.definition.prompt.name} · v{selected.data.definition.prompt.version}</p>
              <pre>{promptText(selected.data.definition.prompt)}</pre>
            </> : <p>Raw user input · Incoming flow text is used directly.</p>}
            <AgentOutput key={[selected.id, selected.data.definition.id, selected.data.definition.revision].join(":")}
              agent={selected.data.definition}
              selection={selected.data.inputSelection}
              editUrl={linkWithReturn(`/admin/agents/?id=${selected.data.definition.id}&focus=output`, { node: selected.id })} />
          </>}
          <h3>Inputs</h3>
          {selected.type !== "item" ? <>
            {inputEdges.length === 0 && <p>No input connected.</p>}
            {inputEdges.map((edge, index) => {
              const label = items.find(node => node.id === edge.source)?.data.label || "Untitled item";
              return <div className="flow-input" key={edge.id}>
                <label htmlFor={`input-${edge.id}`}>Input {index + 1} source</label>
                <select id={`input-${edge.id}`} value={JSON.stringify([edge.source, edge.sourceHandle ?? null])}
                  onChange={event => changeInput(edge, event.target.value)}>
                  {sourceOptions(edge)}
                </select>
                {wireSettings(edge)}
                <button type="button" aria-label={`Disconnect ${label}`}
                  onClick={() => setEdges(current => current.filter(other => other.id !== edge.id))}>Disconnect</button>
              </div>;
            })}
            {(selected.type !== "switch" || inputEdges.length === 0) && <>
            <label htmlFor="input-source">Add input</label>
            <select id="input-source" value="" onChange={event => {
              const [source, sourceHandle] = JSON.parse(event.target.value);
              connect({ source, sourceHandle, target: selected.id, targetHandle: null });
            }}>
              <option value="" disabled>Choose a source…</option>
              {sourceOptions()}
            </select>
            </>}
          </> : <p>This generic item has no inputs.</p>}
        </>}
        {selectedItems.length > 1 && <p>{selectedItems.length} items selected. Drag the selection to move them together.</p>}
        {!selectedItems.length && !selectedEdge && <p>Select an item or connection to inspect it. Drag on empty canvas to select multiple items. Hold Space and drag to pan.</p>}
      </aside>
    </div>
  </div>;
}

const host = document.getElementById("flow-editor");
if (host) createRoot(host).render(<ReactFlowProvider><Editor /></ReactFlowProvider>);
