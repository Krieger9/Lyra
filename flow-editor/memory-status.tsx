import { useEffect, useState } from "react";

export function MemoryStatus() {
  const [status, setStatus] = useState<{enabled: boolean; embeddingConfigured: boolean}>();
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    window.lyraAdminRequest<{enabled: boolean; embeddingConfigured: boolean}>("/api/admin/memory-status")
      .then(value => { if (live) setStatus(value); })
      .catch(() => { if (live) setError("Memory status unavailable. Refresh after restarting Lyra."); });
    return () => { live = false; };
  }, []);
  const label = !status ? error || "Checking memory service…"
    : !status.enabled ? "Disabled on this server"
    : !status.embeddingConfigured ? "OpenAI embedding key required" : "Enabled for chat";
  return <details className="state-object" open>
    <summary><strong>Long-term memory</strong> · {label}</summary>
    <p>Add Recall to search this user’s memories. Connect its output to each agent’s Memories port.
      Add Remember to choose evidence and when to extract facts for future chats.</p>
    <p>Recall searches this user's saved memories across chats. Run history shows what was recalled, selected and committed.
      The flow debugger uses temporary conversation state. Test memory across conversations in Lyra chat.</p>
    {status && !status.enabled && <p>Set LYRA_MEMORY_ENABLED=true in the server environment and restart Lyra to enable memory.</p>}
  </details>;
}
