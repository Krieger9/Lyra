import { useState } from "react";
import { eventLabel, failed, groupTimeline, phasesFor, stepSummary, validationSummary, type Entry, type TraceNode } from "./timeline";

type Props = {
  entries: Entry[];
  nodes: TraceNode[];
  status: string;
  nodeId: string;
  selectedSeq: number | null;
  selectEvent: (seq: number | null) => void;
};

export function RunTimeline({ entries, nodes, status, nodeId, selectedSeq, selectEvent }: Props) {
  const [flat, setFlat] = useState(false);
  const visible = nodeId ? entries.filter(e => e.node === nodeId) : entries;
  const steps = groupTimeline(visible, nodes);
  const tail = entries.at(-1);

  function eventRow(event: Entry) {
    const open = selectedSeq === event.seq;
    return <details key={event.seq} open={open} className="trace-step">
      <summary onClick={click => { click.preventDefault(); selectEvent(open ? null : event.seq); }}>
        {flat && <strong>{nodes.find(node => node.id === event.node)?.data.label || event.node || "Flow"} · </strong>}
        #{event.seq} · {eventLabel(event)} · +{event.elapsed_ms} ms
      </summary>
      {open && <><small>{event.at}</small><pre>{JSON.stringify(event.data, null, 2)}</pre></>}
    </details>;
  }

  function clearHiddenSelection(element: HTMLDetailsElement, events: Entry[]) {
    if (!element.open && events.some(e => e.seq === selectedSeq)) selectEvent(null);
  }

  return <div className="run-timeline">
    <button onClick={() => { selectEvent(null); setFlat(!flat); }}>{flat ? "Group by step" : "Show all events"}</button>
    {flat ? visible.map(eventRow) : steps.map(step => {
      const last = step.events[step.events.length - 1];
      const unfinished = last === tail && !["node.completed", "node.skipped"].includes(last.kind);
      const interrupted = unfinished && status === "interrupted";
      const problem = interrupted || step.events.some(failed);
      return <details key={step.key} className={problem ? "trace-group trace-problem" : "trace-group"}
        open={Boolean(nodeId) || problem} onToggle={e => {
          if (e.target === e.currentTarget) clearHiddenSelection(e.currentTarget, step.events);
        }}>
        <summary><strong>{step.label}</strong>
          <span className="trace-caption">{interrupted && last.kind !== "run.interrupted" && "Interrupted · "}
            {stepSummary(step, unfinished && status === "running")}</span>
        </summary>
        {step.type === "apiResponse" && last.kind === "node.completed" && <pre>{typeof last.data.output === "string"
          ? last.data.output : JSON.stringify(last.data.output, null, 2)}</pre>}
        <div className="trace-children">{phasesFor(step).map(phase => {
          if (!phase.label) return eventRow(phase.events[0]);
          return <details className="trace-phase" key={phase.events[0].seq} open={phase.events.some(failed)}
            onToggle={e => { if (e.target === e.currentTarget) clearHiddenSelection(e.currentTarget, phase.events); }}>
            <summary>{phase.label}
              {phase.label === "Validate output" && <span className="trace-caption">{validationSummary(phase.events)}</span>}
            </summary>
            <div className="trace-children">{phase.events.map(eventRow)}</div>
          </details>;
        })}</div>
      </details>;
    })}
  </div>;
}
