// Field controls keep their draft values verbatim, including incomplete choice lists.
export function mountOutputEditor(host, initial, onChange) {
  host.innerHTML = `
    <h3>Output</h3>
    <label>Response format<select class="output-format">
      <option value="text">Text</option><option value="object">Structured object</option>
    </select></label>
    <fieldset class="output-object" hidden><legend>Object fields</legend>
      <div class="output-fields"></div><button type="button" class="add-field">Add field</button>
    </fieldset>`;
  const format = host.querySelector("select");
  const object = host.querySelector(".output-object");
  const rows = host.querySelector(".output-fields");
  const add = host.querySelector(".add-field");
  function addField(field = {}) {
    const row = document.createElement("fieldset");
    row.innerHTML = `<legend>Output field</legend>
      <label>Field name<input class="field-name" required pattern="[A-Za-z_][A-Za-z0-9_]{0,63}" maxlength="64"
        title="Use letters, digits or underscores. Start with a letter or underscore."></label>
      <label>Type<select class="field-type"><option value="text">Text</option><option value="choice">Single choice</option></select></label>
      <label class="choice-label">Allowed values (one per line)<textarea rows="3" class="field-choices" required></textarea></label>
      <label>Description<input class="field-description" maxlength="1000"></label>
      <label class="required-field"><input class="field-required" type="checkbox"> Required</label>
      <button type="button">Remove field</button>`;
    const type = row.querySelector("select");
    const choices = row.querySelector("textarea");
    row.querySelector(".field-name").value = field.name ?? "";
    type.value = field.type ?? "text";
    choices.value = (field.choices ?? []).join("\n");
    row.querySelector(".field-description").value = field.description ?? "";
    row.querySelector(".field-required").checked = field.required ?? true;
    function toggleChoices() {
      choices.disabled = row.querySelector(".choice-label").hidden = type.value !== "choice";
    }
    type.addEventListener("change", toggleChoices);
    toggleChoices();
    row.querySelector("button").addEventListener("click", () => {
      row.remove();
      add.disabled = false;
      onChange();
      add.focus();
    });
    rows.append(row);
    add.disabled = rows.children.length >= 64;
    return row;
  }
  function showFormat() {
    object.hidden = object.disabled = format.value !== "object";
    if (!object.hidden && !rows.children.length) addField();
  }
  for (const field of initial?.fields ?? []) addField(field);
  format.value = initial ? "object" : "text";
  showFormat();
  format.addEventListener("change", showFormat);
  add.addEventListener("click", () => {
    addField().querySelector("input").focus();
    onChange();
  });
  return function readSchema(validate = false) {
    if (format.value === "text") return null;
    const names = new Set();
    const fields = Array.from(rows.children, row => {
      const name = row.querySelector(".field-name").value;
      const type = row.querySelector("select").value;
      const choices = type === "choice" ? row.querySelector("textarea").value.split("\n") : [];
      if (validate) {
        if (names.has(name)) throw new Error("Duplicate output field name: " + name);
        names.add(name);
        const values = choices.map(choice => choice.trim());
        if (values.some(value => !value || value.length > 120) || values.length > 64 || new Set(values).size !== values.length) {
          throw new Error(name + ": enter up to 64 distinct, nonempty choices of at most 120 characters each.");
        }
      }
      return { name, type, choices, description: row.querySelector(".field-description").value,
        required: row.querySelector(".field-required").checked };
    });
    if (validate && !fields.length) throw new Error("Add at least one output field.");
    return { fields };
  };
}
