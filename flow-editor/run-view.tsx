import { useEffect, useState } from "react";
import { Background, Controls, Position, ReactFlow, ReactFlowProvider, type Edge, type Node } from "@xyflow/react";

type Graph = { nodes: { id: string; type: string; position: { x: number; y: number }; data: { label: string } }[];
  edges: (Edge & { name?: string | null })[] };
type Document = { id: string; name: string; revision: number; graph: Graph };
type Entry = { seq: number; node: string | null; kind: string; at: string; elapsed_ms: number; data: Record<string, unknown> };
type Summary = { id: string; source: string; revision: number; status: string; started_at: string };
type RecordPage = Summary & { snapshot?: Document; events: Entry[]; hasMore: boolean; result: unknown; error?: string };
type Binding = Pick<Document, "id" | "name" | "revision">;
const api = <T,>(path: string, options?: RequestInit) => window.lyraAdminRequest<T>("/api/admin" + path, options);
const json = (method: string, body: unknown) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const message = (failure: unknown) => failure instanceof Error ? failure.message : "Request failed.";

function eventLabel(event: Entry) {
  const data = event.data;
  switch (event.kind) {
    case "gate.evaluated": return `${data.passed ? "Pass" : "Fail"} · ${data.field ?? "output"} · ${data.operation}`;
    case "mapping.applied": return `${data.source} → ${data.target}`;
    case "template.started": return `Render ${data.part} template`;
    case "template.rendered": return `Rendered ${data.part}`;
    case "schema.inserted": return "Insert output schema";
    case "model.request": return "Send rendered prompt";
    case "model.response": return `Model reply · ${data.duration_ms} ms`;
    case "provider.request": return "Ollama request body";
    case "provider.response": return `Ollama response · HTTP ${data.status}`;
    default: return event.kind;
  }
}

