import { useState } from "react";
import type { AgentDefinition, ValueDefinition } from "./catalog";
import { useSampleInput, type InputSelection } from "./chat-input";

function Shape({ value }: { value: ValueDefinition }) {
  const details = value.type === "object" ? <ul>{value.fields.map(field =>
    <li key={field.name}><code>{field.name}</code> · {field.required ? "Required" : "Optional"} · <Shape value={field} /></li>
  )}</ul> : value.type === "array" ? <> of <Shape value={value.items} /></> : null;
  return <>{value.type === "choice" ? value.choices.join(" | ") : value.type}{details}
    {value.description && <p>{value.description}</p>}</>;
}

export function AgentOutput({ agent, editUrl, selection }: {
  agent: AgentDefinition; editUrl: string; selection?: InputSelection | null;
}) {
  const [chatSample, setChatSample] = useState(false);
  const {input, controls} = useSampleInput(Boolean(selection) || chatSample);
  const [wireJson, setWireJson] = useState("{}");
  const usesChat = selection?.source === "chat";
  const sampleKey = JSON.stringify([input, selection, usesChat && wireJson]);
  const [testedBody, setTestedBody] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function test() {
    setBusy(true);
    setTestedBody(sampleKey);
    setResult(null);
    setError("");
    try {
      let wire = input;
      if (usesChat) {
        try { wire = JSON.parse(wireJson); }
        catch { throw new Error("Enter the sample incoming wire value as JSON."); }
      }
      const { output } = await window.lyraAdminRequest<{ output: string | Record<string, unknown> }>(
        "/api/admin/agents/" + agent.id + "/test", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ input: wire, inputSelection: selection, chatContext: usesChat ? input : undefined }) });
      setResult(typeof output === "string" ? output : JSON.stringify(output, null, 2));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Agent test failed.");
    } finally { setBusy(false); }
  }
  return <section>
    <h3>Output</h3>
    {agent.outputSchema ? <Shape value={{ type: "object", fields: agent.outputSchema.fields, choices: [], description: "" }} />
      : <p>Text</p>}
    <a href={editUrl}>Edit output</a>
    <details><summary>Test agent</summary>
      <fieldset disabled={busy}>
        {!selection && <label>Sample format<select value={chatSample ? "chat" : "text"}
          onChange={e => setChatSample(e.target.value === "chat")}>
          <option value="text">Text</option><option value="chat">Chat request</option>
        </select></label>}
        {controls}
        {usesChat && <label>Incoming wire value (JSON)<textarea rows={3} value={wireJson}
          onChange={e => setWireJson(e.target.value)} /></label>}
      </fieldset>
      <button type="button" onClick={test} disabled={busy}>{busy ? "Running…" : "Run test"}</button>
      {testedBody === sampleKey && error && <p role="alert">{error}</p>}
      {testedBody === sampleKey && result !== null && <pre aria-label="Agent output" aria-live="polite">{result}</pre>}
    </details>
  </section>;
}
