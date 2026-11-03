import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

type StateDefinition = { key: string; scope: "run" | "conversation"; operation: "read" | "update" | "replace";
  initial: Record<string, unknown> };
export type StateItem = Node<{ label: string; stateDefinition: StateDefinition; initialText?: string;
  initialError?: boolean }, "state">;

export function newState(): StateDefinition {
  return { key: "context", scope: "conversation", operation: "read", initial: {} };
}

export function StateCard({data}: NodeProps<StateItem>) {
  const def = data.stateDefinition;
  return <div className="item-card">
    <Handle type="target" position={Position.Left} className="flow-port" />
    <div><span className="item-kind">State · {def.scope}</span><strong>{data.label}</strong>
      <small>{def.operation} · {def.key}</small></div>
    <Handle type="source" position={Position.Right} className="flow-port" />
  </div>;
}

export function StateInspector({data, change}: {data: StateItem["data"]; change: (patch: Partial<StateItem["data"]>) => void}) {
  const def = data.stateDefinition;
  const update = (patch: Partial<StateDefinition>) => change({stateDefinition: {...def, ...patch}});
  return <>
    <label>State key<input value={def.key} maxLength={80} onChange={e => update({key:e.target.value})} /></label>
    <label>Scope<select value={def.scope} onChange={e => update({scope:e.target.value as StateDefinition["scope"]})}>
      <option value="run">This run</option><option value="conversation">This conversation</option>
    </select></label>
    <label>Operation<select value={def.operation} onChange={e => update({operation:e.target.value as StateDefinition["operation"]})}>
      <option value="read">Read</option><option value="update">Update fields</option><option value="replace">Replace object</option>
    </select></label>
    <label>Initial object<textarea value={data.initialText ?? JSON.stringify(def.initial, null, 2)} onChange={e => {
      const initialText = e.target.value;
      try {
        const initial = JSON.parse(initialText);
        if (!initial || typeof initial !== "object" || Array.isArray(initial)) throw new Error();
        change({initialText, initialError:false, stateDefinition:{...def, initial}});
      } catch { change({initialText, initialError:true}); }
    }} /></label>
    {data.initialError && <p role="alert">Enter a valid JSON object.</p>}
    <p>{def.operation === "read" ? "The incoming wire triggers a read. The output is the current object." :
      "Supply an object on the incoming wire. The output is the object after the write."}</p>
  </>;
}