function download(run: RecordPage) {
  const url = URL.createObjectURL(new Blob([pretty(run)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "lyra-run-" + run.id + ".json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function RunView({ design, onClose }: { design: Document; onClose: () => void }) {
  const [input, setInput] = useState("");
  const [history, setHistory] = useState<Summary[]>([]);
  const [runId, setRunId] = useState(new URLSearchParams(location.search).get("run") ?? "");
  const [run, setRun] = useState<RecordPage>();
  const [nodeId, setNodeId] = useState("");
  const [binding, setBinding] = useState<Binding | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let live = true;
    Promise.all([api<Summary[]>("/runs?flow_id=" + design.id), api<Binding | null>("/chat-flow")])
      .then(([items, active]) => { if (live) { setHistory(items); setBinding(active); } })
      .catch(e => { if (live) setError(message(e)); });
    return () => { live = false; };
  }, [design.id, refresh]);

  useEffect(() => {
    if (!runId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    let after = 0;
    let record: RecordPage;
    async function poll() {
      try {
        const page = await api<RecordPage>(`/runs/${encodeURIComponent(runId)}?after=${after}&include_snapshot=${after === 0}`);
        if (!live) return;
        if (page.snapshot && page.snapshot.id !== design.id) { setError("The selected run belongs to another flow."); return; }
        record = { ...record, ...page, snapshot: page.snapshot ?? record?.snapshot,
          events: [...(record?.events ?? []), ...page.events] };
        after = record.events.at(-1)?.seq ?? 0;
        setRun(record);
        if (page.hasMore || page.status === "running") timer = setTimeout(poll, page.hasMore ? 0 : 450);
        else setRefresh(n => n+1);
      } catch (e) {
        if (live) { setError(message(e)); timer = setTimeout(poll, 3000); }
      }
    }
    void poll();
    return () => { live = false; clearTimeout(timer); };
  }, [runId, design.id]);

  function open(id: string) {
    const url = new URL(location.href);
    if (id) url.searchParams.set("run", id);
    else url.searchParams.delete("run");
    window.history.replaceState(window.history.state, "", url);
    if (id !== runId) { setRun(undefined); setNodeId(""); setRunId(id); }
    setError("");
  }
  async function start() {
    setBusy(true);
    setError("");
    try {
      const created = await api<{id: string}>(`/flows/${design.id}/runs`, json("POST", {input, revision: design.revision}));
      open(created.id);
      setRefresh(n => n+1);
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  const isActive = binding?.id === design.id && binding.revision === design.revision;
  async function changeActivation() {
    setBusy(true);
    setError("");
    try {
      if (isActive) {
        await api(`/chat-flow/${design.id}?revision=${design.revision}`, {method:"DELETE"});
        setBinding(null);
      } else {
        setBinding(await api<Binding>("/chat-flow/" + design.id, json("PUT", {revision:design.revision})));
      }
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  const doc = run?.snapshot ?? design;
  const entries = run?.events ?? [];
  const states = new Map<string, string>();
  for (const e of entries) {
    if (e.node && e.kind.startsWith("node.")) states.set(e.node, e.kind.slice(5));
  }
  const nodes: Node[] = doc.graph.nodes.map(node => {
    const state = states.get(node.id) ?? (run && run.status !== "running" ? "skipped" : "pending");
    return {id:node.id, position:node.position, sourcePosition:Position.Right, targetPosition:Position.Left,
      data:{label:<><strong>{node.data.label || node.type}</strong><small>{state}</small></>},
      className:"trace-node trace-" + state, selected:node.id === nodeId};
  });
  const selected = doc.graph.nodes.find(node => node.id === nodeId);
  const visible = entries.filter(e => !nodeId || e.node === nodeId);
  const first = visible.find(e => e.kind === "node.started");
  const last = visible.find(e => e.kind === "node.completed");
  const calls = visible.filter(e => e.kind === "model.response");
  const tokenCounts = calls.map(e => (e.data.usage as { total_token_count?: number } | null)?.total_token_count);
  return <div className="run-workspace">
    <header className="run-toolbar"><button onClick={onClose}>Back to design</button>
      <h2>{design.name}</h2><span>Run view · saved revision {design.revision}</span>
      <label>Recent runs <select value={runId} onChange={e => open(e.target.value)}>
        <option value="">Choose a run…</option>
        {runId && !history.some(r => r.id === runId) && <option value={runId}>Selected run</option>}
        {history.map(r => <option key={r.id} value={r.id}>{new Date(r.started_at).toLocaleString()} · {r.source} · r{r.revision} · {r.status}</option>)}
      </select></label><button onClick={() => setRefresh(n => n+1)}>Refresh runs</button>
    </header>
    <div className="run-layout">
      <div className="flow-canvas" aria-label="Run diagram"><ReactFlowProvider><ReactFlow key={doc.id+":"+doc.revision}
        nodes={nodes} edges={doc.graph.edges.map(e => ({...e,label:e.name,animated:states.get(e.target)==="started"}))}
        nodesConnectable={false} nodesDraggable={false} deleteKeyCode={null} fitView minZoom={0.1}
        onNodeClick={(_, n) => setNodeId(n.id)} onPaneClick={() => setNodeId("")}>
        <Background/><Controls showInteractive={false}/></ReactFlow></ReactFlowProvider></div>
      <aside className="flow-inspector run-inspector">
        <h3>Run saved flow</h3><label>Raw input<textarea rows={3} maxLength={8000} value={input} onChange={e=>setInput(e.target.value)}/></label>
        <button disabled={busy} onClick={start}>Run revision {design.revision}</button>
        <details><summary>Chat connection</summary><p>{binding ? `Active: ${binding.name} · revision ${binding.revision}` : "Chat uses the default prompt."}</p>
          <button disabled={busy} onClick={changeActivation}>{isActive ? "Disconnect from chat" : "Use this revision in Lyra chat"}</button></details>
        {error && <p role="alert">{error}</p>}
        {run && <><h3>Run result</h3><p aria-live="polite">{run.status} · {run.source} · revision {run.revision} · {entries.at(-1)?.elapsed_ms ?? 0} ms</p>
          <small>{run.id}</small>{run.error && <p role="alert">{run.error}</p>}
          {run.status==="completed" && <pre>{pretty(run.result)}</pre>}
          <button disabled={run.status==="running" || run.hasMore} onClick={()=>download(run)}>Export run JSON</button>
          <h3>{selected?.data.label || "Execution timeline"}</h3>
          {selected && <button onClick={()=>setNodeId("")}>Show all steps</button>}
          {calls.length > 0 && <p>{calls.length} model {calls.length === 1 ? "call" : "calls"} · {calls.reduce((sum,e)=>sum+Number(e.data.duration_ms),0)} ms
            {tokenCounts.every(n=>typeof n === "number") && ` · ${tokenCounts.reduce<number>((sum,n)=>sum+(n ?? 0),0)} tokens`}</p>}
          {selected && first && <details><summary>Incoming wire values</summary><pre>{pretty(first.data.inputs)}</pre></details>}
          {selected && last && <details><summary>Node output · {String(last.data.duration_ms)} ms</summary><pre>{pretty(last.data.output)}</pre></details>}
          {selected && !visible.length && <p>This node has no recorded events.</p>}
          {visible.map(e => <details key={e.seq}><summary>#{e.seq} · {eventLabel(e)} · +{e.elapsed_ms} ms</summary>
            <small>{e.at} · {doc.graph.nodes.find(n=>n.id===e.node)?.data.label ?? "Flow"}</small><pre>{pretty(e.data)}</pre></details>)}
        </>}
      </aside>
    </div></div>;
}
