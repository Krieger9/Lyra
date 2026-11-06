export type PromptReference = { name: string; version: number };
export type ValueDefinition = { description: string; choices: string[] } & (
  { type: "text" | "choice" } | { type: "object"; fields: OutputField[] } | { type: "array"; items: ValueDefinition }
);
export type OutputField = ValueDefinition & { name: string; required: boolean };
export function sampleObject(fields: OutputField[]): Record<string, unknown> {
  return Object.fromEntries(fields.map(field => [field.name, sampleValue(field)]));
}
function sampleValue(definition: ValueDefinition): unknown {
  if (definition.type === "object") return sampleObject(definition.fields);
  if (definition.type === "array") return [sampleValue(definition.items)];
  return definition.choices?.[0] ?? "";
}
export type AgentDefinition = {
  id: string;
  name: string;
  instructions: PromptReference;
  prompt: PromptReference | null;
  outputSchema: { fields: OutputField[] } | null;
  revision: number;
  toolSets?: string[];
  promptSetId?: string | null;
  modelSelection?: { providerId: string; model: string } | null;
};
export type LibraryPrompt = PromptReference & { text: string };
export type Catalog = { agents: AgentDefinition[]; prompts: LibraryPrompt[]; providers: { id: string; name: string }[];
  promptSets?: {id: string; name: string; prompts: string[]}[] };

declare global {
  interface Window {
    lyraAdminRequest: <T = Catalog>(path: string, options?: RequestInit) => Promise<T>;
  }
}

export function loadCatalog() {
  return window.lyraAdminRequest("/api/admin/catalog");
}
