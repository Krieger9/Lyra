type JsonObject = Record<string, unknown>;
export type Rule = { edgeId: string; source: string; target: string };
export type ObjectDefinition = { base: JsonObject; mappings: Rule[] };
export type WireReference = { id: string; name: string };

const identifier = /^[A-Za-z_][A-Za-z0-9_]*/;
const escapeKey = (key: string) => key.replaceAll("~", "~0").replaceAll("/", "~1");
const unescapeKey = (key: string) => key.replaceAll("~1", "/").replaceAll("~0", "~");
const literalBinding = /^\\*\{\{[\s\S]*\}\}$/;

function children(value: unknown, path: string, visit: (value: unknown, path: string) => unknown): unknown {
  if (Array.isArray(value)) return value.map((child, i) => visit(child, path + "/" + i));
  if (value && typeof value === "object") return Object.fromEntries(
    Object.entries(value).map(([key, child]) => [key, visit(child, path + "/" + escapeKey(key))]));
  return value;
}

function expression(rule: Rule, wires: WireReference[]) {
  const wire = wires.find(item => item.id === rule.edgeId);
  if (!wire) return null;
  if (rule.source === ".text") return "text(" + wire.name + ")";
  const match = /^(\.value|\.json)(\/[\s\S]*)?$/.exec(rule.source);
  if (!match || /~(?![01])/.test(rule.source)) return null;
  let result = match[1] === ".json" ? "json(" + wire.name + ")" : wire.name;
  for (const part of (match[2] ?? "").split("/").slice(1)) {
    const key = unescapeKey(part);
    if (identifier.exec(key)?.[0] === key) result += "." + key;
    else if (/^(0|[1-9]\d*)$/.test(key)) result += "[" + key + "]";
    else result += "[" + JSON.stringify(key) + "]";
  }
  return result;
}

// Project canonical ID-based rules into the object editor. Keep incomplete rows
// outside the projection so editing a literal cannot silently discard a broken wire.
function project(definition: ObjectDefinition, wires: WireReference[]) {
  const shown = new Set<Rule>();
  function visit(value: unknown, path: string): unknown {
    const rule = definition.mappings.find(item => item.target === path && path);
    const binding = rule && expression(rule, wires);
    if (rule && binding) {
      shown.add(rule);
      return "{{ " + binding + " }}";
    }
    if (typeof value === "string" && literalBinding.test(value)) return "\\" + value;
    return children(value, path, visit);
  }
  return { object: visit(definition.base, ""), shown };
}

export function renderTemplate(definition: ObjectDefinition, wires: WireReference[]) {
  return JSON.stringify(project(definition, wires).object, null, 2);
}

function resolve(body: string, wires: WireReference[], target: string): Rule {
  const conversion = /^(text|json)\(([A-Za-z_]\w*)\)/.exec(body);
  const alias = conversion?.[2] ?? identifier.exec(body)?.[0];
  const wire = wires.find(item => item.name === alias);
  if (!wire) throw new Error(target + ": unknown input " + JSON.stringify(alias ?? body) + ". Check the incoming wire names.");
  let tail = body.slice((conversion?.[0] ?? alias!).length);
  let source = conversion ? "." + conversion[1] : ".value";
  if (source === ".text" && tail) throw new Error(target + ": text() returns a whole string.");
  while (tail) {
    const step = /^(?:\.([A-Za-z_]\w*)|\[(0|[1-9]\d*)\]|\[("(?:[^"\\]|\\.)*")\])/.exec(tail);
    if (!step) throw new Error(target + ": use .field, [0] or [\"field name\"] to read a value.");
    source += "/" + escapeKey(step[1] ?? step[2] ?? JSON.parse(step[3]));
    tail = tail.slice(step[0].length);
  }
  return { edgeId: wire.id, source, target };
}

export function compileTemplate(text: string, previous: ObjectDefinition, wires: WireReference[]): ObjectDefinition {
  const object = JSON.parse(text);
  if (!object || Array.isArray(object) || typeof object !== "object") throw new Error("Use an object as the template.");
  const { shown } = project(previous, wires);
  const unresolved = previous.mappings.filter(rule => !shown.has(rule));
  const inline: Rule[] = [];
  function visit(value: unknown, path: string): unknown {
    if (typeof value === "string" && literalBinding.test(value)) {
      if (value.startsWith("\\")) return value.slice(1);
      inline.push(resolve(value.slice(2, -2).trim(), wires, path));
      // A new inline binding gets a null slot. Intermediate keystrokes must
      // never become its literal fallback when the mapping is removed.
      if (!previous.mappings.some(rule => rule.target === path)) return null;
      let original: unknown = previous.base;
      for (const part of path.slice(1).split("/")) {
        const key = unescapeKey(part);
        if (!original || typeof original !== "object" || !Object.hasOwn(original, key)) return null;
        original = (original as JsonObject)[key];
      }
      return original;
    }
    return children(value, path, visit);
  }
  const base = visit(object, "") as JsonObject;
  const replaced = new Set(inline.map(rule => rule.target));
  const mappings = [...unresolved.filter(rule => !replaced.has(rule.target)), ...inline];
  if (mappings.length > 64) throw new Error("An object supports up to 64 field mappings.");
  return { base, mappings };
}
