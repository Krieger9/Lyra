import { useState } from "react";
import type { Wire } from "./graph";


export function WireSettings({ wire, wires, jsonInput, change }: {
  wire: Wire; wires: Wire[]; jsonInput: boolean; change: (patch: Partial<Pick<Wire, "name" | "readAs">>) => void;
}) {
  const [name, setName] = useState(wire.name ?? "");
  const error = !/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(name)
    ? "Start with a letter or underscore. Use letters, digits and underscores."
    : wires.some(other => other.target === wire.target && other.id !== wire.id && other.name === name)
      ? "This node already has an input with that name." : "";
  return <div className="wire-settings">
    <form onSubmit={event => { event.preventDefault(); if (!error) change({ name }); }}>
      <label>Wire name<input value={name} maxLength={64} onChange={event => setName(event.target.value)} /></label>
      <button disabled={!!error || name === wire.name}>Apply name</button>
      {error && <small role="alert">{error}</small>}
    </form>
    {jsonInput && <label>Input interpretation<select value={wire.readAs ?? "value"}
      onChange={event => change({ readAs: event.target.value === "json" ? "json" : "value" })}>
      <option value="value">As received</option><option value="json">Read as JSON</option>
    </select></label>}
    <small>Connection ID</small><code className="connection-id">{wire.id}</code>
  </div>;
}
