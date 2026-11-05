const el = (tag, text = "") => { const n = document.createElement(tag); n.textContent = text; return n; };
const button = (label, action) => { const n = el("button", label); n.type = "button"; n.onclick = action; return n; };
const json = body => ({method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});

export async function mountContinuity() {
  const host = document.getElementById("continuity-page");
  const profiles = await window.lyraAdminRequest("/api/admin/personalities");
  let id = new URLSearchParams(location.search).get("id");
  const pickerLabel = el("label", "Personality"), picker = el("select");
  picker.append(new Option("Choose a personality…", ""));
  for (const p of profiles) picker.append(new Option(p.name, p.id));
  picker.value = id ?? ""; pickerLabel.append(picker);
  picker.onchange = () => { location.href = "/admin/continuity/?id=" + encodeURIComponent(picker.value); };
  const status = el("p"); status.setAttribute("role", "status");
  const content = el("div"); host.append(pickerLabel, status, content);
  if (!id || !profiles.some(p => p.id === id)) return;
  const api = (path = "", options) => window.lyraAdminRequest("/api/admin/continuity/" + encodeURIComponent(id) + path, options);
  let saved, selected = "library", offset = 0, busy = false;
  async function action(work) {
    if (busy) return;
    busy = true; status.textContent = "Working…";
    host.querySelectorAll("button").forEach(b => b.disabled = true);
    try { await work(); if (status.textContent === "Working…") status.textContent = "Ready."; }
    catch (e) { status.textContent = e.message; }
    finally { busy = false; host.querySelectorAll("button").forEach(b => b.disabled = false); }
  }
  async function change(tool, arguments_) {
    await api("/change", json({revision: saved.revision, tool, arguments: arguments_})); await load();
  }
  function field(form, label, value = "", multiline = false) {
    const wrapper = el("label", label), input = el(multiline ? "textarea" : "input");
    if (multiline) input.rows = 4;
    input.value = value; wrapper.append(input); form.append(wrapper); return input;
  }
  function select(form, label, values, value) {
    const wrapper = el("label", label), input = el("select");
    for (const [v, name] of values) input.append(new Option(name, v));
    input.value = value; wrapper.append(input); form.append(wrapper); return input;
  }
  function details(label, value) { const d = el("details"), pre = el("pre", JSON.stringify(value, null, 2));
    d.append(el("summary", label), pre); return d; }
  async function navigate(node) { selected = node; offset = 0; await renderLibrary(); }
  let library;
  async function renderLibrary() {
    const page = await api("/browse?node_id=" + encodeURIComponent(selected) + "&offset=" + offset);
    library.replaceChildren();
    const crumbs = el("nav"); crumbs.setAttribute("aria-label", "Library location");
    for (const n of page.ancestors) crumbs.append(button(n.title, () => action(() => navigate(n.id))), el("span", " / "));
    crumbs.append(el("strong", page.node.title)); library.append(crumbs);
    library.append(el("h3", page.node.title), el("p", page.node.summary));
    const text = el("p", page.node.content); text.style.whiteSpace = "pre-wrap"; library.append(text);
    if (page.node.source_refs.length) library.append(details("Source references and attribution", {sources: page.node.source_refs, attribution: page.node.attribution}));
    const list = el("ul"); list.className = "configuration-list";
    for (const n of page.children) { const row = el("li"); row.append(button(n.title, () => action(() => navigate(n.id))),
      el("span", " · " + n.kind + " · " + n.status), el("p", n.summary)); list.append(row); }
    library.append(list);
    if (offset) library.append(button("Previous items", () => action(async () => { offset = Math.max(0, offset - 20); await renderLibrary(); })));
    if (page.hasMore) library.append(button("More items", () => action(async () => { offset += 20; await renderLibrary(); })));
    if (page.links.length) {
      library.append(el("h4", "Connections"));
      for (const link of page.links) {
        const target = link.source === selected ? link.target : link.source;
        library.append(button(link.relation + " → " + (saved.document.nodes.find(n => n.id === target)?.title ?? target), () => action(() => navigate(target))));
      }
    }
    const searchForm = el("form"), query = field(searchForm, "Search this branch");
    searchForm.append(button("Search", () => action(async () => {
      const result = await api("/search?query=" + encodeURIComponent(query.value) + "&within=" + encodeURIComponent(selected));
      results.replaceChildren();
      for (const key of ["matches", "associations"]) {
        results.append(el("h4", key === "matches" ? "Matches" : "Related items"));
        for (const n of result[key]) results.append(button(n.title, () => action(() => navigate(n.id))), el("p", n.summary));
      }
    })));
    searchForm.onsubmit = e => { e.preventDefault(); searchForm.querySelector("button").click(); };
    const results = el("div"); library.append(searchForm, results);
    library.append(button("Add item here", () => editNode({parent: selected, kind: "note", title: "", summary: "", content: "", status: "active"})),
      button("Edit this item", () => editNode(page.node)));
    const linkForm = el("form"); linkForm.className = "configuration-form";
    const target = select(linkForm, "Connect to", saved.document.nodes.filter(n => n.id !== selected).map(n => [n.id, n.title]), "library");
    const relation = select(linkForm, "Relationship", ["related", "supports", "contradicts", "depends_on", "inspired_by"].map(v => [v, v]), "related");
    linkForm.append(button("Add connection", () => action(() => change("library_link", {link: {source: selected, target: target.value, relation: relation.value}, reason: "Owner connected library items"}))),
      button("Remove connection", () => action(() => change("library_link", {link: {source: selected, target: target.value, relation: relation.value}, remove: true, reason: "Owner removed connection"}))));
    linkForm.onsubmit = e => e.preventDefault();
    const linkDetails = el("details"); linkDetails.append(el("summary", "Organize connections"), linkForm); library.append(linkDetails);
    const archived = saved.document.nodes.filter(n => n.status === "archived");
    if (archived.length) { const d = el("details"); d.append(el("summary", "Archived items"));
      for (const n of archived) d.append(button(n.title, () => action(() => navigate(n.id)))); library.append(d); }
  }
  function editNode(node) {
    const form = el("form"); form.className = "configuration-form";
    const title = field(form, "Title", node.title), summary = field(form, "Overview", node.summary, true), body = field(form, "Details and evidence", node.content, true);
    const parent = select(form, "Place under", saved.document.nodes.filter(n => n.id !== node.id).map(n => [n.id, n.title]), node.parent ?? "library");
    const kind = select(form, "Item type", ["section", "topic", "interest", "goal", "memory", "note", "evidence"].map(v => [v, v]), node.kind);
    const state = select(form, "Status", ["active", "completed", "archived"].map(v => [v, v]), node.status);
    const reason = field(form, "Reason for this change", "Owner curated the library");
    form.append(button("Save item", () => action(async () => {
      await change("library_write", {node: {...node, id: node.id ?? crypto.randomUUID(), title: title.value, summary: summary.value,
        content: body.value, parent: node.id === "library" ? null : parent.value, kind: kind.value, status: state.value}, reason: reason.value});
    })), button("Cancel", () => action(renderLibrary)));
    form.onsubmit = e => e.preventDefault(); library.replaceChildren(form);
  }
  async function load() {
    saved = await api(); content.replaceChildren();
    content.append(el("h2", saved.document.profile.name + " · private continuity"), el("p", "Version " + saved.revision + ". Shared across your flows and models. Changes here affect only your continuation."));
    const form = el("form"); form.className = "configuration-form";
    const p = saved.document.profile, name = field(form, "Name", p.name), constitution = field(form, "Constitution", p.constitution, true), persona = field(form, "Persona", p.persona, true), goals = field(form, "Guiding goals — one per line", p.goals.join("\n"), true), reason = field(form, "Reason for revision", "Owner revised the private profile");
    form.append(button("Save private profile", () => action(() => change("identity_revise", {profile: {name: name.value, constitution: constitution.value, persona: persona.value, goals: goals.value.split("\n").map(s => s.trim()).filter(Boolean)}, reason: reason.value}))));
    form.onsubmit = e => e.preventDefault(); const profile = el("details"); profile.append(el("summary", "Identity and commitments"), form); content.append(profile);
    content.append(details("Current focus and mood", saved.context.working_state));
    content.append(button("Reflect now", () => action(async () => { const r = await api("/reflect", json({})); await load(); status.textContent = `Reflection finished; ${r.changes} changes.`; })));
    const exportButton = button("Export continuity", () => action(async () => {
      const data = await api("/export"), url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: "application/json"}));
      const a = el("a"); a.href = url; a.download = "lyra-continuity.json"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    })); content.append(exportButton);
    const importLabel = el("label", "Import continuity (creates a new version)"); const file = el("input"); file.type = "file"; file.accept = ".json";
    file.onchange = () => action(async () => { if (!file.files[0]) return;
      if (file.files[0].size > 600000) throw new Error("Continuity file is too large.");
      const data = JSON.parse(await file.files[0].text()); if (data.format !== "lyra-continuity-v1") throw new Error("Unsupported continuity format.");
      await api("/import", json({revision: saved.revision, document: data.document})); selected = "library"; await load();
    }); importLabel.append(file); content.append(importLabel);
    library = el("div"); library.className = "continuity-library"; content.append(library); await renderLibrary();
    const history = el("details"); history.append(el("summary", "Version history and restore (latest 50)"));
    for (const v of saved.versions) { const row = el("div"); row.append(el("p", `Version ${v.revision} · ${new Date(v.created_at).toLocaleString()} · ${v.reason}`));
      if (v.revision !== saved.revision) row.append(button("Restore version " + v.revision, () => action(async () => {
        await api("/restore", json({revision: saved.revision, target_revision: v.revision})); selected = "library"; await load();
      }))); history.append(row); }
    content.append(history, details("Source events (latest 50)", saved.events), details("Context preview and selection reasons", saved.context));
  }
  await action(load);
}
