import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { Personality } from "./graph";

type Profile = {id: string; name: string; revision: number};
const Profiles = createContext<{ profiles: Profile[]; error: string; refresh: () => void }>({
  profiles: [], error: "", refresh: () => {},
});

export function PersonalityProvider({children}: {children: ReactNode}) {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let live = true;
    window.lyraAdminRequest<Profile[]>("/api/admin/personalities")
      .then(result => { if (live) { setProfiles(result); setError(""); } })
      .catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [refresh]);
  return <Profiles.Provider value={{ profiles, error, refresh: () => setRefresh(r => r + 1) }}>
    {children}
  </Profiles.Provider>;
}

export function PersonalityCard({data}: NodeProps<Personality>) {
  const {profiles} = useContext(Profiles);
  const profile = profiles.find(p => p.id === data.personalityId);
  return <div className="item-card personality-card">
    <span className="item-kind">{data.label || "Personality"}</span>
    <strong>{profile?.name ?? (data.personalityId ? "Selected personality" : "None selected")}</strong>
    <Handle type="source" position={Position.Right} title="Personality output" className="flow-port personality-port" />
  </div>;
}

export function PersonalityConnection({cards, value, change}: {cards: Personality[]; value: string; change: (id: string) => void}) {
  const {profiles} = useContext(Profiles);
  return <label>Personality connection<select value={value} onChange={e => change(e.target.value)}>
    <option value="">None</option>
    {cards.map(card => <option key={card.id} value={card.id}>{card.data.label} · {
      profiles.find(p => p.id === card.data.personalityId)?.name ?? (card.data.personalityId ? "Selected profile" : "No profile")
    }</option>)}
  </select></label>;
}

export function PersonalityPicker({value, references, change}: {value?: string | null; references: number; change: (id: string | null) => void}) {
  const {profiles, error, refresh} = useContext(Profiles);
  return <>
    <h3>Personality</h3>
    <label>Load personality<select value={value ?? ""} onChange={e => change(e.target.value || null)}>
      <option value="">None — agent instructions only</option>
      {value && !profiles.some(p => p.id === value) && <option value={value}>Selected personality (unavailable)</option>}
      {profiles.map(p => <option value={p.id} key={p.id}>{p.name} · revision {p.revision}</option>)}
    </select></label>
    {value && references > 1 && <p>{references} cards in this flow reference this profile.</p>}
    <a href={value ? `/admin/personalities/?id=${encodeURIComponent(value)}` : "/admin/personalities/?new=1"}
      target="_blank" rel="noopener">{value ? "Edit personality" : "Create personality"}</a>
    <button type="button" onClick={refresh}>Refresh personalities</button>
    {error && <p role="alert">{error}</p>}
    <p>Connect this card to an agent’s Personality port to supply its voice, goals and memories.
      Cards that select the same profile share its identity. Reactivate chat after editing the flow or profile.</p>
  </>;
}
