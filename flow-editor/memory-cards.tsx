import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { AgentDefinition } from "./catalog";
import type { Recall, Remember } from "./graph";

export function MemoriesPort() {
  return <Handle type="target" id="memories" position={Position.Top} title="Recalled memories" className="flow-port memory-port" />;
}

export function RecallCard({data}: NodeProps<Recall>) {
  return <div className="item-card memory-card">
    <Handle type="target" position={Position.Left} title="Search query" className="flow-port" />
    <span className="item-kind">Recall · user memories</span><strong>{data.label}</strong>
    <Handle type="source" position={Position.Right} title="Recalled memories" className="flow-port memory-port" />
  </div>;
}

export function RememberCard({data}: NodeProps<Remember>) {
  return <div className="item-card memory-card">
    <Handle type="target" position={Position.Left} title="Evidence" className="flow-port" />
    <span className="item-kind">Remember · user memories</span><strong>{data.label}</strong>
    <small>{data.rememberWhen === "onEvidence" ? "When evidence arrives" : "After API Response"}</small>
  </div>;
}

export function RememberProperties({data, agents, change}: {data: Remember["data"]; agents: AgentDefinition[];
  change: (patch: Partial<Remember["data"]>) => void}) {
  return <>
    <label>Evidence to inspect<select value={data.memoryEvidence ?? "value"}
      onChange={e => change({memoryEvidence: e.target.value as Remember["data"]["memoryEvidence"]})}>
      <option value="value">Connected value only</option><option value="exchange">Completed chat exchange</option>
    </select></label>
    {data.memoryEvidence === "exchange" && <p>Connect API Response or a generating State that includes the completed turn.</p>}
    <label>Run extraction<select value={data.rememberWhen ?? "afterResponse"}
      onChange={e => change({rememberWhen: e.target.value as Remember["data"]["rememberWhen"]})}>
      <option value="afterResponse">After API Response</option><option value="onEvidence">When evidence arrives</option>
    </select></label>
    <label>Extraction model<select value={data.extractorAgentId ?? ""} onChange={e => change({extractorAgentId: e.target.value || null})}>
      <option value="">Default chat model</option>
      {agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name} · model</option>)}
    </select></label>
    <p>Facts need a quote from the user. The turn must succeed before selected memories are saved.</p>
  </>;
}
