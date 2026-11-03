// Readers return draft values as typed. Validation runs on save.
function fieldList(host, initial, onChange) {
  host.innerHTML = '<div class="output-fields"></div><button type="button">Add field</button>';
  const rows = host.firstElementChild;
  const add = host.lastElementChild;
  const readers = new Map();
  function append(field = {}) {
    const row = document.createElement("fieldset");
    row.innerHTML = `<legend>Object field</legend>
      <label>Field name<input required pattern="[A-Za-z_][A-Za-z0-9_]{0,63}" maxlength="64"
        title="Use letters, digits or underscores. Start with a letter or underscore."></label>
      <label class="required-field"><input type="checkbox"> Required</label>
      <div class="value-definition"></div><button type="button">Remove field</button>`;
    const [name, required] = row.querySelectorAll("input");
    name.value = field.name ?? "";
    required.checked = field.required ?? true;
    const read = valueEditor(row.querySelector(".value-definition"), field, onChange);
    readers.set(row, validate => ({ name: name.value, required: required.checked, ...read(validate) }));
    row.lastElementChild.onclick = () => {
      readers.delete(row);
      row.remove();
      add.disabled = false;
      onChange();
      add.focus();
    };
    rows.append(row);
    add.disabled = readers.size >= 64;
    return name;
  }
  for (const field of initial) append(field);
  add.onclick = () => { append().focus(); onChange(); };
  return validate => {
    const fields = Array.from(readers.values(), read => read(validate));
    if (validate && !fields.length) throw new Error("Add at least one field to each object.");
    if (validate && new Set(fields.map(f => f.name)).size !== fields.length)
      throw new Error("Field names within each object must be unique.");
    return fields;
  };
}

function valueEditor(host, value, onChange) {
  host.innerHTML = `<label>Type<select><option value="text">Text</option><option value="choice">Single choice</option>
      <option value="object">Object</option><option value="array">Array</option></select></label>
    <label>Description<input maxlength="1000"></label>
    <label class="choice-label">Allowed values (one per line)<textarea rows="3" required></textarea></label>
    <fieldset class="nested-object"><legend>Object fields</legend><div></div></fieldset>
    <fieldset class="array-item"><legend>Array item</legend><div></div></fieldset>`;
  const type = host.querySelector("select");
  const description = host.querySelector("input");
  const choices = host.querySelector("textarea");
  const object = host.querySelector(".nested-object");
  const array = host.querySelector(".array-item");
  let readFields, readItems;
  type.value = value.type ?? "text";
  description.value = value.description ?? "";
  choices.value = (value.choices ?? []).join("\n");
  function showType() {
    choices.disabled = choices.parentElement.hidden = type.value !== "choice";
    object.hidden = object.disabled = type.value !== "object";
    array.hidden = array.disabled = type.value !== "array";
    if (!object.hidden) readFields ??= fieldList(object.lastElementChild, value.fields ?? [{}], onChange);
    if (!array.hidden) readItems ??= valueEditor(array.lastElementChild, value.items ?? {}, onChange);
  }
  showType();
  type.addEventListener("input", showType);
  type.addEventListener("change", showType);
  return validate => {
    const result = { type: type.value, description: description.value };
    if (type.value === "choice") {
      result.choices = choices.value.split("\n");
      const values = result.choices.map(choice => choice.trim());
      if (validate && (values.some(v => !v || v.length > 120) || values.length > 64 || new Set(values).size !== values.length))
        throw new Error("Enter up to 64 distinct, nonempty choices of at most 120 characters each.");
    }
    if (type.value === "object") result.fields = readFields(validate);
    if (type.value === "array") result.items = readItems(validate);
    return result;
  };
}

export function mountOutputEditor(host, initial, onChange) {
  host.innerHTML = `<h3>Output</h3>
    <label>Response format<select><option value="text">Text</option><option value="object">Structured object</option></select></label>
    <fieldset><legend>Object fields</legend><div></div></fieldset>`;
  const format = host.querySelector("select");
  const object = host.querySelector("fieldset");
  const readFields = fieldList(object.lastElementChild, initial?.fields ?? [{}], onChange);
  format.value = initial ? "object" : "text";
  function showFormat() { object.hidden = object.disabled = format.value !== "object"; }
  showFormat();
  format.addEventListener("change", showFormat);
  return (validate = false) => format.value === "object" ? { fields: readFields(validate) } : null;
}
