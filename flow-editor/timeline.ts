export type Entry = { seq: number; node: string | null; kind: string; at: string; elapsed_ms: number; data: Record<string, unknown> };
export type TraceNode = { id: string; type: string; data: { label: string } };
export type Step = { key: string; label: string; type: string; events: Entry[] };

export function isSetup(event: Entry) {
  return event.kind === "run.started" || event.kind === "plan.ready";
}

export function groupTimeline(events: Entry[], nodes: TraceNode[]) {
  const steps = new Map<string, Step>();
  const byId = new Map(nodes.map(node => [node.id, node]));
  for (const event of events) {
    const { kind, node } = event;
    if (isSetup(event)) continue;
    const graphNode = node ? byId.get(node) : undefined;
    let key = "node:" + node;
    let label = graphNode?.data.label || node || "Flow";
    let type = graphNode?.type || "node";
    // A failed run can still have a node ID on its commit events.
    if (kind.startsWith("run.") || kind === "memory.committed" || kind === "identity.committed") {
      key = type = "finish";
      label = "Finish run";
    } else if (!node && /^(provider\.|identity\.(tool|reflected|reflection_skipped)$)/.test(kind)) {
      key = type = "reflection";
      label = "Identity reflection";
    } else if (!node) {
      key = type = "flow";
    }
    let step = steps.get(key);
    if (!step) {
      step = { key, label, type, events: [] };
      steps.set(key, step);
    }
    step.events.push(event);
  }
  return [...steps.values()];
}

function quantity(value: unknown, singular: string, plural: string) {
  return `${value} ${value === 1 ? singular : plural}`;
}

export function eventLabel({ kind, data }: Entry): string {
  switch (kind) {
    case "personality.rendered": return `Personality · ${data.name} · revision ${data.revision}`;
    case "memory.recalled": return `Recalled ${quantity(data.count ?? (data.ids as unknown[] | undefined)?.length ?? 0, "memory", "memories")}`;
    case "memory.extracted": return `Selected ${quantity(data.count, "memory", "memories")} to save`;
    case "memory.committed": return `Committed ${quantity(data.count, "memory selection", "memory selections")}`;
    case "identity.context": return "Loaded identity context";
    case "identity.tool": return String(data.tool || "Identity tool");
    case "identity.reflected": return `${quantity(data.changes, "identity change", "identity changes")} proposed`;
    case "identity.reflection_skipped": return "Reflection skipped";
    case "identity.committed": return `Committed ${quantity(data.changes, "identity change", "identity changes")}`;
    case "switch.selected": return `Selected path · ${data.label}`;
    case "node.started": return "Read inputs";
    case "node.completed": return "Node output";
    case "node.skipped": return "Skipped · inactive input path";
    case "run.failed": case "node.failed": case "mapping.failed": return `Failed · ${data.error ?? kind}`;
    case "run.interrupted": return "Run interrupted";
    case "gate.evaluated": return `${data.passed ? "Pass" : "Fail"} · ${data.field ?? "output"} · ${data.operation}`;
    case "mapping.applied": return `${data.source} → ${data.target}`;
    case "state.applied": return `${data.operation === "read" ? "Read" : "Updated"} state · ${data.key}`;
    case "input.selected": return `${data.source === "chat" ? "Original chat" : "Incoming wire"} · ${data.priorTurns} prior turns`;
    case "template.started": return `Render ${data.part} template`;
    case "template.rendered": return `Rendered ${data.part}`;
    case "schema.inserted": return "Insert output schema";
    case "model.request": return "Send rendered prompt";
    case "model.response": return `Model reply · ${data.duration_ms} ms`;
    case "provider.request": return `${data.provider} request body`;
    case "provider.response": return `${data.provider} response · HTTP ${data.status}`;
    case "agent.trace": return "Agent trace";
    case "run.completed": return "Completed";
    case "plan.ready": return "Execution order";
    case "run.started": return "Run input";
    default: return kind;
  }
}

export function failed(event: Entry) {
  return event.kind.endsWith(".failed") || event.kind === "run.interrupted"
    || event.kind === "gate.evaluated" && event.data.passed === false
    || event.kind === "provider.response" && Number(event.data.status) >= 400;
}

type Phase = { label: string; events: Entry[] };
export function phasesFor(step: Step): Phase[] {
  const phases: Phase[] = [];
  for (const event of step.events) {
    const kind = event.kind;
    const last = phases.at(-1);
    let label = "";
    if (/^(template\.|input\.selected|schema\.inserted|personality\.rendered|identity\.context)/.test(kind)) label = "Prepare prompt";
    else if (/^(model\.|provider\.|identity\.tool|agent\.trace)/.test(kind)) label = "Model call";
    else if (kind === "gate.evaluated") label = "Validate output";
    else if (step.type === "state" && /^(mapping\.|state\.applied)/.test(kind)) label = event.data.operation === "read" ? "Read state" : "Update state";
    else if (kind === "memory.extracted") {
      label = "Extract memories";
      if (last?.label === "Model call") last.label = label;
    }
    const newCall = kind === "model.request" || (step.type === "reflection" && kind === "provider.request");
    if (label && last?.label === label && !newCall) last.events.push(event);
    else phases.push({ label, events: [event] });
  }
  return phases;
}

export function validationSummary(events: Entry[]) {
  const passed = events.filter(event => event.data.passed === true).length;
  const failure = events.find(failed);
  return [`${passed} of ${events.length} checks passed`, failure && eventLabel(failure)].filter(Boolean).join(" · ");
}

function preview(output: unknown) {
  const text = (typeof output === "string" ? output : JSON.stringify(output) ?? "").replace(/\s+/g, " ").trim();
  return text.length > 90 ? text.slice(0, 89) + "…" : text;
}

export function stepSummary(step: Step, active: boolean) {
  const events = step.events;
  const last = events[events.length - 1];
  const failure = events.find(failed);
  if (failure) return eventLabel(failure);
  if (active) return "Running · " + eventLabel(last);
  const parts = events.filter(e => ["memory.recalled", "memory.extracted", "state.applied", "switch.selected",
    "memory.committed", "identity.committed", "identity.reflected", "identity.reflection_skipped"].includes(e.kind)).map(eventLabel);
  if (step.type === "agent") {
    const request = events.find(e => e.kind === "provider.request");
    const model = (request?.data.body as {model?: string} | undefined)?.model ?? events.find(e => e.kind === "model.request")?.data.model;
    if (model) parts.unshift(String(model));
  }
  if (last.kind === "node.completed") {
    if (typeof last.data.duration_ms === "number") parts.push(`${last.data.duration_ms} ms`);
    if (step.type === "agent" || step.type === "apiResponse") parts.push(preview(last.data.output) || "Empty output");
  }
  return parts.filter(Boolean).join(" · ") || eventLabel(last);
}
