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
    <form class="configuration-form" hidden>
      <h3></h3>
      <label for="definition-name">Name</label>
      <input id="definition-name" required autocomplete="off">
      <div class="agent-fields" hidden>
        <label for="instructions">System instructions</label>
        <select id="instructions" required></select>
        <a id="create-instructions" href="/admin/prompts/?new=1">Create instructions in PromptStudio</a>
        <pre id="instructions-preview"></pre>
        <label for="prompt">Prompt</label>
        <select id="prompt" required></select>
        <p>Use raw user input to pass incoming flow text directly, or select a template to format it.</p>
        <a id="create-prompt" href="/admin/prompts/?new=1">Create a prompt in PromptStudio</a>
        <pre id="prompt-preview"></pre>
        <button id="refresh-library" type="button">Refresh prompt library</button>
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
  if (!query.has("new") && !editing) {
    status.textContent = "This definition could not be found. Return to the list.";
    return;
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

  function persistDraft() {
    draft.save({
      name: name.value, instructions: instructions.value, prompt: prompt.value, text: text.value,
      revision: editing?.revision,
    });
  }
  if (agentsPage) {
    populatePrompts();
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
        payload = { name: name.value, instructions: ref(instructions), prompt: ref(prompt) };
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
