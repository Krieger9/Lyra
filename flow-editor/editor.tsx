import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  addEdge, Background, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider,
  useEdgesState, useNodesState, useReactFlow, type Connection, type Edge, type Node, type NodeProps, type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./editor.css";
import { loadCatalog, type AgentDefinition, type Catalog, type PromptReference } from "./catalog";
import { draftSession, linkWithReturn, renderBreadcrumbs } from "../static/admin/navigation.js";

type GenericItem = Node<{ label: string }, "item">;
type ApiInput = Node<{ label: string }, "apiInput">;
type Agent = Node<{ label: string; definition: AgentDefinition | null }, "agent">;
type FlowItem = GenericItem | ApiInput | Agent;
type FlowDraft = { items: FlowItem[]; edges: Edge[]; tool: string; nextNumber: number; viewport: Viewport };

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

function ApiInputCard({ data }: NodeProps<ApiInput>) {
  return <div className="item-card api-input-card">
    <span className="api-input-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
        strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 4h5a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-5M3 12h12m-5-5 5 5-5 5" />
      </svg>
    </span>
    <div><span className="item-kind">Flow start</span><strong>{data.label}</strong></div>
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

const nodeTypes = { item: ItemCard, apiInput: ApiInputCard, agent: AgentCard };
const palette = [
  { type: "agent", title: "Agent" },
  { type: "item", title: "Generic item" },
] as const;
const edgeOptions = { markerEnd: { type: MarkerType.ArrowClosed }, style: { strokeWidth: 2 } };
const startingNode: ApiInput = {
  id: "api-input", type: "apiInput", data: { label: "API Input" },
  position: { x: 112, y: 68 }, deletable: false, selected: true,
  ariaLabel: "API Input, flow start",
};

function Editor() {
  const [draft] = useState(() => draftSession<FlowDraft>());
  const [items, setItems, onNodesChange] = useNodesState<FlowItem>(draft.data?.items ?? [startingNode]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(draft.data?.edges ?? []);
  const { updateNodeData, fitView, getViewport } = useReactFlow<FlowItem>();
  const [tool, setTool] = useState<string>(draft.data?.tool ?? palette[0].type);
  const [viewport, setViewport] = useState<Viewport>(draft.data?.viewport ?? { x: 0, y: 0, zoom: 1 });
  const [catalog, setCatalog] = useState<Catalog>({ agents: [], prompts: [] });
  const [catalogStatus, setCatalogStatus] = useState("Loading agents…");
  const [catalogReady, setCatalogReady] = useState(false);
  const [catalogRefresh, setCatalogRefresh] = useState(0);
  const nextNumber = useRef(draft.data?.nextNumber ?? 1);
  const selected = items.find(item => item.selected);
  const sources = items.filter(item => item.type === "apiInput" || item.type === "agent");
  const inputEdges = edges.filter(edge => edge.target === selected?.id);

  useEffect(() => { renderBreadcrumbs(); }, []);
  useEffect(() => {
    draft.save({ items, edges, tool, nextNumber: nextNumber.current, viewport });
  }, [draft, items, edges, tool, viewport]);
  useEffect(() => {
    function saveBeforeLeaving() {
      draft.save({ items, edges, tool, nextNumber: nextNumber.current, viewport: getViewport() });
    }
    window.addEventListener("pagehide", saveBeforeLeaving);
    return () => window.removeEventListener("pagehide", saveBeforeLeaving);
  }, [draft, items, edges, tool, getViewport]);

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
          return { ...node, data: { label: definition?.name ?? "Unconfigured agent", definition } };
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
    return connection.source !== connection.target &&
      sources.some(item => item.id === connection.source) &&
      items.some(item => item.id === connection.target && item.type === "agent") &&
      !edges.some(edge => edge.id !== replacedEdgeId &&
        edge.source === connection.source && edge.target === connection.target);
  }

  function connect(connection: Connection) {
    if (!validConnection(connection)) return;
    setEdges(current => addEdge({ ...connection, id: crypto.randomUUID() }, current));
    setItems(current => current.map(node => ({ ...node, selected: node.id === connection.target })));
  }

  function changeInput(edge: Edge, source: string) {
    const connection = { ...edge, source, sourceHandle: null };
    if (!validConnection(connection, edge.id)) return;
    setEdges(current => current.map(other => other.id === edge.id ? connection : other));
  }

  function addItem() {
    const entry = palette.find(option => option.type === tool)!;
    const anchor = selected ?? items[0];
    const position = { x: anchor.position.x + 260, y: anchor.position.y };
    while (items.some(node => Math.abs(node.position.x - position.x) < 200 && Math.abs(node.position.y - position.y) < 90)) {
      position.y += 110;
    }
    const common = { id: crypto.randomUUID(), position, selected: true };
    const item: FlowItem = entry.type === "agent"
      ? { ...common, type: "agent", data: { label: "Unconfigured agent", definition: null } }
      : { ...common, type: "item", data: { label: `Item ${nextNumber.current++}` } };
    setItems(current => [...current.map(node => ({ ...node, selected: false })), item]);
    void fitView({ nodes: [anchor, item], padding: 0.18, maxZoom: 1 });
  }

  return <div className="flow-editor">
    <div className="flow-toolbar">
      <div><h2>Flow View</h2><small>Unsaved draft</small></div>
      <div className="flow-actions">
        <label htmlFor="flow-type">Item type</label>
        <select id="flow-type" value={tool} onChange={event => setTool(event.target.value)}>
          {palette.map(entry => <option key={entry.type} value={entry.type}>{entry.title}</option>)}
        </select>
        <button className="add-item" type="button" onClick={addItem}>+ Add to canvas</button>
      </div>
    </div>
    <div className="flow-grid">
      <div className="flow-canvas">
        <ReactFlow<FlowItem> nodes={items} nodeTypes={nodeTypes} onNodesChange={onNodesChange}
          defaultViewport={draft.data?.viewport} onMoveEnd={(_, value) => setViewport(value)}
          edges={edges} onEdgesChange={onEdgesChange} defaultEdgeOptions={edgeOptions}
          isValidConnection={validConnection} onConnect={connect}
          nodeOrigin={[0.5, 0.5]} multiSelectionKeyCode={null}
          selectionKeyCode={null} deleteKeyCode={null} ariaLabelConfig={{ "node.a11yDescription.default":
            "Press Enter or Space to select a flow item. Use the arrow keys to move it." }}>
          <Background gap={20} color="#cfccda" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <aside className="flow-inspector" aria-label="Item inspector">
        <h3>Properties</h3>
        {selected?.type === "apiInput" && <>
          <strong>{selected.data.label}</strong>
          <p>This endpoint is the required starting point for the flow.</p>
          <h3>Inputs</h3><p>Incoming API request</p>
        </>}
        {(selected?.type === "item" || selected?.type === "agent") && <>
          {selected.type === "agent" && <>
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
          <label htmlFor="item-name">{selected.type === "agent" ? "Agent" : "Item"} name</label>
          <input id="item-name" value={selected.type === "agent" ? selected.data.definition?.name ?? "" : selected.data.label}
            maxLength={120} autoComplete="off"
            readOnly={selected.type === "agent"}
            onChange={event => updateNodeData(selected.id, { label: event.target.value })}
            onBlur={() => {
              if (selected.type === "item" && !selected.data.label.trim()) updateNodeData(selected.id, { label: "Untitled item" });
            }} />
          {selected.type === "agent" && selected.data.definition && <>
            <a href={linkWithReturn(`/admin/agents/?id=${selected.data.definition.id}`, { node: selected.id })}>Edit agent definition</a>
            <h3>Instructions</h3>
            <p>{selected.data.definition.instructions.name} · v{selected.data.definition.instructions.version}</p>
            <pre>{promptText(selected.data.definition.instructions)}</pre>
            <h3>Prompt</h3>
            {selected.data.definition.prompt ? <>
              <p>{selected.data.definition.prompt.name} · v{selected.data.definition.prompt.version}</p>
              <pre>{promptText(selected.data.definition.prompt)}</pre>
            </> : <p>Raw user input · Incoming flow text is used directly.</p>}
          </>}
          <h3>Inputs</h3>
          {selected.type === "agent" ? <>
            {inputEdges.length === 0 && <p>No input connected.</p>}
            {inputEdges.map((edge, index) => {
              const label = sources.find(node => node.id === edge.source)?.data.label || "Untitled agent";
              return <div className="flow-input" key={edge.id}>
                <label htmlFor={`input-${edge.id}`}>Input {index + 1} source</label>
                <select id={`input-${edge.id}`} value={edge.source}
                  onChange={event => changeInput(edge, event.target.value)}>
                  {sources.filter(node => node.id !== selected.id &&
                    !inputEdges.some(other => other.id !== edge.id && other.source === node.id))
                    .map(node => <option key={node.id} value={node.id}>{node.data.label || "Untitled agent"}</option>)}
                </select>
                <button type="button" aria-label={`Disconnect ${label}`}
                  onClick={() => setEdges(current => current.filter(other => other.id !== edge.id))}>Disconnect</button>
              </div>;
            })}
            <label htmlFor="input-source">Add input</label>
            <select id="input-source" value="" onChange={event => connect({
              source: event.target.value, target: selected.id, sourceHandle: null, targetHandle: null,
            })}>
              <option value="" disabled>Choose a source…</option>
              {sources.filter(node => node.id !== selected.id && !inputEdges.some(edge => edge.source === node.id))
                .map(node => <option key={node.id} value={node.id}>{node.data.label || "Untitled agent"}</option>)}
            </select>
          </> : <p>This generic item has no inputs.</p>}
        </>}
        {!selected && <p>Select an item on the canvas to inspect it.</p>}
      </aside>
    </div>
  </div>;
}

const host = document.getElementById("flow-editor");
if (host) createRoot(host).render(<ReactFlowProvider><Editor /></ReactFlowProvider>);
