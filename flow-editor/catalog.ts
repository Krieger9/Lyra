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
    lyraAdminRequest: (path: string) => Promise<Catalog>;
  }
}

export function loadCatalog() {
  return window.lyraAdminRequest("/api/admin/catalog");
}
