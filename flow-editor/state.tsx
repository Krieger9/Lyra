import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { useState } from "react";
import { sampleObject, type AgentDefinition } from "./catalog";
import { fieldPaths } from "./json-mapping";
import type { Rule } from "./bindings";

type StateDefinition = { key: string; scope: "run" | "conversation";
  operation: "read" | "update" | "replace" | "map" | "generate" | "generateObject";
  initial: Record<string, unknown>; agentId?: string | null; mappings?: Rule[]; property?: string; includeChatTurn?: boolean;
  captureMemories?: boolean;
  output?: "state" | "input" | "property"; outputProperty?: string; outputWire?: string };
type Port = {id: string; name: string; base?: unknown};
const pathFor = (key: string) => "/" + key.replaceAll("~", "~0").replaceAll("/", "~1");
export type StateItem = Node<{ label: string; stateDefinition: StateDefinition; initialText?: string;
  initialError?: boolean }, "state">;

export function newState(): StateDefinition {
  return { key: "context", scope: "conversation", operation: "read", initial: {}, output: "input" };
}

export function StateObjectEditor({properties, change}: {
  properties: Record<string, unknown>; change: (properties: Record<string, unknown>) => void;
}) {
  const [key, setKey] = useState("");
  const [value, setValue] = useState('""');
  const [error, setError] = useState("");
  const names = Object.keys(properties);
  return <details className="state-object">
    <summary><strong>State object</strong> · {names.length} {names.length === 1 ? "property" : "properties"}</summary>
    <p>Define properties and their initial values for State elements in this Flow.</p>
    <div className="state-properties">{names.map(name => <button type="button" key={name} onClick={() => {
      setKey(name); setValue(JSON.stringify(properties[name])); setError("");
    }}>{name}</button>)}</div>
    <form onSubmit={event => {
      event.preventDefault();
      try {
        const initial = JSON.parse(value);
        change({...properties, [key]:initial});
        setKey(""); setValue('""'); setError("");
      } catch { setError('Enter a JSON default, such as "hello", [], or null.'); }
    }}>
      <label>Property name<input required maxLength={80} pattern="[A-Za-z_][A-Za-z0-9_]*" value={key}
        onChange={e => setKey(e.target.value)} /></label>
      <label>Default value (JSON)<input required value={value} onChange={e => setValue(e.target.value)} /></label>
      <button>{Object.hasOwn(properties, key) ? "Update property" : "Add property"}</button>
    </form>
    {error && <p role="alert">{error}</p>}
  </details>;
}

export function StateCard({data}: NodeProps<StateItem>) {
  const def = data.stateDefinition;
  return <div className="item-card">
    <Handle type="target" position={Position.Left} className="flow-port" />
    <div><span className="item-kind">State · {def.scope}</span><strong>{data.label}</strong>
      <small>{def.operation} · {def.key}</small>
      {def.includeChatTurn && (def.captureMemories ?? true) && <small>Save long-term memories</small>}</div>
    <Handle type="source" position={Position.Right} className="flow-port" />
  </div>;
}

