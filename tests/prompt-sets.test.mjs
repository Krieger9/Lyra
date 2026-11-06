import assert from "node:assert/strict";
import test from "node:test";
import { effectivePromptSet, filterPrompts, normalizeSet, promptChoices } from "../static/admin/prompt-sets.js";

const prompts = [
  {name: "shared", version: 1}, {name: "shared", version: 2},
  {name: "old-flow", version: 1}, {name: "new-flow", version: 1},
  {name: "unfiled", version: 1},
];
const sets = [
  {id: "old", prompts: ["shared", "old-flow"]},
  {id: "new", prompts: ["shared", "new-flow"]},
  {id: "empty", prompts: []},
];

test("sets filter every version, allow shared prompts and start empty", () => {
  assert.deepEqual(filterPrompts(prompts, sets, "all"), prompts);
  assert.deepEqual(filterPrompts(prompts, sets, "new"), [prompts[0], prompts[1], prompts[3]]);
  assert.deepEqual(filterPrompts(prompts, sets, "empty"), []);
  assert.deepEqual(filterPrompts(prompts, sets, "unfiled"), [prompts[4]]);
});

test("switching sets retains the exact selected agent version", () => {
  assert.deepEqual(promptChoices(prompts, sets, "empty", '["shared",1]'), [prompts[0]]);
  assert.deepEqual(promptChoices(prompts, sets, "new", '["old-flow",1]'), prompts.slice(0, 4));
  assert.deepEqual(promptChoices(prompts, sets, "empty", "raw"), []);
});

test("invalid saved filters recover to All prompts", () => {
  assert.equal(normalizeSet("missing", sets), "all");
  assert.equal(normalizeSet(null, sets), "all");
  assert.equal(normalizeSet("new", sets), "new");
  assert.equal(normalizeSet("unfiled", sets), "unfiled");
});

test("agent overrides take precedence and inheritance follows the current flow", () => {
  assert.equal(effectivePromptSet(null, "old"), "old");
  assert.equal(effectivePromptSet(null, "new"), "new");
  assert.equal(effectivePromptSet("old", "new"), "old");
  assert.equal(effectivePromptSet("old", null), "old");
  assert.equal(effectivePromptSet(null, null), "all");
  assert.deepEqual(promptChoices(prompts, sets, effectivePromptSet(null, "new"), "raw"),
    [prompts[0], prompts[1], prompts[3]]);
});
