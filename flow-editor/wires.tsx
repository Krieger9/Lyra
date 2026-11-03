import { useState } from "react";
import type { Edge } from "@xyflow/react";

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
