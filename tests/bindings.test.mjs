import assert from "node:assert/strict";
import test from "node:test";
import { compileTemplate, renderTemplate } from "../flow-editor/bindings.ts";

const ports = [{ id: "stable-a", name: "classification" }, { id: "stable-b", name: "request" }];
const empty = { base: {}, mappings: [] };

test("inline names compile to IDs and renaming changes only the projection", () => {
  const template = { Category: "{{ classification.category }}", Text: "{{ request }}", step: 1 };
  const definition = compileTemplate(JSON.stringify(template), empty, ports);
  assert.deepEqual(definition.base, { Category: null, Text: null, step: 1 });
  assert.deepEqual(definition.mappings, [
    { edgeId: "stable-a", source: ".value/category", target: "/Category" },
    { edgeId: "stable-b", source: ".value", target: "/Text" },
  ]);
  const renamed = [{ id: "stable-b", name: "request" }, { id: "stable-a", name: "result" }];
  const shown = renderTemplate(definition, renamed);
  assert.equal(JSON.parse(shown).Category, "{{ result.category }}");
  assert.deepEqual(compileTemplate(shown, definition, renamed), definition);
});

test("legacy conversions, escaped keys, literal expressions and array references round-trip", () => {
  const definition = {
    base: { message: "", values: [0], literal: "{{ request }}" },
    mappings: [
      { edgeId: "stable-b", source: ".text", target: "/message" },
      { edgeId: "stable-a", source: ".json/a~1b/0/~0", target: "/values/0" },
    ],
  };
  const template = renderTemplate(definition, ports);
  assert.equal(JSON.parse(template).values[0], '{{ json(classification)["a/b"][0]["~"] }}');
  assert.deepEqual(compileTemplate(template, definition, ports), definition);
});

test("dangling ID bindings survive unrelated edits and never attach to a reused name", () => {
  const definition = { base: { x: null }, mappings: [{ edgeId: "gone", source: ".value", target: "/x" }] };
  const template = JSON.parse(renderTemplate(definition, ports));
  template.status = "edited";
  assert.deepEqual(compileTemplate(JSON.stringify(template), definition, ports).mappings, definition.mappings);
  template.x = "{{ request }}";
  assert.deepEqual(compileTemplate(JSON.stringify(template), definition, ports).mappings,
    [{ edgeId: "stable-b", source: ".value", target: "/x" }]);
  for (const value of ["{{ missing }}", "{{ request.toUpperCase() }}", "{{ request + 1 }}"]) {
    assert.throws(() => compileTemplate(JSON.stringify({ x: value }), empty, ports));
  }
});
