const api = (path = "", options) => window.lyraAdminRequest("/api/admin/personalities" + path, options);
const json = (method, body) => ({method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
function el(tag, text = "") { const node = document.createElement(tag); node.textContent = text; return node; }
function link(text, href) { const node = el("a", text); node.href = href; return node; }

export async function mountPersonalities() {
  const host = document.getElementById("personalities-page");
  const params = new URLSearchParams(location.search);
  let id = params.get("id");
  if (!id && !params.has("new")) {
    host.append(link("Create personality", "/admin/personalities/?new=1"),
      el("p", "Write a reusable voice, goals and background memories, then select it on a flow’s API Input."));
    const list = el("ul"); list.className = "configuration-list";
    const profiles = await api();
    for (const profile of profiles) {
      const row = el("li");
      row.append(link(profile.name, "/admin/personalities/?id=" + encodeURIComponent(profile.id)),
        el("span", " · revision " + profile.revision));
      list.append(row);
    }
    host.append(list);
    if (!profiles.length) host.append(el("p", "No personalities yet."));
    return;
  }
  let saved = id ? await api("/" + encodeURIComponent(id)) : null;
  host.innerHTML = `<form class="configuration-form personality-form">
    <label for="personality-name">Name</label><input id="personality-name" maxlength="120" required>
    <label for="personality-template">Prompt template</label><textarea id="personality-template" rows="10" maxlength="12000" required></textarea>
    <p>Placeholders: <code>{{name}}</code>, <code>{{goals}}</code>, <code>{{memories}}</code>.
    Goals and memories are appended when their placeholders are omitted. Other template expressions are not supported.</p>
    <label for="personality-goals">Goals — one per line (up to 20)</label><textarea id="personality-goals" rows="4"></textarea>
    <label for="personality-constitution">Constitution — durable commitments</label><textarea id="personality-constitution" rows="6" maxlength="6000"></textarea>
    <label for="personality-persona">Persona — temperament, interests and voice</label><textarea id="personality-persona" rows="5" maxlength="4000"></textarea>
    <label><input type="checkbox" id="personality-continuity">Enable private continuity, library tools and automatic reflection</label>
    <p>Each user's first conversation copies this foundation. Later development belongs to that user and follows this personality across flows and models. Foundation edits do not overwrite existing private continuity. Tools require an OpenAI or Claude provider.</p>
    <h3>Personality memories</h3><p>All assigned memories accompany this personality. These are shared background facts for anyone using the flow.
    General chat memories stay private to their owner. Copying one here makes the selected fact part of the shared personality.</p>
    <div id="personality-memories"></div><button type="button" id="add-personality-memory">Write a memory</button>
    <details><summary>Copy a general memory</summary>
      <label for="personality-memory-query">Search general memories</label><input id="personality-memory-query" maxlength="2000">
      <button type="button" id="search-personality-memory">Search</button><ul id="personality-memory-results" class="configuration-list"></ul>
    </details>
    <div class="configuration-actions"><button type="submit">Save personality</button><button type="button" id="preview-personality">Preview prompt</button></div>
    <p role="status"></p><pre id="personality-preview" hidden></pre>
    <p>Add a Personality card to the flow, select this profile, and connect it to each agent that should use it.
    Save the flow and activate it for chat.
    Reactivate after personality edits to load the new revision. State updaters keep their own instructions.</p>
  </form>`;
  const form = host.querySelector("form");
  const name = form.querySelector("#personality-name"), template = form.querySelector("#personality-template");
  const goals = form.querySelector("#personality-goals"), memoryHost = form.querySelector("#personality-memories");
  const status = form.querySelector('[role="status"]'), preview = form.querySelector("pre");
  name.value = saved?.name ?? "";
  template.value = saved?.template ?? "You are {{name}}.\n\n{{goals}}\n\n{{memories}}";
  goals.value = saved?.goals.join("\n") ?? "";
  let memories = [];
  function addMemory(memory = {content: ""}) {
    if (memories.length >= 24) { status.textContent = "A personality can have up to 24 memories."; return; }
    const entry = {...memory};
    const row = el("div"); row.className = "personality-memory";
    const label = el("label", "Memory");
    const input = el("textarea"); input.rows = 2; input.maxLength = 2000; input.required = true; input.value = entry.content;
    input.oninput = () => { entry.content = input.value; };
    label.append(input); row.append(label);
    if (entry.sourceMemoryId) row.append(link("Source memory and its turns", "/admin/memories/?id=" + encodeURIComponent(entry.sourceMemoryId)));
    const remove = el("button", "Remove memory"); remove.type = "button";
    remove.onclick = () => { memories = memories.filter(m => m !== entry); row.remove(); dirty = true; preview.hidden = true; };
    row.append(remove); memoryHost.append(row); memories.push(entry);
  }
  for (const memory of saved?.memories ?? []) addMemory(memory);
  let dirty = false;
  form.addEventListener("input", () => { dirty = true; preview.hidden = true; });
  window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
  form.querySelector("#add-personality-memory").onclick = () => { addMemory(); dirty = true; preview.hidden = true; };
  const constitution = form.querySelector("#personality-constitution"), persona = form.querySelector("#personality-persona");
  const continuity = form.querySelector("#personality-continuity");
  constitution.value = saved?.constitution ?? ""; persona.value = saved?.persona ?? "";
  continuity.checked = saved?.continuityEnabled ?? false;
  const body = () => ({name: name.value, template: template.value, constitution: constitution.value,
    persona: persona.value, continuityEnabled: continuity.checked,
    goals: goals.value.split("\n").map(s => s.trim()).filter(Boolean), memories});
  let busy = false;
  async function action(work) {
    if (busy) return;
    busy = true;
    const controls = [...form.querySelectorAll("button,input,textarea")];
    controls.forEach(button => { button.disabled = true; });
    status.textContent = "Working…";
    try { await work(); } catch (error) { status.textContent = error.message; }
    finally { controls.forEach(button => { button.disabled = false; }); busy = false; }
  }
  form.onsubmit = event => {
    event.preventDefault();
    void action(async () => {
      const payload = body();
      saved = await api(id ? "/" + encodeURIComponent(id) : "", json(id ? "PUT" : "POST", id ? {...payload, revision: saved.revision} : payload));
      id = saved.id; memories = []; memoryHost.replaceChildren();
      for (const memory of saved.memories) addMemory(memory);
      dirty = false;
      const url = new URL(location.href); url.searchParams.delete("new"); url.searchParams.set("id", id);
      history.replaceState(null, "", url);
      status.textContent = `Saved revision ${saved.revision}. Select this personality in your flow and activate it to apply this revision.`;
    });
  };
  form.querySelector("#preview-personality").onclick = () => {
    if (!form.reportValidity()) return;
    void action(async () => { preview.textContent = (await api("/preview", json("POST", body()))).prompt;
      preview.hidden = false; status.textContent = "Preview of the personality section added to agent instructions."; });
  };
  form.querySelector("#search-personality-memory").onclick = () => { void action(async () => {
    const query = form.querySelector("#personality-memory-query").value;
    const page = await window.lyraAdminRequest("/api/admin/memories?limit=30&query=" + encodeURIComponent(query));
    const results = form.querySelector("#personality-memory-results"); results.replaceChildren();
    for (const memory of page.memories) {
      const row = el("li"), copy = el("button", "Copy into personality"); copy.type = "button";
      copy.onclick = () => {
        if (memories.some(m => m.sourceMemoryId === memory.id)) { status.textContent = "This memory is already assigned."; return; }
        addMemory({content: memory.content, sourceMemoryId: memory.id}); dirty = true; preview.hidden = true;
      };
      row.append(el("p", memory.content), copy); results.append(row);
    }
    status.textContent = page.memories.length ? "Choose facts to share with this personality." : "No matching memories.";
  }); };
  if (id) {
    host.append(link("Open my private continuity and library", "/admin/continuity/?id=" + encodeURIComponent(id)));
    const runs = await api("/" + encodeURIComponent(id) + "/runs");
    host.append(el("h3", "Recent turns using this personality"));
    const list = el("ul"); list.className = "configuration-list";
    for (const run of runs) {
      const row = el("li");
      row.append(link(`${new Date(run.started_at).toLocaleString()} · personality revision ${run.personality_revision} · ${run.status}`,
        `/admin/flow/?id=${encodeURIComponent(run.flow_id)}&run=${encodeURIComponent(run.id)}`)); list.append(row);
    }
    host.append(list, el("p", runs.length ? "Showing the latest 50 runs. Inspect a run for its exact personality and memories." : "No runs recorded yet."));
  }
}
