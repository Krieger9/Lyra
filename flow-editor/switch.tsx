import { useEffect, useState } from "react";
import { Handle, Position, useUpdateNodeInternals, type Node, type NodeProps } from "@xyflow/react";
import { otherwise, ports } from "./graph";

type Check = { field: string; operator: "empty" | "notEmpty" | "equals" | "notEquals"; value?: string | null };
type Path = { id: string; label: string; match: "all" | "any"; checks: Check[] };
export type SwitchDefinition = { paths: Path[] };
export type SwitchItem = Node<{ label: string; switchDefinition: SwitchDefinition }, "switch">;
const emptyPath = (n: number): Path => ({ id: crypto.randomUUID(), label: "Path " + n, match: "all", checks: [] });
export const newSwitch = (): SwitchDefinition => ({ paths: [emptyPath(1)] });

export function SwitchCard({ id, data }: NodeProps<SwitchItem>) {
  const update = useUpdateNodeInternals();
  useEffect(() => { update(id); }, [id, data, update]);
  return <div className="item-card switch-card">
    <Handle type="target" position={Position.Left} className="flow-port" title="Switch input" />
    <span className="item-kind">Switch · First match</span><strong>{data.label}</strong>
    {ports(data).map(port => <div className="switch-port" key={port.id}>{port.label || "Unnamed path"}
      <Handle type="source" position={Position.Right} id={port.id} title={port.label} className="flow-port" />
    </div>)}
  </div>;
}

const operations = { empty: "is empty", notEmpty: "is not empty", equals: "equals", notEquals: "does not equal" };
const freshCheck = (field: string, choices: string[]): Check => choices.length
  ? { field, operator: "equals", value: choices[0] } : { field, operator: "notEmpty" };

export function SwitchInspector({ node, graphBody, catalogVersion, change }: {
  node: SwitchItem; graphBody: string; catalogVersion: string;
  change: (definition: SwitchDefinition, removed?: string) => void;
}) {
  const [fields, setFields] = useState<Record<string, string[]>>({});
  const [status, setStatus] = useState("");
  const paths = node.data.switchDefinition?.paths ?? [];
  useEffect(() => {
    let live = true;
    setFields({});
    setStatus("Loading input fields…");
    window.lyraAdminRequest<Record<string, string[]>>("/api/admin/flows/switch-fields/" + encodeURIComponent(node.id),
      { method: "POST", headers: { "Content-Type": "application/json" }, body: graphBody }).then(result => {
        if (live) { setFields(result); setStatus(Object.keys(result).length ? "" : "Connect an input with text or choice fields."); }
      }).catch(error => { if (live) setStatus(error.message); });
    return () => { live = false; };
  }, [node.id, graphBody, catalogVersion]);
  const keys = Object.keys(fields);
  const edit = (id: string, patch: Partial<Path>) => change({ paths: paths.map(p => p.id === id ? { ...p, ...patch } : p) });
  function move(index: number, offset: number) {
    const ordered = [...paths];
    ordered.splice(index + offset, 0, ...ordered.splice(index, 1));
    change({ paths: ordered });
  }
  return <section className="switch-inspector">
    <h3>Routing paths</h3><p>The first matching path receives the original input.</p>
    {status && <p role="status">{status}</p>}
    {paths.map((path, i) => <fieldset key={path.id}><legend>Path {i + 1}</legend>
      <label>Path name<input value={path.label} maxLength={120} onChange={e => edit(path.id, {label: e.target.value})} /></label>
      <label>Match<select value={path.match} onChange={e => edit(path.id, { match: e.target.value as Path["match"] })}>
        <option value="all">All checks</option><option value="any">Any check</option></select></label>
      {path.checks.map((check, index) => {
        const choices = fields[check.field];
        const operators: Check["operator"][] = choices?.length ? ["equals", "notEquals"] : ["empty", "notEmpty"];
        const update = (next: Check) => edit(path.id, { checks: path.checks.map((old, j) => j === index ? next : old) });
        return <div className="switch-check" key={index}>
          <label>Field<select value={check.field} onChange={e => update(freshCheck(e.target.value, fields[e.target.value]))}>
            {!choices && <option value={check.field}>{check.field || "Whole input"} (unavailable)</option>}
            {keys.map(key => <option key={key} value={key}>{key || "Whole input"}</option>)}</select></label>
          <label>Operator<select value={check.operator} onChange={e => update({ ...check, operator: e.target.value as Check["operator"],
            value: choices?.length ? check.value ?? choices[0] : null })}>
            {!operators.includes(check.operator) && <option value={check.operator}>{operations[check.operator]} (unavailable)</option>}
            {operators.map(op => <option key={op} value={op}>{operations[op]}</option>)}</select></label>
          {(check.operator === "equals" || check.operator === "notEquals") && <label>Choice<select value={check.value ?? ""}
            onChange={e => update({ ...check, value: e.target.value })}>
            {!choices?.includes(check.value ?? "") && <option value={check.value ?? ""}>{check.value || "Select a choice"} (unavailable)</option>}
            {choices?.map(v => <option key={v}>{v}</option>)}</select></label>}
          <button onClick={() => edit(path.id, { checks: path.checks.filter((_, j) => j !== index) })}>Remove check</button>
        </div>;
      })}
      <button disabled={!keys.length || path.checks.length >= 32} onClick={() => edit(path.id, { checks: [...path.checks,
        freshCheck(keys[0], fields[keys[0]])] })}>Add check</button>
      <div className="switch-actions"><button disabled={!i} onClick={() => move(i, -1)}>Move up</button>
        <button disabled={i === paths.length - 1} onClick={() => move(i, 1)}>Move down</button>
        <button onClick={() => change({ paths: paths.filter(p => p.id !== path.id) }, path.id)}>Delete path</button></div>
    </fieldset>)}
    <button disabled={paths.length >= 32} onClick={() => change({ paths: [...paths, emptyPath(paths.length + 1)] })}>Add path</button>
    <p><strong>Otherwise</strong> receives the input if no path matches.</p>
  </section>;
}
