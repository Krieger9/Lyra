import { useEffect, useRef, useState } from "react";
import { Background, Controls, Position, ReactFlow, ReactFlowProvider, type Edge, type Node } from "@xyflow/react";
import type { SwitchDefinition } from "./switch";
import { otherwise } from "./graph";
import { useSampleInput, type InputSelection } from "./chat-input";

type Graph = { nodes: { id: string; type: string; position: { x: number; y: number }; data: { label: string; chatInput?: boolean; inputSelection?: InputSelection | null; switchDefinition?: SwitchDefinition | null } }[];
  edges: (Edge & { name?: string | null })[] };
type Document = { id: string; name: string; revision: number; graph: Graph;
  personality?: {id: string; name: string; revision: number; template: string; goals: string[];
    memories: {id: string; content: string; sourceMemoryId?: string | null}[]} | null };
type Entry = { seq: number; node: string | null; kind: string; at: string; elapsed_ms: number; data: Record<string, unknown> };
type Summary = { id: string; source: string; revision: number; status: string; started_at: string };
type MemoryLink = {id: string; content: string; relation: "created" | "reinforced" | "recalled"; node: string; evidence?: string};
type RecordPage = Summary & { snapshot?: Document; events: Entry[]; hasMore: boolean; result: unknown; error?: string;
  memories?: MemoryLink[] };
type Binding = Pick<Document, "id" | "name" | "revision"> & {personality?: {id: string; name: string; revision: number} | null};
const api = <T,>(path: string, options?: RequestInit) => window.lyraAdminRequest<T>("/api/admin" + path, options);
const json = (method: string, body: unknown) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const message = (failure: unknown) => failure instanceof Error ? failure.message : "Request failed.";

function eventLabel(event: Entry) {
  const data = event.data;
  switch (event.kind) {
    case "personality.rendered": return `Personality · ${data.name} · revision ${data.revision}`;
    case "memory.recalled": return `Recalled ${data.count ?? (data.ids as unknown[] | undefined)?.length ?? 0} memories`;
    case "memory.extracted": return `Selected ${data.count} memories to save`;
    case "memory.committed": return `Committed ${data.count} memory selections`;
    case "switch.selected": return `Selected path · ${data.label}`;
    case "node.skipped": return "Skipped · inactive input path";
    case "gate.evaluated": return `${data.passed ? "Pass" : "Fail"} · ${data.field ?? "output"} · ${data.operation}`;
    case "mapping.applied": return `${data.source} → ${data.target}`;
    case "input.selected": return `${data.source === "chat" ? "Original chat" : "Incoming wire"} · ${data.priorTurns} prior turns`;
    case "template.started": return `Render ${data.part} template`;
    case "template.rendered": return `Rendered ${data.part}`;
    case "schema.inserted": return "Insert output schema";
    case "model.request": return "Send rendered prompt";
    case "model.response": return `Model reply · ${data.duration_ms} ms`;
    case "provider.request": return `${data.provider} request body`;
    case "provider.response": return `${data.provider} response · HTTP ${data.status}`;
    default: return event.kind;
  }
}

