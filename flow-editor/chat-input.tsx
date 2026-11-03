import { useState } from "react";

export type InputSelection = ({ mode: "message" | "all" } | { mode: "recent"; turns: number }) &
  { source?: "wire" | "chat" };
type Turn = { role: "user" | "assistant"; content: string };

export function SelectionEditor({ selection, change }: {
  selection?: InputSelection | null; change: (next: InputSelection | null) => void;
}) {
  return <>
    <label>Agent input<select value={selection?.mode ?? "wire"} onChange={e => {
      const mode = e.target.value;
      const source = selection?.source ?? "wire";
      change(mode === "recent" ? { mode, turns: 2, source } : mode === "message" || mode === "all" ? { mode, source } : null);
    }}>
      <option value="wire">Whole wire value</option>
      <option value="message">Current message only</option>
      <option value="recent">Current message + recent turns</option>
      <option value="all">Current message + all available turns</option>
    </select></label>
    {selection?.mode === "recent" && <label>Prior turns<select value={selection.turns}
      onChange={e => change({ ...selection, turns: Number(e.target.value) })}>
      {Array.from({length: 12}, (_, n) => <option key={n} value={n+1}>{n+1}</option>)}
    </select></label>}
    {selection && <><label>Chat source<select value={selection.source ?? "wire"}
      onChange={e => change({ ...selection, source: e.target.value === "chat" ? "chat" : "wire" })}>
      <option value="wire">Incoming wire</option><option value="chat">Original chat request</option>
    </select></label>
      <p>{selection.source === "chat"
        ? "Reads the message and history from this run’s original chat. Prompt templates can also read the incoming value as wire."
        : "Reads the message and history from the incoming wire. Choose Original chat request when a router forwards a different object."}</p></>}
  </>;
}

export function useSampleInput(chat: boolean) {
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState<Turn[]>([]);
  function update(index: number, patch: Partial<Turn>) {
    setHistory(current => current.map((turn, i) => i === index ? { ...turn, ...patch } : turn));
  }
  const controls = <>
    <label>{chat ? "Current message" : "Sample input"}<textarea rows={3} maxLength={8000} value={message}
      onChange={e => setMessage(e.target.value)} /></label>
    {chat && <details><summary>Prior turns ({history.length})</summary>
      {history.map((turn, i) => <fieldset key={i}><legend>Turn {i+1}</legend>
        <label>Role<select value={turn.role} onChange={e => update(i, {role: e.target.value as Turn["role"]})}>
          <option value="user">User</option><option value="assistant">Assistant</option>
        </select></label>
        <label>Content<textarea rows={2} maxLength={8000} value={turn.content} onChange={e => update(i, {content: e.target.value})} /></label>
        <button type="button" onClick={() => setHistory(current => current.filter((_, j) => j !== i))}>Remove turn</button>
      </fieldset>)}
      <button type="button" disabled={history.length >= 12} onClick={() => setHistory(current => [...current,
        { role: current.at(-1)?.role === "user" ? "assistant" : "user", content: "" }])}>Add prior turn</button>
    </details>}
  </>;
  return { input: chat ? {message, history} : message, controls };
}
