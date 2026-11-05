export function mountDesignerChat(host, agent) {
  host.hidden = false;
  host.innerHTML = `<h3>Designer chat</h3>
    <div role="log" aria-label="Conversation"></div>
    <form class="configuration-form">
      <label for="designer-message">Ask the designer to inspect or edit your workspace</label>
      <textarea id="designer-message" rows="3" maxlength="8000" required></textarea>
      <button type="submit">Send</button>
    </form>
    <p role="status"></p>`;
  const log = host.querySelector('[role="log"]');
  const status = host.querySelector('[role="status"]');
  const message = host.querySelector("textarea");
  const send = host.querySelector("button");
  const history = [];

  function append(speaker, content) {
    const label = document.createElement("strong");
    label.textContent = speaker;
    const text = document.createElement("p");
    text.textContent = content;
    log.append(label, text);
  }

  host.querySelector("form").addEventListener("submit", async event => {
    event.preventDefault();
    const input = message.value.trim();
    if (!input || send.disabled) return;
    send.disabled = message.disabled = true;
    append("You", input);
    status.textContent = "Working…";
    try {
      const result = await window.lyraAdminRequest("/api/admin/agents/" + agent.id + "/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input, chatContext: { message: input, history: history.slice(-12) },
                              inputSelection: { source: "chat", mode: "all" } }),
      });
      const reply = typeof result.output === "string" ? result.output : JSON.stringify(result.output, null, 2);
      append(agent.name, reply);
      history.push({ role: "user", content: input }, { role: "assistant", content: reply.slice(0, 8000) });
      if (result.operations?.length) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "API calls (" + result.operations.length + ")";
        details.append(summary);
        for (const call of result.operations) {
          const line = document.createElement("p");
          line.textContent = call.status + " · " + call.operation.replace(/\{(\w+)\}/g, (_, key) => call.path[key]);
          details.append(line);
        }
        log.append(details);
      }
      message.value = "";
      status.textContent = "";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      send.disabled = message.disabled = false;
      message.focus();
    }
  });
}
