export type PromptReference = { name: string; version: number };
export type AgentDefinition = {
  id: string;
  name: string;
  instructions: PromptReference;
  prompt: PromptReference | null;
  revision: number;
};
export type LibraryPrompt = PromptReference & { text: string };
export type Catalog = { agents: AgentDefinition[]; prompts: LibraryPrompt[] };

declare global {
  interface Window {
    lyraAdminRequest: <T = Catalog>(path: string, options?: RequestInit) => Promise<T>;
  }
}

export function loadCatalog() {
  return window.lyraAdminRequest("/api/admin/catalog");
}
