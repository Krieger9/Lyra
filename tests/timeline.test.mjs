import assert from "node:assert/strict";
import test from "node:test";
import { failed, groupTimeline, isSetup, phasesFor, stepSummary, validationSummary } from "../flow-editor/timeline.ts";

const entry = (seq, node, kind, data = {}) => ({ seq, node, kind, data, elapsed_ms: seq, at: "" });
const node = (id, type, label) => ({ id, type, data: { label } });

test("grouped history keeps every event and separates node work, reflection and final commits", () => {
  const events = [
    entry(1, null, "run.started"), entry(2, null, "plan.ready"),
    entry(3, "input", "memory.recalled", { count: 4 }), entry(4, "input", "node.completed"),
    entry(5, "lyra", "provider.request", { body: { model: "local-model" } }),
    entry(6, "lyra", "identity.tool"), entry(7, "lyra", "node.completed", { output: "Hello", duration_ms: 8 }),
    entry(8, null, "provider.request"), entry(9, null, "provider.response"), entry(10, null, "identity.tool"),
    entry(11, null, "provider.request"), entry(12, null, "identity.reflected", { changes: 2 }),
    entry(13, null, "memory.committed", { count: 1 }), entry(14, null, "identity.committed", { changes: 2 }),
    entry(15, null, "run.completed"),
  ];
  const steps = groupTimeline(events, [node("input", "apiInput", "API Input"), node("lyra", "agent", "Saved name")]);
  assert.deepEqual(steps.map(step => step.label), ["API Input", "Saved name", "Identity reflection", "Finish run"]);
  assert.deepEqual([...events.filter(isSetup), ...steps.flatMap(step => phasesFor(step).flatMap(p => p.events))], events);
  assert.match(stepSummary(steps[0], false), /Recalled 4/);
  assert.equal(stepSummary(steps[1], false), "local-model · 8 ms · Hello");
  assert.equal(phasesFor(steps[2]).filter(p => p.label === "Model call").length, 2);
  assert.match(stepSummary(steps[3], false), /Committed 1 memory selection · Committed 2 identity changes/);
});

test("a growing State step groups extraction with its own call and keeps gate failures visible", () => {
  const state = node("state", "state", "Save Memories");
  const events = ["template.rendered", "schema.inserted", "model.request", "provider.response", "model.response",
    "agent.trace", "gate.evaluated", "gate.evaluated", "model.request", "model.response", "memory.extracted",
    "mapping.applied", "state.applied", "node.completed"].map((kind, i) => entry(i + 1, state.id, kind));
  events[6].data = { passed: true };
  events[7].data = { passed: false, field: "memory", operation: "required" };
  const partial = groupTimeline(events.slice(0, 3), [state])[0];
  assert.equal(stepSummary(partial, true), "Running · Send rendered prompt");
  const phases = phasesFor(groupTimeline(events, [state])[0]);
  assert.deepEqual(phases.map(p => p.label), ["Prepare prompt", "Model call", "Validate output", "Extract memories", "Update state", ""]);
  assert.equal(validationSummary(phases[2].events), "1 of 2 checks passed · Fail · memory · required");
  assert.deepEqual(phases[3].events.map(e => e.seq), [9, 10, 11]);
  assert.deepEqual(phases.flatMap(p => p.events), events);
});

test("unknown events, skipped paths and node-attributed commits survive failed older runs", () => {
  const events = [entry(1, "branch", "node.skipped"), entry(2, "missing", "custom.event"),
    entry(3, "missing", "memory.committed", { count: 0 }), entry(4, "missing", "node.failed", {error: "Bad input"}),
    entry(5, null, "run.failed", {error: "Bad input"})];
  const [skipped, missing, finish] = groupTimeline(events, []);
  assert.match(stepSummary(skipped, false), /Skipped/);
  assert.equal(missing.label, "missing");
  assert.deepEqual(finish.events.map(e => e.seq), [3, 5]);
  assert.ok(finish.events.some(failed));
  assert.equal(stepSummary(missing, false), "Failed · Bad input");
});
