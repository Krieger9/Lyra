const sections = {
  "/admin/": "Configuration",
  "/admin/agents/": "Agents",
  "/admin/prompts/": "PromptStudio",
  "/admin/flow/": "Flow View",
  "/admin/memories/": "Memories",
  "/admin/personalities/": "Personalities",
  "/admin/continuity/": "Continuity",
  "/admin/providers/": "Providers",
  "/admin/models/": "Models",
  "/admin/model-groups/": "Model groups",
  "/admin/protocols/": "Protocols",
};

function adminUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value, location.origin);
    return url.origin === location.origin && sections[url.pathname] ? url : null;
  } catch { return null; }
}

const relativeUrl = url => url.pathname + url.search;
const storageKey = id => "lyra.adminDraft." + id;

export async function prepareFlowView(accountId, loadChatFlow) {
  const key = "lyra.lastFlowView." + accountId;
  const url = new URL(location.href);
  const explicit = ["id", "run", "draft", "new"].some(param => url.searchParams.has(param));
  // The editor adds the draft ID on mount and the flow ID on the first Save.
  window.addEventListener("pagehide", () => {
    const params = new URLSearchParams(location.search);
    const draft = params.get("draft");
    if (!draft) return;
    const last = new URLSearchParams({ draft });
    if (params.has("id")) last.set("id", params.get("id"));
    sessionStorage.setItem(key, last.toString());
  });
  if (explicit) return;
  const previous = new URLSearchParams(sessionStorage.getItem(key) || "");
  const draft = previous.get("draft");
  if (draft && sessionStorage.getItem(storageKey(draft))) {
    url.searchParams.set("draft", draft);
    if (previous.has("id")) url.searchParams.set("id", previous.get("id"));
  } else {
    const binding = await loadChatFlow();
    if (!binding) return;
    url.searchParams.set("id", binding.id);
  }
  history.replaceState(history.state, "", relativeUrl(url));
}

export function draftSession() {
  const url = new URL(location.href);
  let id = url.searchParams.get("draft");
  if (!id || !/^[a-f0-9-]{36}$/.test(id)) {
    id = crypto.randomUUID();
    url.searchParams.set("draft", id);
    history.replaceState(history.state, "", relativeUrl(url));
  }
  let data = null;
  try { data = JSON.parse(sessionStorage.getItem(storageKey(id))); } catch {}
  return {
    id, data,
    save(value) { sessionStorage.setItem(storageKey(id), JSON.stringify(value)); },
    clear() { sessionStorage.removeItem(storageKey(id)); },
  };
}

export function linkWithReturn(destination, context = {}) {
  const url = adminUrl(destination);
  if (!url) throw new Error("Unknown admin destination");
  url.searchParams.set("returnTo", location.pathname + location.search);
  for (const [key, value] of Object.entries(context)) url.searchParams.set(key, value);
  return relativeUrl(url);
}

export function returnDestination(fallback) {
  return relativeUrl(adminUrl(new URLSearchParams(location.search).get("returnTo")) ?? adminUrl(fallback));
}

export function returnWithSelection(saved, kind) {
  const query = new URLSearchParams(location.search);
  const destination = adminUrl(query.get("returnTo"));
  if (!destination) return false;
  const id = destination.searchParams.get("draft");
  let draft = null;
  try { draft = JSON.parse(sessionStorage.getItem(storageKey(id))); } catch {}
  if (draft && kind === "prompt" && destination.pathname === "/admin/agents/") {
    const field = query.get("field");
    if (field === "instructions" || field === "prompt") {
      draft[field] = JSON.stringify([saved.name, saved.version]);
    }
  }
  if (draft && kind === "agent" && destination.pathname === "/admin/flow/") {
    draft.items = draft.items.map(node => node.type === "agent" && node.id === query.get("node")
      ? { ...node, data: { label: saved.name, definition: saved } } : node);
  }
  if (draft) {
    if (destination.pathname === "/admin/model-groups/" && kind === "model") {
      draft.modelIds = [...new Set([...(draft.modelIds || []), saved.id])];
    } else if (destination.pathname === "/admin/providers/") {
      if (kind === "model group") draft.groupId = saved.id;
      if (kind === "protocol") draft.protocolId = saved.id;
    } else if (destination.pathname === "/admin/agents/") {
      if (kind === "provider") draft.providerId = saved.id;
      if (kind === "model") { draft.modelId = saved.id; draft.model = saved.name; }
    }
    sessionStorage.setItem(storageKey(id), JSON.stringify(draft));
  }
  location.replace(relativeUrl(destination));
  return true;
}

export function renderBreadcrumbs() {
  const container = document.querySelector("#breadcrumbs");
  const crumbs = [{ title: "Configuration", href: "/admin/" }];
  function append(url, depth = 0) {
    if (depth > 5) return;
    const parent = adminUrl(url.searchParams.get("returnTo"));
    if (parent && relativeUrl(parent) !== relativeUrl(url)) append(parent, depth + 1);
    if (url.pathname === "/admin/") return;
    const editor = url.pathname !== "/admin/flow/" &&
      (url.searchParams.has("new") || url.searchParams.has("id") || url.searchParams.has("name"));
    const listUrl = new URL(url);
    for (const key of ["new", "id", "name", "draft", "saved"]) listUrl.searchParams.delete(key);
    crumbs.push({ title: sections[url.pathname], href: editor ? relativeUrl(listUrl) : relativeUrl(url) });
    if (editor) {
      const noun = sections[url.pathname] === "PromptStudio" ? "prompt" :
        sections[url.pathname] === "Personalities" ? "personality" : sections[url.pathname].slice(0, -1).toLowerCase();
      crumbs.push({ title: (url.searchParams.has("new") ? "Create " : "Edit ") + noun, href: relativeUrl(url) });
    }
  }
  append(new URL(location.href));
  const list = document.createElement("ol");
  crumbs.forEach((crumb, index) => {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = crumb.href;
    link.textContent = crumb.title;
    if (index === crumbs.length - 1) link.setAttribute("aria-current", "page");
    item.append(link);
    list.append(item);
  });
  container.replaceChildren(list);
}

export function initializeNavigation() {
  document.querySelector("#admin-navigation").hidden = false;
  document.querySelector("#admin-back").addEventListener("click", () => history.back());
  document.querySelector("#admin-forward").addEventListener("click", () => history.forward());
  // Rehydrate from the tab's draft after browser history restores a cached page.
  window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
  renderBreadcrumbs();
}