export function StateInspector({data, properties, ports, agents, change}: {data: StateItem["data"];
  properties: Record<string, unknown>; ports: Port[]; agents: AgentDefinition[]; change: (patch: Partial<StateItem["data"]>) => void}) {
  const def = data.stateDefinition;
  const update = (patch: Partial<StateDefinition>) => change({stateDefinition: {...def, ...patch}});
  const names = Object.keys(properties);
  const generates = def.operation === "generate" || def.operation === "generateObject";
  const mapsResult = def.operation === "generateObject";
  const agent = agents.find(item => item.id === def.agentId);
  const sources = mapsResult ? [{id:"result", name:"Agent result", base: agent?.outputSchema && sampleObject(agent.outputSchema.fields)}] : ports;
  const mappings = def.mappings ?? [];
  function editMapping(index: number, patch: Partial<Rule>) {
    update({mappings: mappings.map((rule, i) => i === index ? {...rule, ...patch} : rule)});
  }
  const propertyOptions = names.map(name => <option key={name} value={name}>{name}</option>);
  return <>
    <label>State key<input value={def.key} maxLength={80} onChange={e => update({key:e.target.value})} /></label>
    <label>Scope<select value={def.scope} onChange={e => update({scope:e.target.value as StateDefinition["scope"]})}>
      <option value="run">This run</option><option value="conversation">This conversation</option>
    </select></label>
    <label>Operation<select value={def.operation} onChange={e => update({operation:e.target.value as StateDefinition["operation"],
      includeChatTurn: ["generate", "generateObject"].includes(e.target.value) && def.includeChatTurn, mappings: []})}>
      <option value="read">Read</option><option value="update">Update fields</option><option value="replace">Replace object</option>
      <option value="map">Map input fields</option><option value="generate">Generate a property with an agent</option>
      <option value="generateObject">Map a structured agent result</option>
    </select></label>
    {generates && <>
      <label>Agent<select value={def.agentId ?? ""} onChange={e => update({agentId:e.target.value || null})}>
        <option value="">Choose an agent…</option>
        {agents.filter(item => !mapsResult || item.outputSchema).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <p>The agent receives the current State and all inputs keyed by wire name.
        {mapsResult && " Choose an agent with structured object output."}</p>
      <label><input type="checkbox" checked={def.includeChatTurn ?? false}
        onChange={e => update({includeChatTurn:e.target.checked})} />Include completed chat turn</label>
      <p>Connect API Response to this State. The agent receives prior history and the latest user message and assistant reply as a separate pair.</p>
      {def.includeChatTurn && <>
        <label><input type="checkbox" checked={def.captureMemories ?? true}
          onChange={e => update({captureMemories:e.target.checked})} />Save long-term memories</label>
        <p>After updating context, this agent selects facts and preferences from the same completed turn.
          They are saved for future chats with this user. Inspect this node's run events to see the selected memories.</p>
      </>}
    </>}
    {def.operation === "generate" && <label>Write result to<select value={def.property ?? ""} onChange={e => update({property:e.target.value})}>
      <option value="">Choose a State property…</option>{propertyOptions}
    </select></label>}
    {(def.operation === "map" || mapsResult) && <fieldset><legend>Property mappings</legend>
      {mappings.map((rule, index) => <div key={index} className="state-mapping">
        <label>Source<select value={rule.edgeId} onChange={e => editMapping(index, {edgeId:e.target.value, source:".value"})}>
          <option value="">Choose an input…</option>{sources.map(port => <option key={port.id} value={port.id}>{port.name}</option>)}
        </select></label>
        <label>Source field<input list={`state-fields-${index}`} value={rule.source}
          onChange={e => editMapping(index, {source:e.target.value})} /></label>
        <datalist id={`state-fields-${index}`}><option value=".value" /><option value=".text" /><option value=".json" />
          {fieldPaths(sources.find(port => port.id === rule.edgeId)?.base).map(path => <option key={path} value={".value"+path} />)}
        </datalist>
        <label>State property<select value={rule.target} onChange={e => editMapping(index, {target:e.target.value})}>
          <option value="">Choose a property…</option>{names.map(name => <option key={name} value={pathFor(name)}>{name}</option>)}
        </select></label>
        <button type="button" onClick={() => update({mappings:mappings.filter((_, i) => i !== index)})}>Remove mapping</button>
      </div>)}
      <button type="button" onClick={() => update({mappings:[...mappings, {edgeId:sources[0]?.id ?? "", source:".value",
        target:names.length ? pathFor(names.find(name => !mappings.some(m => m.target === pathFor(name))) ?? names[0]) : ""}]})}>Add mapping</button>
      <p>Use .value for the whole input, .value/field for a property, or .json/field for JSON text.</p>
    </fieldset>}
    <label>Output<select value={def.output ?? "state"} onChange={e => update({output:e.target.value as StateDefinition["output"]})}>
      <option value="input">Pass through input</option><option value="property">State property</option><option value="state">Whole State object</option>
    </select></label>
    {def.output === "input" && <label>Pass through<select value={def.outputWire ?? ""} onChange={e => update({outputWire:e.target.value})}>
      <option value="">Original input (named object for multiple inputs)</option>
      {ports.map(port => <option key={port.id} value={port.id}>{port.name}</option>)}
    </select></label>}
    {def.output === "property" && <label>Output property<select value={def.outputProperty ?? ""} onChange={e => update({outputProperty:e.target.value})}>
      <option value="">Choose a property…</option>{propertyOptions}
    </select></label>}
    <details><summary>Initial values for this key</summary>
    <label>Initial object<textarea value={data.initialText ?? JSON.stringify(def.initial, null, 2)} onChange={e => {
      const initialText = e.target.value;
      try {
        const initial = JSON.parse(initialText);
        if (!initial || typeof initial !== "object" || Array.isArray(initial)) throw new Error();
        change({initialText, initialError:false, stateDefinition:{...def, initial}});
      } catch { change({initialText, initialError:true}); }
    }} /></label>
    {data.initialError && <p role="alert">Enter a valid JSON object.</p>}
    </details>
    {!names.length && <p>Open State object above the canvas to declare properties.</p>}
  </>;
}
