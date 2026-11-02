import { useRef } from "react";
import { createRoot } from "react-dom/client";
import {
  Background, Controls, ReactFlow, ReactFlowProvider, useNodesState, useReactFlow,
  type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./editor.css";

type GenericItem = Node<{ label: string }, "item">;
type ApiInput = Node<{ label: string }, "apiInput">;
type FlowItem = GenericItem | ApiInput;

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
  </div>;
}

const nodeTypes = { item: ItemCard, apiInput: ApiInputCard };
const startingNode: ApiInput = {
  id: "api-input", type: "apiInput", data: { label: "API Input" },
  position: { x: 112, y: 68 }, deletable: false, selected: true,
  ariaLabel: "API Input, flow start",
};

function Editor() {
  const [items, setItems, onNodesChange] = useNodesState<FlowItem>([startingNode]);
  const { screenToFlowPosition, updateNodeData } = useReactFlow<FlowItem>();
  const canvas = useRef<HTMLDivElement>(null);
  const nextNumber = useRef(1);
  const selected = items.find(item => item.selected);

  function addItem() {
    if (!canvas.current) return;
    const bounds = canvas.current.getBoundingClientRect();
    const number = nextNumber.current++;
    const offset = ((number - 1) % 4) * 18;
    const position = screenToFlowPosition({
      x: bounds.left + bounds.width / 2 + offset,
      y: bounds.top + bounds.height / 2 + offset,
    });
    const item: FlowItem = {
      id: crypto.randomUUID(), type: "item", position, selected: true,
      data: { label: `Item ${number}` },
    };
    setItems(current => [...current.map(node => ({ ...node, selected: false })), item]);
  }

  return <div className="flow-editor">
    <div className="flow-toolbar">
      <div><h2>Flow canvas</h2><small>Unsaved draft</small></div>
      <button className="add-item" type="button" onClick={addItem}>+ Add item</button>
    </div>
    <div className="flow-grid">
      <div className="flow-canvas" ref={canvas}>
        <ReactFlow<FlowItem> nodes={items} nodeTypes={nodeTypes} onNodesChange={onNodesChange}
          nodeOrigin={[0.5, 0.5]} nodesConnectable={false} multiSelectionKeyCode={null}
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
        {selected?.type === "item" && <>
          <label htmlFor="item-name">Item name</label>
          <input id="item-name" value={selected.data.label} maxLength={120} autoComplete="off"
            onChange={event => updateNodeData(selected.id, { label: event.target.value })}
            onBlur={() => {
              if (!selected.data.label.trim()) updateNodeData(selected.id, { label: "Untitled item" });
            }} />
          <h3>Inputs</h3><p>This generic item has no inputs.</p>
        </>}
        {!selected && <p>Select an item on the canvas to inspect it.</p>}
      </aside>
    </div>
  </div>;
}

const host = document.getElementById("flow-editor");
if (host) createRoot(host).render(<ReactFlowProvider><Editor /></ReactFlowProvider>);
