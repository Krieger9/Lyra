import { useState } from "react";

type JsonObject = Record<string, unknown>;
type Rule = { edgeId: string; source: string; target: string };
export type ObjectDefinition = { base: JsonObject; mappings: Rule[] };
export type JsonNodeData = {
  label: string;
  objectDefinition?: ObjectDefinition | null;
  baseText?: string;
  baseError?: string;
};
type Port = { id: string; label: string; base?: JsonObject; structured: boolean };

// Suggest existing fields, including fields with slashes or tildes in their names.
function fieldPaths(base: JsonObject) {
  const paths: string[] = [];
  function visit(value: unknown, prefix: string) {
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (paths.length >= 500) return;
      const path = prefix + "/" + key.replaceAll("~", "~0").replaceAll("/", "~1");
      paths.push(path);
      visit(child, path);
    }
  }
  visit(base, "");
  return paths;
}

export function JsonMappingInspector({ data, ports, onChange }: {
  data: JsonNodeData; ports: Port[]; onChange: (patch: Partial<JsonNodeData>) => void;
}) {
  const [samples, setSamples] = useState<Record<string, string>>({});
  const [formats, setFormats] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState({ key: "", result: "", error: "" });
  const [busy, setBusy] = useState(false);
  const definition = data.objectDefinition;
  if (!definition) return <section>
    <h3>Object definition</h3>
    <button type="button" onClick={() => onChange({ objectDefinition: { base: {}, mappings: [] } })}>Define object</button>
  </section>;

  const { base, mappings } = definition;
  const targets = fieldPaths(base);
  const sampleText = (port: Port) => samples[port.id] ?? (port.structured ? JSON.stringify(port.base ?? {}, null, 2) : "");
  const format = (port: Port) => formats[port.id] ?? (port.structured ? "json" : "text");
  const previewKey = JSON.stringify([definition, data.baseError, ports.map(port => [port.id, format(port), sampleText(port)])]);

  function updateBase(text: string) {
    try {
      const parsed = JSON.parse(text);
      if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("Use an object as the base.");
      onChange({ baseText: text, baseError: "", objectDefinition: { base: parsed, mappings } });
    } catch (error) {
      onChange({ baseText: text, baseError: error instanceof Error ? error.message : "Invalid JSON." });
    }
  }
  function setRules(rules: Rule[]) { onChange({ objectDefinition: { base, mappings: rules } }); }
  function changeRule(index: number, patch: Partial<Rule>) {
    setRules(mappings.map((rule, position) => position === index ? { ...rule, ...patch } : rule));
  }
  async function runPreview() {
    setBusy(true);
    try {
      const inputs = Object.fromEntries(ports.map(port => {
        let sample = sampleText(port);
        if (format(port) === "json") {
          try { sample = JSON.parse(sample); }
          catch { throw new Error("Enter valid sample JSON for " + port.label + "."); }
        }
        return [port.id, sample];
      }));
      const output = await window.lyraAdminRequest<JsonObject>("/api/admin/flows/preview-json", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ definition, inputs }),
      });
      setPreview({ key: previewKey, result: JSON.stringify(output, null, 2), error: "" });
    } catch (error) {
      setPreview({ key: previewKey, result: "", error: error instanceof Error ? error.message : "Preview failed." });
    } finally { setBusy(false); }
  }
  return <section className="json-mapper" aria-label="Object definition">
    <h3>Object definition</h3>
    <label htmlFor="json-base">Base JSON object</label>
    <textarea id="json-base" spellCheck={false} rows={6} value={data.baseText ?? JSON.stringify(base, null, 2)}
      onChange={event => updateBase(event.target.value)} />
    {data.baseError && <p role="alert">{data.baseError}</p>}
    <p>Keep literal defaults here. Use <code>null</code> as a placeholder for a mapped field.</p>
    <h3>Field mappings</h3>
    <p><code>.text</code> takes the whole input as text. <code>.json</code> copies its JSON value.
      Use <code>.json/session/id</code> to read a field or <code>.json/items/0</code> for an array entry.</p>
    <datalist id="json-targets">{targets.map(path => <option key={path} value={path} />)}</datalist>
    {mappings.map((rule, index) => {
      const port = ports.find(port => port.id === rule.edgeId);
      return <fieldset key={index}>
        <legend>Mapping {index + 1}</legend>
        <label>Incoming wire<select value={rule.edgeId} onChange={event => changeRule(index, { edgeId: event.target.value })}>
          {!port && <option value={rule.edgeId}>Choose a connected wire</option>}
          {ports.map(port => <option key={port.id} value={port.id}>{port.label} · {port.id.slice(0, 8)}</option>)}
        </select></label>
        <label>Read expression<input value={rule.source} list={`json-reads-${index}`} spellCheck={false}
          onChange={event => changeRule(index, { source: event.target.value })} /></label>
        <datalist id={`json-reads-${index}`}>
          <option value=".text" /><option value=".json" />
          {port?.base && fieldPaths(port.base).map(path => <option key={path} value={".json" + path} />)}
        </datalist>
        <label>Write to field<input value={rule.target} list="json-targets" placeholder="/session/id" spellCheck={false}
          onChange={event => changeRule(index, { target: event.target.value })} /></label>
        <button type="button" onClick={() => setRules(mappings.filter((_, position) => position !== index))}>Remove mapping</button>
      </fieldset>;
    })}
    <button type="button" disabled={!ports.length || !targets.length || mappings.length >= 64 || Boolean(data.baseError)}
      onClick={() => setRules([...mappings, { edgeId: ports[0].id, source: ".text",
        target: targets.find(path => !mappings.some(rule => rule.target === path)) ?? "" }])}>Add mapping</button>
    {(!ports.length || !targets.length) && <p>Add a base field and connect an input to map values.</p>}
    <details><summary>Preview with sample inputs</summary>
      {ports.map(port => <fieldset key={port.id}><legend>{port.label} · {port.id.slice(0, 8)}</legend>
        <label>Sample format<select value={format(port)} onChange={event => setFormats({ ...formats, [port.id]: event.target.value })}>
          <option value="text">Text</option><option value="json">JSON</option>
        </select></label>
        <label>Sample input<textarea rows={3} value={sampleText(port)} onChange={event => setSamples({ ...samples, [port.id]: event.target.value })} /></label>
      </fieldset>)}
      <button type="button" disabled={busy || Boolean(data.baseError)} onClick={runPreview}>Preview output</button>
      {preview.key === previewKey && (preview.error ? <p role="alert">{preview.error}</p> : <pre aria-label="Mapped output">{preview.result}</pre>)}
    </details>
  </section>;
}
