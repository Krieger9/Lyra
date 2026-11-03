import { useState } from "react";
import type { AgentDefinition } from "./catalog";

export function AgentOutput({ agent, editUrl }: { agent: AgentDefinition; editUrl: string }) {
  const [input, setInput] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function test() {
    setBusy(true);
    setResult(null);
    setError("");
    try {
      const { output } = await window.lyraAdminRequest<{ output: string | Record<string, unknown> }>(
        "/api/admin/agents/" + agent.id + "/test", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ input }) });
      setResult(typeof output === "string" ? output : JSON.stringify(output, null, 2));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Agent test failed.");
    } finally { setBusy(false); }
  }
  return <section>
    <h3>Output</h3>
    {agent.outputSchema ? <><p>Structured object</p><ul>{agent.outputSchema.fields.map(field =>
      <li key={field.name}><code>{field.name}</code> · {field.type === "choice" ? field.choices.join(" | ") : "Text"}
        {" · "}{field.required ? "Required" : "Optional"}{field.description && <p>{field.description}</p>}</li>
    )}</ul></> : <p>Text</p>}
    <a href={editUrl}>Edit output</a>
    <details><summary>Test agent</summary>
      <label>Sample input<textarea rows={3} maxLength={8000} value={input} disabled={busy}
        onChange={event => { setInput(event.target.value); setResult(null); setError(""); }} /></label>
      <button type="button" onClick={test} disabled={busy}>{busy ? "Running…" : "Run test"}</button>
      {error && <p role="alert">{error}</p>}
      {result !== null && <pre aria-label="Agent output" aria-live="polite">{result}</pre>}
    </details>
  </section>;
}
