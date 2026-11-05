import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { NodeProps } from "@xyflow/react";
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
    <span className="item-kind">Flow personality</span>
    <strong>{profile?.name ?? (data.personalityId ? "Selected personality" : "None selected")}</strong>
  </div>;
}

export function PersonalityPicker({value, change}: {value?: string | null; change: (id: string | null) => void}) {
  const {profiles, error, refresh} = useContext(Profiles);
  return <>
    <h3>Personality</h3>
    <label>Load personality<select value={value ?? ""} onChange={e => change(e.target.value || null)}>
      <option value="">None — agent instructions only</option>
      {value && !profiles.some(p => p.id === value) && <option value={value}>Selected personality (unavailable)</option>}
      {profiles.map(p => <option value={p.id} key={p.id}>{p.name} · revision {p.revision}</option>)}
    </select></label>
    <a href={value ? `/admin/personalities/?id=${encodeURIComponent(value)}` : "/admin/personalities/?new=1"}
      target="_blank" rel="noopener">{value ? "Edit personality" : "Create personality"}</a>
    <button type="button" onClick={refresh}>Refresh personalities</button>
    {error && <p role="alert">{error}</p>}
    <p>Adds the personality’s prompt, goals and all assigned memories to participating agents.
      Save and activate the flow to apply your selection. Reactivate after personality edits.
      State updaters keep their own instructions.</p>
  </>;
}
