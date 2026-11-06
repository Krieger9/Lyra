export const ALL_PROMPTS = "all";
export const UNFILED_PROMPTS = "unfiled";

export function effectivePromptSet(agentSet, flowSet) {
  return agentSet || flowSet || ALL_PROMPTS;
}

export function normalizeSet(value, sets) {
  return value === UNFILED_PROMPTS || sets.some(set => set.id === value) ? value : ALL_PROMPTS;
}

export function filterPrompts(prompts, sets, selected) {
  if (selected === ALL_PROMPTS) return prompts;
  const names = new Set(sets.filter(set => selected === UNFILED_PROMPTS || set.id === selected)
    .flatMap(set => set.prompts));
  return prompts.filter(prompt => selected === UNFILED_PROMPTS ? !names.has(prompt.name) : names.has(prompt.name));
}

export function promptChoices(prompts, sets, selected, retained) {
  const visible = new Set(filterPrompts(prompts, sets, selected));
  return prompts.filter(prompt => visible.has(prompt) || JSON.stringify([prompt.name, prompt.version]) === retained);
}
