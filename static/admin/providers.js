const { draftSession, linkWithReturn, renderBreadcrumbs, returnDestination, returnWithSelection } =
  await import("/admin/navigation.js?v=" + encodeURIComponent(window.lyraAssetVersion));

const sections = {
  models: ["models", "model"], "model-groups": ["modelGroups", "model group"],
  protocols: ["protocols", "protocol"], providers: ["providers", "provider"],
};

function link(text, href) {
  const anchor = document.createElement("a");
  anchor.textContent = text;
  anchor.href = href;
  return anchor;
}

export async function mountCatalogEditor(page) {
  const host = document.querySelector("#" + page + "-page");
  const [collection, kind] = sections[page];
  const catalog = await window.lyraAdminRequest("/api/admin/catalog");
  const query = new URLSearchParams(location.search);
  const base = "/admin/" + page + "/";
  if (!query.has("new") && !query.has("id")) {
    function destination(param) {
      const url = new URL(location.href);
      url.searchParams.delete("saved");
      for (const [key, value] of Object.entries(param)) url.searchParams.set(key, value);
      return url.pathname + url.search;
    }
    host.append(link("Create " + kind, destination({new: 1})));
    const list = document.createElement("ul");
    list.className = "configuration-list";
    for (const item of catalog[collection]) {
      const row = document.createElement("li");
      row.append(link(item.name, destination({id: item.id})));
      list.append(row);
    }
    host.append(list);
    return;
  }

  const draft = draftSession();
  renderBreadcrumbs();
  const existing = catalog[collection].find(item => item.id === query.get("id"));
  if (query.has("id") && !existing) { host.textContent = "This item is unavailable."; return; }
  const data = { ...existing, ...draft.data };
  const form = document.createElement("form");
  form.className = "configuration-form";
  form.innerHTML = '<h2></h2><label>Name<input name="label" required></label>';
  form.querySelector("h2").textContent = (existing ? "Edit " : "Create ") + kind;
  const name = form.elements.label;
  name.maxLength = page === "models" ? 255 : 120;
  name.value = data.name || "";
  let read = () => ({});
  function select(label, entries, value) {
    const wrapper = document.createElement("label");
    wrapper.textContent = label;
    const input = document.createElement("select");
    input.required = true;
    input.add(new Option("Choose…", ""));
    entries.forEach(item => input.add(new Option(item.name, item.id)));
    input.value = value || "";
    wrapper.append(input);
    form.append(wrapper);
    return input;
  }
  function reference(label, path, options, value) {
    const picker = select(label, options, value);
    const create = link("Create " + label.toLowerCase(), linkWithReturn("/admin/" + path + "/?new=1"));
    const edit = link("Edit selected " + label.toLowerCase(), "#");
    const refresh = () => {
      edit.hidden = !picker.value;
      edit.href = linkWithReturn("/admin/" + path + "/?id=" + encodeURIComponent(picker.value));
    };
    picker.addEventListener("change", refresh);
    refresh();
    form.append(create, " · ", edit);
    return picker;
  }
  if (page === "models" && query.has("addToGroup")) {
    const group = catalog.modelGroups.find(g => g.id === query.get("addToGroup"));
    if (!group) { host.textContent = "The selected model group is unavailable."; return; }
    const note = document.createElement("p");
    note.textContent = "Saving also adds this model to " + group.name + ".";
    form.append(note);
    read = () => ({ addToGroup: group.id, groupRevision: data.groupRevision ?? group.revision });
  }
  if (page === "model-groups") {
    const fields = document.createElement("fieldset");
    fields.innerHTML = "<legend>Models in this group</legend>";
    for (const model of catalog.models) {
      const row = document.createElement("div");
      const label = document.createElement("label");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.value = model.id;
      check.checked = data.modelIds?.includes(model.id) ?? false;
      label.append(check, " ", model.name);
      row.append(label, link("Edit " + model.name, linkWithReturn("/admin/models/?id=" + model.id)));
      fields.append(row);
    }
    form.append(fields, link("Create model", linkWithReturn("/admin/models/?new=1")));
    read = () => ({ modelIds: Array.from(fields.querySelectorAll(":checked"), input => input.value) });
  }
  if (page === "protocols") {
    const adapter = select("Client API adapter", catalog.adapters, data.adapter);
    read = () => ({ adapter: adapter.value });
  }
  if (page === "providers") {
    const uriLabel = document.createElement("label");
    uriLabel.textContent = "API base URI";
    const uri = document.createElement("input");
    uri.type = "url";
    uri.maxLength = 2048;
    uri.required = true;
    uri.value = data.uri || "";
    uriLabel.append(uri);
    form.append(uriLabel);
    const protocol = reference("Protocol", "protocols", catalog.protocols, data.protocolId);
    const group = reference("Model group", "model-groups", catalog.modelGroups, data.groupId);
    const hint = document.createElement("p");
    function showHint() {
      const adapter = catalog.protocols.find(p => p.id === protocol.value)?.adapter;
      const example = { openai: "https://api.openai.com/v1", anthropic: "https://api.anthropic.com/v1",
        ollama: "http://localhost:11434" }[adapter];
      hint.textContent = example ? "Example API base: " + example : "Choose the protocol spoken by this endpoint.";
    }
    protocol.addEventListener("change", showHint);
    showHint();
    form.append(hint);
    read = () => ({ uri: uri.value, groupId: group.value, protocolId: protocol.value });
  }
  const save = document.createElement("button");
  save.textContent = "Save " + kind;
  const status = document.createElement("p");
  status.setAttribute("role", "status");
  form.append(save, " ", link("Cancel", returnDestination(base)), status);
  const payload = () => ({ name: name.value, revision: data.revision, ...read() });
  const persist = () => draft.save(payload());
  form.addEventListener("input", persist);
  form.addEventListener("change", persist);
  persist();
  form.addEventListener("submit", async event => {
    event.preventDefault();
    save.disabled = true;
    try {
      const saved = await window.lyraAdminRequest("/api/admin/" + page + (existing ? "/" + existing.id : ""), {
        method: existing ? "PUT" : "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload()),
      });
      draft.clear();
      if (!returnWithSelection(saved, kind)) location.replace(base + "?saved=1");
    } catch (error) { status.textContent = error.message; save.disabled = false; }
  });
  host.append(form);
}
