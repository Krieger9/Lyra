const { draftSession, linkWithReturn, renderBreadcrumbs, returnDestination, returnWithSelection } =
  await import("/admin/navigation.js?v=" + encodeURIComponent(window.lyraAssetVersion));

const request = (...args) => window.lyraAdminRequest(...args);
const RAW_INPUT = "raw";
const referenceKey = prompt => prompt ? JSON.stringify([prompt.name, prompt.version]) : RAW_INPUT;
const promptLabel = prompt => prompt.name + " · v" + prompt.version;

function option(value, label) {
  const element = document.createElement("option");
  element.value = value;
  element.textContent = label;
  return element;
}

export async function mountConfiguration(page) {
  const host = document.getElementById(page + "-page");
  host.innerHTML = `
    <div class="configuration-heading"><h2></h2><a class="create-link"></a></div>
    <ul class="configuration-list"></ul>
    <p class="configuration-empty" hidden></p>
    <section id="designer-chat" hidden aria-label="System Flow Designer chat"></section>
    <form class="configuration-form" hidden>
      <h3></h3>
      <label for="definition-name">Name</label>
      <input id="definition-name" required autocomplete="off">
      <div class="agent-fields" hidden>
        <label for="agent-provider">Provider</label>
        <select id="agent-provider"></select>
        <label for="agent-model">Model</label>
        <select id="agent-model"></select>
        <div id="agent-model-links"></div>
        <label for="instructions">System instructions</label>
        <select id="instructions" required></select>
        <a id="create-instructions" href="/admin/prompts/?new=1">Create instructions in PromptStudio</a>
        <a id="edit-instructions">Edit selected instructions</a>
        <pre id="instructions-preview"></pre>
        <label for="prompt">Prompt</label>
        <select id="prompt" required></select>
        <p>Use raw user input to pass incoming flow text directly, or select a template to format it.</p>
        <a id="create-prompt" href="/admin/prompts/?new=1">Create a prompt in PromptStudio</a>
        <a id="edit-prompt">Edit selected prompt</a>
        <pre id="prompt-preview"></pre>
        <button id="refresh-library" type="button">Refresh prompt library</button>
        <section id="agent-output"></section>
      </div>
      <div class="prompt-fields" hidden>
        <label for="prompt-text">Template</label>
        <textarea id="prompt-text" rows="14" maxlength="100000"></textarea>
        <p>Saving an existing prompt creates a new version.</p>
      </div>
      <div class="configuration-actions"><button type="submit">Save</button><a class="cancel-link">Back to list</a></div>
    </form>
    <p class="configuration-status" role="status"></p>`;
  const agentsPage = page === "agents";
  const title = agentsPage ? "Agents" : "PromptStudio";
  const base = "/admin/" + page + "/";
  const query = new URLSearchParams(location.search);
  function editorUrl(parameters) {
    const target = new URLSearchParams(parameters);
    for (const key of ["returnTo", "field", "node"]) {
      if (query.has(key)) target.set(key, query.get(key));
    }
    return base + "?" + target;
  }
  const isEditor = query.has("new") || query.has("id") || query.has("name");
  const draft = isEditor ? draftSession() : null;
  renderBreadcrumbs();
  const form = host.querySelector("form");
  const status = host.querySelector(".configuration-status");
  const name = host.querySelector("#definition-name");
  const instructions = host.querySelector("#instructions");
  const provider = host.querySelector("#agent-provider");
  const model = host.querySelector("#agent-model");
  const prompt = host.querySelector("#prompt");
  const text = host.querySelector("#prompt-text");
  const save = form.querySelector('[type="submit"]');
  let catalog = await request("/api/admin/catalog");
  let editing = null;
  host.querySelector("h2").textContent = title;
  const create = host.querySelector(".create-link");
  create.href = editorUrl({ new: "1" });
  create.textContent = agentsPage ? "+ Create agent" : "+ Create prompt";
  const cancel = host.querySelector(".cancel-link");
  cancel.href = returnDestination(base);
  if (query.has("returnTo")) cancel.textContent = "Back to previous page";
  name.maxLength = agentsPage ? 120 : 255;

  const entries = agentsPage ? catalog.agents : [...new Map(catalog.prompts.map(entry => [entry.name, entry])).values()];
  const list = host.querySelector(".configuration-list");
  for (const entry of entries) {
    const row = document.createElement("li");
    const link = document.createElement("a");
    link.href = editorUrl(agentsPage ? { id: entry.id } : { name: entry.name });
    link.textContent = agentsPage ? entry.name : promptLabel(entry);
    row.append(link);
    if (entry.systemAgent === "flow-designer") {
      const chat = document.createElement("a");
      chat.href = editorUrl({ id: entry.id, chat: "1" });
      chat.textContent = "Open chat";
      row.append(" · ", chat);
    }
    list.append(row);
  }
  list.hidden = isEditor;
  create.hidden = isEditor;
  if (query.has("saved")) status.textContent = "Saved.";
  if (!isEditor && !entries.length) {
    const empty = host.querySelector(".configuration-empty");
    empty.hidden = false;
    empty.textContent = agentsPage ? "No agents defined yet. Create your first agent." : "No prompts in this library yet.";
  }
  if (!isEditor) return;
  if (query.has("id")) editing = catalog.agents.find(entry => entry.id === query.get("id"));
  if (query.has("name")) editing = entries.find(entry => entry.name === query.get("name"));
  if (!agentsPage && query.has("version")) editing = catalog.prompts.find(entry =>
    entry.name === query.get("name") && entry.version === Number(query.get("version")));
  if (!query.has("new") && !editing) {
    status.textContent = "This definition could not be found. Return to the list.";
    return;
  }
  if (editing?.systemAgent === "flow-designer") {
    const { mountDesignerChat } = await import("/admin/designer-chat.js?v=" + encodeURIComponent(window.lyraAssetVersion));
    mountDesignerChat(host.querySelector("#designer-chat"), editing);
    if (query.has("chat")) return;
  }
  form.hidden = false;
  form.querySelector("h3").textContent = (editing ? "Edit " : "Create ") + (agentsPage ? "agent" : "prompt");
  name.value = draft.data?.name ?? editing?.name ?? "";
  if (editing && draft.data?.revision) editing = { ...editing, revision: draft.data.revision };
  host.querySelector(".agent-fields").hidden = !agentsPage;
  host.querySelector(".prompt-fields").hidden = agentsPage;
  text.required = !agentsPage;
  instructions.required = prompt.required = agentsPage;
  if (!agentsPage) {
    name.readOnly = Boolean(editing);
    text.value = draft.data?.text ?? editing?.text ?? "";
  }

  function preview(select, field) {
    const entry = catalog.prompts.find(entry => referenceKey(entry) === select.value);
    host.querySelector("#" + field + "-preview").textContent = field === "prompt" && select.value === RAW_INPUT
      ? "Incoming flow text is used directly."
      : entry?.text ?? "";
    const edit = host.querySelector("#edit-" + field);
    edit.hidden = !entry;
    if (entry) edit.href = linkWithReturn("/admin/prompts/?" + new URLSearchParams({
      name: entry.name, version: entry.version,
    }), { field });
  }

  function modelLinks() {
    const links = host.querySelector("#agent-model-links");
    links.replaceChildren();
    const add = (label, page, params) => {
      const anchor = document.createElement("a");
      anchor.textContent = label;
      anchor.href = linkWithReturn("/admin/" + page + "/?" + new URLSearchParams(params));
      links.append(anchor, " ");
    };
    add("Create provider", "providers", {new: 1});
    const selected = catalog.providers.find(p => p.id === provider.value);
    if (!selected) return;
    add("Edit selected provider", "providers", {id: selected.id});
    add("Create model in provider group", "models", {new: 1, addToGroup: selected.groupId});
    const current = catalog.models.find(m => m.name === model.value);
    if (current) add("Edit selected model", "models", {id: current.id});
    add("Edit provider’s model group", "model-groups", {id: selected.groupId});
  }

  function populateModels(previous) {
    const selected = catalog.providers.find(row => row.id === provider.value);
    const group = catalog.modelGroups.find(row => row.id === selected?.groupId);
    model.disabled = !provider.value;
    model.required = Boolean(provider.value);
    model.replaceChildren(option("", selected ? "Choose from " + group.name + "…" : "Default chat model"),
      ...(group?.models ?? []).map(value => option(value, value)));
    model.value = group?.models.includes(previous) ? previous : "";
    modelLinks();
  }

  function populateProviders(id, selectedModel) {
    provider.replaceChildren(option("", "Use default chat model"), ...catalog.providers.map(row => option(row.id, row.name)));
    provider.value = id || "";
    populateModels(selectedModel);
  }

  let selectionsInitialized = false;
  function populatePrompts() {
    const defaultInstructions = catalog.prompts.filter(entry => entry.name === "Instructions.default")
      .sort((first, second) => second.version - first.version)[0];
    for (const [select, field] of [[instructions, "instructions"], [prompt, "prompt"]]) {
      let initial = field === "prompt" ? RAW_INPUT : defaultInstructions ? referenceKey(defaultInstructions) : "";
      if (editing) initial = referenceKey(editing[field]);
      const previous = selectionsInitialized ? select.value : draft.data?.[field] ?? initial;
      const placeholder = field === "prompt"
        ? option(RAW_INPUT, "Raw user input")
        : option("", "Choose system instructions…");
      select.replaceChildren(placeholder,
        ...catalog.prompts.map(entry => option(referenceKey(entry), promptLabel(entry))));
      select.value = previous;
      preview(select, field);
    }
    selectionsInitialized = true;
  }

  let readOutput;
  function persistDraft() {
    draft.save({
      name: name.value, instructions: instructions.value, prompt: prompt.value, text: text.value,
      revision: editing?.revision,
      providerId: provider.value, model: model.value,
      modelId: catalog.models.find(m => m.name === model.value)?.id,
      outputSchema: readOutput?.(),
    });
  }
  if (agentsPage) {
    const { mountOutputEditor } = await import("/admin/output-editor.js?v=" + encodeURIComponent(window.lyraAssetVersion));
    const output = host.querySelector("#agent-output");
    readOutput = mountOutputEditor(output, draft.data && "outputSchema" in draft.data
      ? draft.data.outputSchema : editing?.outputSchema, persistDraft);
    if (query.get("focus") === "output") output.querySelector("select").focus();
    populatePrompts();
    populateProviders(draft.data?.providerId ?? editing?.modelSelection?.providerId,
                      catalog.models.find(m => m.id === draft.data?.modelId)?.name ?? draft.data?.model ?? editing?.modelSelection?.model);
    provider.addEventListener("change", () => populateModels(""));
    model.addEventListener("change", modelLinks);
    for (const field of ["instructions", "prompt"]) {
      const link = host.querySelector("#create-" + field);
      link.href = linkWithReturn("/admin/prompts/?new=1", { field });
      link.addEventListener("click", persistDraft);
    }
    instructions.addEventListener("change", () => preview(instructions, "instructions"));
    prompt.addEventListener("change", () => preview(prompt, "prompt"));
    async function refresh() {
      try {
        catalog = await request("/api/admin/catalog");
        populatePrompts();
        populateProviders(provider.value, model.value);
        status.textContent = "Prompt library refreshed.";
      } catch (error) { status.textContent = error.message; }
    }
    host.querySelector("#refresh-library").addEventListener("click", refresh);
    window.addEventListener("focus", refresh);
  }
  persistDraft();
  form.addEventListener("input", persistDraft);
  form.addEventListener("change", persistDraft);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    save.disabled = true;
    status.textContent = "Saving…";
    try {
      let payload = { name: name.value, text: text.value };
      let endpoint = "/api/admin/prompts";
      let method = "POST";
      if (agentsPage) {
        const ref = select => {
          if (select.value === RAW_INPUT) return null;
          const [name, version] = JSON.parse(select.value);
          return { name, version };
        };
        payload = { name: name.value, instructions: ref(instructions), prompt: ref(prompt), outputSchema: readOutput(true) };
        payload.modelSelection = provider.value ? { providerId: provider.value, model: model.value } : null;
        endpoint = "/api/admin/agents";
        if (editing) {
          endpoint += "/" + editing.id;
          method = "PUT";
          payload.revision = editing.revision;
        }
      }
      const saved = await request(endpoint, {
        method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      draft.clear();
      if (!returnWithSelection(saved, agentsPage ? "agent" : "prompt")) {
        location.replace(base + "?saved=1");
      }
    } catch (error) {
      status.textContent = error.message;
      save.disabled = false;
    }
  });
}
