function flowLink(flow) {
  const link = document.createElement("a");
  link.href = "/admin/flow/?" + new URLSearchParams({id: flow.id});
  link.textContent = flow.name + " · Revision " + flow.revision;
  return link;
}

export async function mountOverview(host) {
  const button = host.querySelector("#overview-refresh");
  const status = host.querySelector("#overview-status");
  const data = host.querySelector("#overview-data");

  async function refresh() {
    if (button.disabled) return;
    button.disabled = true;
    status.textContent = "Loading workspace…";
    try {
      const overview = await window.lyraAdminRequest("/api/admin/overview");
      for (const cell of host.querySelectorAll("[data-count]")) {
        cell.textContent = overview.counts[cell.dataset.count].toLocaleString();
      }
      const flow = overview.activeFlow;
      const active = host.querySelector("#overview-active");
      const models = host.querySelector("#overview-models");
      models.replaceChildren();
      if (flow) {
        active.textContent = "Active chat flow · " + flow.name + " · Revision " + flow.revision;
        const heading = document.createElement("h3");
        heading.textContent = "Configured models";
        const list = document.createElement("dl");
        for (const assignment of flow.assignments) {
          const step = document.createElement("dt");
          step.textContent = assignment.agent && assignment.agent !== assignment.step
            ? assignment.step + " · " + assignment.agent : assignment.step;
          const model = document.createElement("dd");
          model.textContent = [assignment.model, assignment.usesDefault ? "Server default" : assignment.provider]
            .filter(Boolean).join(" · ");
          list.append(step, model);
        }
        if (flow.assignments.length) models.append(heading, list);
        else models.textContent = "This flow has no connected model steps.";
      } else {
        active.textContent = "Built-in chat · No flow activated";
        models.textContent = "Chat model · " + overview.defaultModel;
      }
      host.querySelector("#overview-recent").replaceChildren(...overview.recentFlows.map(flow => {
        const item = document.createElement("li");
        item.append(flowLink(flow));
        return item;
      }));
      host.querySelector("#overview-empty").hidden = overview.recentFlows.length > 0;
      data.hidden = false;
      status.textContent = "Updated " + new Date().toLocaleTimeString();
    } catch (error) {
      status.textContent = (data.hidden ? "Could not load the overview. " : "Showing the last loaded overview. ") + error.message;
    } finally {
      button.disabled = false;
    }
  }

  button.addEventListener("click", refresh);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void refresh(); });
  await refresh();
}
