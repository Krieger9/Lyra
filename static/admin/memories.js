const request = path => window.lyraAdminRequest("/api/admin" + path);
const labels = {created: "Created", reinforced: "Reinforced", recalled: "Recalled"};
const date = value => new Date(value).toLocaleString();

function element(tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function link(text, href) {
  const node = element("a", text);
  node.href = href;
  return node;
}

export async function mountMemories() {
  const host = document.getElementById("memories-page");
  const params = new URLSearchParams(location.search);
  const memoryId = params.get("id");
  host.replaceChildren();
  if (memoryId) {
    const memory = await request("/memories/" + encodeURIComponent(memoryId));
    host.append(link("All memories", "/admin/memories/"), element("h2", memory.content),
      element("p", `${memory.kind} · saved ${date(memory.created_at)}`),
      element("h3", "Original evidence"), element("blockquote", memory.evidence),
      element("h3", "Associated turns"),
      element("p", "Created identifies the first save. Reinforced means the turn selected the same memory again. Recalled means the memory was supplied to the agents in that turn."));
    const list = element("ul", "");
    list.className = "configuration-list memory-turn-list";
    const more = element("button", "Load more turns");
    let offset = 0;
    function append(page) {
      for (const turn of page.turns) {
        const item = element("li", "");
        const url = "/admin/flow/?id=" + encodeURIComponent(turn.flow_id) + "&run=" + encodeURIComponent(turn.run_id);
        const input = typeof turn.input === "string" ? turn.input : turn.input?.message ?? JSON.stringify(turn.input);
        item.append(link(`${labels[turn.relation]} · ${date(turn.started_at)}`, url),
          element("p", input || "Input unavailable"));
        if (turn.evidence) item.append(element("blockquote", turn.evidence));
        const details = element("details", "");
        details.append(element("summary", "Turn identifiers"), element("p", "Run " + turn.run_id),
          element("p", "Turn " + (turn.turn_id ?? "unavailable")),
          element("p", "Conversation " + (turn.conversation_id ?? "unavailable")),
          element("p", "Node " + (turn.node || "Flow")));
        item.append(details);
        list.append(item);
      }
      offset += page.turns.length;
      more.hidden = !page.hasMore;
    }
    append(memory);
    host.append(list, more);
    if (!memory.turns.length) host.append(element("p", "No recorded turn links are available for this memory."));
    more.onclick = async () => {
      more.disabled = true;
      try { append(await request(`/memories/${encodeURIComponent(memoryId)}?offset=${offset}`)); }
      catch (error) { host.append(element("p", error.message)); }
      finally { more.disabled = false; }
    };
    return;
  }
  host.innerHTML = `<form class="memory-search"><label for="memory-query">Search memories</label>
    <input id="memory-query" name="query" maxlength="2000"><button>Search</button></form>
    <ul class="configuration-list memory-turn-list"></ul><p role="status"></p>
    <button type="button" id="more-memories">Load more memories</button>`;
  const input = host.querySelector("input");
  input.value = params.get("query") ?? "";
  const list = host.querySelector("ul");
  const status = host.querySelector('[role="status"]');
  const more = host.querySelector("#more-memories");
  let offset = 0;
  let query = input.value;
  let loading = false;
  async function load(reset = false) {
    if (loading) return;
    loading = true;
    more.disabled = true;
    if (reset) { offset = 0; query = input.value; list.replaceChildren(); }
    try {
      const result = await request(`/memories?query=${encodeURIComponent(query)}&offset=${offset}`);
      for (const memory of result.memories) {
        const item = element("li", "");
        item.append(link(memory.content, "/admin/memories/?id=" + encodeURIComponent(memory.id)),
          element("p", `${memory.kind} · saved ${date(memory.created_at)}`));
        list.append(item);
      }
      offset += result.memories.length;
      status.textContent = offset ? `${offset} memories shown` : "No memories found.";
      more.hidden = !result.hasMore;
    } catch (error) { status.textContent = error.message; }
    finally { more.disabled = false; loading = false; }
  }
  host.querySelector("form").onsubmit = event => { event.preventDefault(); void load(true); };
  more.onclick = () => { void load(); };
  await load();
}