// A wire ID identifies a connection even if the design has since been renamed.
function eventWires(event?: Entry): Set<unknown> | undefined {
  if (!event) return;
  if (event.kind === "switch.selected" && Array.isArray(event.data.wires)) return new Set(event.data.wires);
  if (event.kind === "mapping.applied") return new Set([event.data.wire]);
  if (event.kind === "mapping.failed") return new Set([(event.data.rule as { edgeId?: string } | null)?.edgeId]);
  if (event.kind === "node.started" && Array.isArray(event.data.inputs))
    return new Set(event.data.inputs.map(input => input?.wire));
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
  const exposeChat = Boolean(design.graph.nodes.find(n => n.type === "apiInput")?.data.chatInput);
  const usesChat = design.graph.nodes.some(n => n.type === "agent" && n.data.inputSelection?.source === "chat");
  const { input, controls } = useSampleInput(exposeChat || usesChat);
  const [history, setHistory] = useState<Summary[]>([]);
  const [runId, setRunId] = useState(new URLSearchParams(location.search).get("run") ?? "");
  const [run, setRun] = useState<RecordPage>();
  const [nodeId, setNodeId] = useState("");
  const [eventSeq, setEventSeq] = useState<number | null>(null);
  const timeline = useRef<HTMLHeadingElement>(null);
  const measurements = useRef(new Map<string, Node["measured"]>());
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
    if (id !== runId) { setRun(undefined); setNodeId(""); setEventSeq(null); setRunId(id); }
    setError("");
  }
  async function start() {
    setBusy(true);
    setError("");
    try {
      const wireInput = !exposeChat && typeof input !== "string" ? input.message : input;
      const created = await api<{id: string}>(`/flows/${design.id}/runs`, json("POST", {
        input: wireInput, chatContext: usesChat ? input : undefined, revision: design.revision
      }));
      open(created.id);
      setRefresh(n => n+1);
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
  const isActive = binding?.id === design.id && binding.revision === design.revision;
  async function changeActivation(reapply = false) {
    setBusy(true);
    setError("");
    try {
      if (isActive && !reapply) {
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
  const selectedPaths = new Map<string, unknown>();
  for (const e of entries) {
    if (e.node && e.kind.startsWith("node.")) states.set(e.node, e.kind.slice(5));
    if (e.node && e.kind === "switch.selected") selectedPaths.set(e.node, e.data.label);
  }
  const activeEvent = entries.find(e => e.seq === eventSeq);
  const focusId = activeEvent?.node ?? nodeId;
  const wires = eventWires(activeEvent);
  const neighbors = new Set<string>();
  const edges = doc.graph.edges.map(edge => {
    const incident = edge.source === focusId || edge.target === focusId;
    const highlighted = incident && (!wires || wires.has(edge.id));
    if (highlighted) neighbors.add(edge.source === focusId ? edge.target : edge.source);
    const path = edge.sourceHandle && [...(doc.graph.nodes.find(n => n.id === edge.source)?.data.switchDefinition?.paths ?? []),
      otherwise].find(p => p.id === edge.sourceHandle);
    // Run cards use one read-only handle. Keep the design's named port visible on the wire.
    return { ...edge, sourceHandle: null, label: path ? path.label + " · " + edge.name : edge.name, animated: states.get(edge.target) === "started",
      className: focusId ? (highlighted ? "trace-wire-active" : "trace-wire-muted") : undefined };
  });
  function selectNode(id: string) {
    setNodeId(id);
    setEventSeq(null);
    if (id) requestAnimationFrame(() => timeline.current?.scrollIntoView({ block: "nearest" }));
  }
  const nodes: Node[] = doc.graph.nodes.map(node => {
    const state = states.get(node.id) ?? (run && run.status !== "running" ? "skipped" : "pending");
    return {id:node.id, position:node.position, measured:measurements.current.get(node.id),
      sourcePosition:Position.Right, targetPosition:Position.Left,
      data:{label:<><strong>{node.data.label || node.type}</strong><small>{state}</small>
        {selectedPaths.has(node.id) && <small>Path · {String(selectedPaths.get(node.id))}</small>}</>},
      className:[`trace-node trace-${state}`, node.id === focusId ? "trace-focus" :
        neighbors.has(node.id) ? "trace-neighbor" : focusId ? "trace-muted" : ""].join(" "),
      selected:node.id === focusId, ariaRole:"button", ariaLabel:node.data.label || node.type,
      domAttributes:{ "aria-pressed":node.id === focusId, onKeyDown:(event: React.KeyboardEvent<HTMLDivElement>) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectNode(node.id); }
      }}};
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
        nodes={nodes} edges={edges}
        onNodesChange={changes => {
          // Retain measured handles as selection and live events rebuild the read-only nodes.
          for (const change of changes) {
            if (change.type === "dimensions" && change.dimensions) measurements.current.set(change.id, change.dimensions);
          }
        }}
        nodesConnectable={false} nodesDraggable={false} deleteKeyCode={null} fitView minZoom={0.1}
        onNodeClick={(_, n) => selectNode(n.id)} onPaneClick={() => selectNode("")}>
        <Background/><Controls showInteractive={false}/></ReactFlow></ReactFlowProvider></div>
      <aside className="flow-inspector run-inspector">
        <h3>Run saved flow</h3>{controls}
        <button disabled={busy} onClick={start}>Run revision {design.revision}</button>
        <details><summary>Chat connection</summary><p>{binding ? `Active: ${binding.name} · revision ${binding.revision}` : "Chat uses the default prompt."}</p>
          {binding?.personality && <p>Personality: {binding.personality.name} · revision {binding.personality.revision}</p>}
          <button disabled={busy} onClick={() => changeActivation()}>{isActive ? "Disconnect from chat" : "Use this revision in Lyra chat"}</button>
          {isActive && <button disabled={busy} onClick={() => changeActivation(true)}>Reactivate with latest personality and agents</button>}</details>
        {error && <p role="alert">{error}</p>}
        {run && <><h3>Run result</h3><p aria-live="polite">{run.status} · {run.source} · revision {run.revision} · {entries.at(-1)?.elapsed_ms ?? 0} ms</p>
          <small>{run.id}</small>{run.error && <p role="alert">{run.error}</p>}
          {run.status==="completed" && <pre>{pretty(run.result)}</pre>}
          <button disabled={run.status==="running" || run.hasMore} onClick={()=>download(run)}>Export run JSON</button>
          {run.snapshot?.personality && <>
            <h3>Personality snapshot</h3>
            <a href={`/admin/personalities/?id=${encodeURIComponent(run.snapshot.personality.id)}`}>{run.snapshot.personality.name}</a>
            <p>Revision {run.snapshot.personality.revision} · see personality events for the rendered prompt on each participating agent.</p>
            <details><summary>Saved prompt, goals and memories</summary><pre>{pretty(run.snapshot.personality)}</pre></details>
          </>}
          <h3>Memories associated with this turn</h3>
          {(run.memories ?? []).length ? <ul>{run.memories!.map(memory => <li key={`${memory.id}:${memory.relation}:${memory.node}`}>
            <strong>{memory.relation === "created" ? "Created" : memory.relation === "reinforced" ? "Reinforced" : "Recalled"}</strong>
            {" · "}<a href={`/admin/memories/?id=${encodeURIComponent(memory.id)}`}>{memory.content}</a>
            {memory.evidence && <blockquote>{memory.evidence}</blockquote>}
          </li>)}</ul> : <p>No committed memory links for this turn.</p>}
          <h3 ref={timeline}>{selected?.data.label || "Execution timeline"}</h3>
          {selected && <button onClick={()=>selectNode("")}>Show all steps</button>}
          {calls.length > 0 && <p>{calls.length} model {calls.length === 1 ? "call" : "calls"} · {calls.reduce((sum,e)=>sum+Number(e.data.duration_ms),0)} ms
            {tokenCounts.every(n=>typeof n === "number") && ` · ${tokenCounts.reduce<number>((sum,n)=>sum+(n ?? 0),0)} tokens`}</p>}
          {selected && first && <details><summary>Incoming wire values</summary><pre>{pretty(first.data.inputs)}</pre></details>}
          {selected && last && <details><summary>Node output · {String(last.data.duration_ms)} ms</summary><pre>{pretty(last.data.output)}</pre></details>}
          {selected && !visible.length && <p>This node has no recorded events.</p>}
          {visible.map(e => <details key={e.seq} open={e.seq === eventSeq} className="trace-step">
            <summary onClick={click => {
              click.preventDefault();
              setEventSeq(current => current === e.seq ? null : e.seq);
            }}>
              <strong>{doc.graph.nodes.find(n => n.id === e.node)?.data.label || e.node || "Flow"}</strong>
              {" · "}#{e.seq} · {eventLabel(e)} · +{e.elapsed_ms} ms
            </summary>
            <small>{e.at}</small><pre>{pretty(e.data)}</pre></details>)}
        </>}
      </aside>
    </div></div>;
}
