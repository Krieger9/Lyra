const { filterPrompts, normalizeSet } = await import("/admin/prompt-sets.js?v=" + encodeURIComponent(window.lyraAssetVersion));
const { renderBreadcrumbs } = await import("/admin/navigation.js?v=" + encodeURIComponent(window.lyraAssetVersion));

const element = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
const option = (value, label) => {
  const node = element("option", "", label);
  node.value = value;
  return node;
};

export async function mountPromptBrowser(host) {
  const request = (...args) => window.lyraAdminRequest(...args);
  const catalog = await request("/api/admin/catalog");
  let sets = catalog.promptSets ?? [];
  const versions = new Map();
  for (const prompt of catalog.prompts) {
    if (!versions.has(prompt.name)) versions.set(prompt.name, []);
    versions.get(prompt.name).push(prompt);
  }
  for (const entries of versions.values()) entries.sort((a, b) => b.version - a.version);
  const prompts = [...versions.values()].map(entries => entries[0]).sort((a, b) => a.name.localeCompare(b.name));
  const storageKey = "lyra.promptSet." + (window.lyraPromptSetScope ?? "default");
  let remembered;
  try { remembered = localStorage.getItem(storageKey); } catch {}
  let activeSet = normalizeSet(new URLSearchParams(location.search).get("set") ?? remembered, sets);
  let selectedPrompt = null;
  let visible = [];
  const checked = new Set();
  let busy = false;
  host.classList.add("prompt-browser");
  host.innerHTML = `
    <div class="studio-heading">
      <div><h2 id="studio-title"></h2><p id="studio-summary"></p></div>
      <a class="studio-primary" id="studio-create">＋ Create prompt</a>
    </div>
    <div class="studio-grid">
      <aside class="studio-sets" aria-label="Prompt sets">
        <div class="studio-section-heading"><h3>Prompt sets</h3><button type="button" id="studio-new-set" title="Create prompt set" aria-label="Create prompt set" aria-expanded="false" aria-controls="studio-set-form">＋</button></div>
        <form id="studio-set-form" hidden>
          <label for="studio-set-name">New set name</label>
          <input id="studio-set-name" required maxlength="120" placeholder="e.g. Research flow" autocomplete="off">
          <button type="submit" class="studio-primary">Create set</button>
        </form>
        <nav id="studio-set-list" aria-label="Filter by prompt set"></nav>
        <p class="studio-set-hint">Keep each flow’s prompts together. A shared prompt can belong to multiple sets.</p>
      </aside>
      <section class="studio-library" aria-label="Prompts">
        <div class="studio-search"><label class="sr-only" for="studio-search">Search prompts</label><input id="studio-search" type="search" placeholder="Search names or prompt text…" autocomplete="off"></div>
        <div class="studio-list-heading"><label><input id="studio-select-all" type="checkbox"> Select all</label><span id="studio-result-count" role="status"></span></div>
        <div class="studio-bulk" hidden>
          <span id="studio-selected-count"></span>
          <label class="sr-only" for="studio-target">Add selected prompts to set</label><select id="studio-target"></select>
          <button type="button" id="studio-add">Add to set</button><button type="button" id="studio-remove">Remove from set</button>
          <button type="button" id="studio-clear">Clear</button>
        </div>
        <ul class="studio-prompt-list" aria-label="Prompt list"></ul>
        <div class="studio-empty" hidden><span aria-hidden="true">◇</span><h3></h3><p></p><a class="studio-primary"></a></div>
      </section>
      <section class="studio-detail" aria-label="Prompt preview">
        <div class="studio-preview-empty"><span aria-hidden="true">▤</span><h3>Your prompt, at a glance</h3><p>Select a prompt to read its template and browse its versions.</p></div>
        <div class="studio-preview-content" hidden>
          <div class="studio-detail-heading"><span class="studio-kicker">Prompt preview</span><a id="studio-edit">Edit prompt ↗</a></div>
          <h3 id="studio-prompt-name"></h3>
          <div class="studio-version-bar"><label for="studio-version">Version</label><select id="studio-version"></select><span id="studio-length"></span></div>
          <div id="studio-memberships" class="studio-tags"></div>
          <pre id="studio-template" tabindex="0" aria-label="Prompt template"></pre>
        </div>
      </section>
    </div>
    <p class="studio-status" role="status"></p>`;
  const find = selector => host.querySelector(selector);
  const status = find(".studio-status");
  const search = find("#studio-search");
  const list = find(".studio-prompt-list");
  const target = find("#studio-target");
  const all = find("#studio-select-all");
  const version = find("#studio-version");
  function url(params) {
    const query = new URLSearchParams({set: activeSet, ...params});
    const context = new URLSearchParams(location.search);
    for (const key of ["returnTo", "field", "node"]) if (context.has(key)) query.set(key, context.get(key));
    return "/admin/prompts/?" + query;
  }
  function remember() {
    try { localStorage.setItem(storageKey, activeSet); } catch {}
    const current = new URL(location.href);
    current.searchParams.set("set", activeSet);
    history.replaceState(history.state, "", current.pathname + current.search);
  }
  function renderSets() {
    const nav = find("#studio-set-list");
    nav.replaceChildren();
    const choices = [{id: "all", name: "All prompts", count: prompts.length},
      {id: "unfiled", name: "Unfiled", count: filterPrompts(prompts, sets, "unfiled").length},
      ...sets.map(set => ({...set, count: set.prompts.length}))];
    for (const choice of choices) {
      const button = element("button", "studio-set");
      button.type = "button";
      button.dataset.set = choice.id;
      button.setAttribute("aria-pressed", String(choice.id === activeSet));
      button.append(element("span", "", choice.name), element("span", "studio-count", choice.count));
      button.addEventListener("click", () => {
        activeSet = choice.id;
        checked.clear(); search.value = ""; selectedPrompt = null;
        remember(); renderSets(); renderList();
      });
      nav.append(button);
    }
    const previous = target.value;
    target.replaceChildren(option("", "Choose a set…"), ...sets.map(set => option(set.id, set.name)));
    target.value = sets.some(set => set.id === previous) ? previous : sets.some(set => set.id === activeSet) ? activeSet : "";
    find("#studio-title").textContent = choices.find(choice => choice.id === activeSet)?.name ?? "All prompts";
    find("#studio-summary").textContent = "Your reusable prompts, organized for the work ahead.";
    find("#studio-create").href = url({new: "1"});
  }
  function selectionChanged() {
    find(".studio-bulk").hidden = checked.size === 0;
    find("#studio-selected-count").textContent = checked.size + " selected";
    find("#studio-add").disabled = busy || !target.value || !checked.size;
    find("#studio-remove").disabled = busy || !checked.size;
    find("#studio-remove").hidden = !sets.some(set => set.id === activeSet);
    all.checked = visible.length > 0 && checked.size === visible.length;
    all.indeterminate = checked.size > 0 && checked.size < visible.length;
    all.disabled = busy || !visible.length;
    target.disabled = busy;
    find("#studio-clear").disabled = busy;
    for (const input of list.querySelectorAll("input")) { input.checked = checked.has(input.value); input.disabled = busy; }
  }
  function renderTemplate() {
    const entry = versions.get(selectedPrompt)?.find(entry => String(entry.version) === version.value);
    if (!entry) return;
    find("#studio-template").textContent = entry.text;
    find("#studio-length").textContent = entry.text.length.toLocaleString() + " characters";
    find("#studio-edit").href = url({name: entry.name, version: entry.version});
  }
  function renderPreview() {
    const entries = versions.get(selectedPrompt);
    find(".studio-preview-empty").hidden = Boolean(entries);
    find(".studio-preview-content").hidden = !entries;
    for (const button of list.querySelectorAll(".studio-prompt-open")) {
      button.setAttribute("aria-pressed", String(button.dataset.name === selectedPrompt));
    }
    if (!entries) return;
    find("#studio-prompt-name").textContent = selectedPrompt;
    version.replaceChildren(...entries.map((entry, index) => option(entry.version, "v" + entry.version + (index === 0 ? " · Latest" : ""))));
    find("#studio-memberships").replaceChildren(...sets.filter(set => set.prompts.includes(selectedPrompt))
      .map(set => element("span", "studio-tag", set.name)));
    renderTemplate();
  }
  function renderList() {
    const needle = search.value.trim().toLocaleLowerCase();
    const scoped = filterPrompts(prompts, sets, activeSet);
    visible = scoped.filter(prompt => (prompt.name + "\n" + prompt.text).toLocaleLowerCase().includes(needle));
    for (const name of checked) if (!visible.some(entry => entry.name === name)) checked.delete(name);
    list.replaceChildren();
    for (const entry of visible) {
      const row = element("li", "studio-prompt-row");
      const checkbox = element("input"); checkbox.type = "checkbox"; checkbox.value = entry.name;
      checkbox.setAttribute("aria-label", "Select " + entry.name);
      checkbox.addEventListener("change", () => { checkbox.checked ? checked.add(entry.name) : checked.delete(entry.name); selectionChanged(); });
      const button = element("button", "studio-prompt-open"); button.type = "button"; button.dataset.name = entry.name;
      const heading = element("span", "studio-row-heading");
      heading.append(element("span", "studio-row-name", entry.name), element("span", "studio-version-badge", "v" + entry.version));
      button.append(heading, element("span", "studio-excerpt", entry.text.replace(/\s+/g, " ").slice(0, 180)));
      button.addEventListener("click", () => { selectedPrompt = entry.name; renderPreview(); });
      row.append(checkbox, button); list.append(row);
    }
    find("#studio-result-count").textContent = needle ? visible.length + " of " + scoped.length + " prompts" : visible.length + (visible.length === 1 ? " prompt" : " prompts");
    const empty = find(".studio-empty"); empty.hidden = visible.length > 0;
    empty.querySelector("h3").textContent = needle ? "No matching prompts" : "A fresh space for your prompts";
    empty.querySelector("p").textContent = needle ? "Try a different name or a phrase from the template." : "Create the first prompt for this set, or add existing prompts from All prompts.";
    const emptyLink = empty.querySelector("a"); emptyLink.hidden = Boolean(needle);
    emptyLink.href = url({new: "1"}); emptyLink.textContent = "＋ Create prompt";
    if (!visible.some(entry => entry.name === selectedPrompt)) selectedPrompt = visible[0]?.name ?? null;
    selectionChanged(); renderPreview();
  }
  search.addEventListener("input", renderList);
  version.addEventListener("change", renderTemplate);
  target.addEventListener("change", selectionChanged);
  all.addEventListener("change", () => { checked.clear(); if (all.checked) visible.forEach(entry => checked.add(entry.name)); selectionChanged(); });
  find("#studio-clear").addEventListener("click", () => { checked.clear(); selectionChanged(); });
  const setForm = find("#studio-set-form");
  find("#studio-new-set").addEventListener("click", event => {
    setForm.hidden = !setForm.hidden;
    event.currentTarget.setAttribute("aria-expanded", String(!setForm.hidden));
    if (!setForm.hidden) find("#studio-set-name").focus();
  });
  setForm.addEventListener("submit", async event => {
    event.preventDefault();
    const name = find("#studio-set-name").value.trim();
    if (!name) return;
    const submit = setForm.querySelector("button"); submit.disabled = true;
    try {
      const saved = await request("/api/admin/prompt-sets", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name})});
      sets.push(saved); activeSet = saved.id; checked.clear(); search.value = ""; selectedPrompt = null;
      remember(); renderSets(); renderList(); setForm.reset(); setForm.hidden = true;
      find("#studio-new-set").setAttribute("aria-expanded", "false");
      find("#studio-create").focus(); status.textContent = "Set created. New prompts will be added here.";
    } catch (error) { status.textContent = error.message; }
    finally { submit.disabled = false; }
  });
  for (const [id, remove] of [["studio-add", false], ["studio-remove", true]]) {
    find("#" + id).addEventListener("click", async () => {
      if (busy || !checked.size) return;
      const setId = remove ? activeSet : target.value;
      const names = [...checked];
      busy = true; selectionChanged();
      try {
        await request("/api/admin/prompt-sets/" + setId + "/members", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({names, remove})});
        const set = sets.find(set => set.id === setId);
        set.prompts = remove ? set.prompts.filter(name => !names.includes(name)) : [...new Set([...set.prompts, ...names])];
        checked.clear(); renderSets(); renderList();
        status.textContent = names.length + (names.length === 1 ? " prompt " : " prompts ") + (remove ? "removed from " : "added to ") + set.name + ".";
      } catch (error) { status.textContent = error.message; }
      finally { busy = false; selectionChanged(); }
    });
  }
  remember(); renderBreadcrumbs(); renderSets(); renderList();
  if (new URLSearchParams(location.search).has("saved")) status.textContent = "Prompt saved.";
}
