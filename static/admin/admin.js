const accountButton = document.querySelector("#account");
const statusLine = document.querySelector("#status");
let auth;
let account;

const sidebar = document.querySelector("#sidebar");
const toggle = document.querySelector("#nav-toggle");
const narrow = matchMedia("(max-width: 700px)");
const activeLink = [...sidebar.querySelectorAll("nav a")].find(link => link.pathname === location.pathname);
const page = activeLink ? activeLink.pathname.split("/")[2] : "home";
document.body.classList.toggle("prompt-workspace", page === "prompts");
if (activeLink) {
  activeLink.setAttribute("aria-current", "page");
  const title = activeLink.textContent.trim();
  document.querySelector("h1").textContent = title;
  document.title = title + " · Lyra";
}
for (const section of document.querySelectorAll("#workspace > section")) {
  section.hidden = section.id !== page + "-page";
}

function setExpanded(expanded) {
  sidebar.classList.toggle("expanded", expanded);
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.title = expanded ? "Collapse navigation" : "Expand navigation";
  toggle.setAttribute("aria-label", toggle.title);
}

function restoreNavigation() {
  setExpanded(!narrow.matches && localStorage.getItem("lyra.adminNav") !== "collapsed");
}
restoreNavigation();
narrow.addEventListener("change", restoreNavigation);
toggle.addEventListener("click", () => {
  const expanded = !sidebar.classList.contains("expanded");
  setExpanded(expanded);
  if (!narrow.matches) localStorage.setItem("lyra.adminNav", expanded ? "expanded" : "collapsed");
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && sidebar.classList.contains("expanded") && !sidebar.hidden) {
    setExpanded(false);
    if (!narrow.matches) localStorage.setItem("lyra.adminNav", "collapsed");
    toggle.focus();
  }
});
document.addEventListener("click", event => {
  if (narrow.matches && !sidebar.contains(event.target)) setExpanded(false);
});

async function loadHome() {
  const { accessToken } = await auth.client.acquireTokenSilent({ scopes: auth.scopes, account });
  const response = await fetch("/api/admin/home", {
    headers: { Authorization: "Bearer " + accessToken },
    cache: "no-store",
  });
  if (response.status === 401) {
    accountButton.textContent = "Sign in again";
    account = null;
    throw new Error("Your session has expired. Sign in again.");
  }
  if (response.status === 403) {
    throw new Error("Access denied. This workspace requires the Lyra admin permission and an assigned administrator role.");
  }
  if (!response.ok) throw new Error("Could not load the configuration. Refresh to try again.");
  const home = await response.json();
  window.lyraAssetVersion = home.ui_version;
  window.lyraPromptSetScope = account.homeAccountId + "." + home.prompt_library;
  const assetUrl = path => path + "?v=" + encodeURIComponent(home.ui_version);
  for (const field of document.querySelectorAll("[data-library]")) field.textContent = home.prompt_library;
  sidebar.hidden = false;
  document.querySelector("#workspace").hidden = false;
  const { initializeNavigation, prepareFlowView } = await import(assetUrl("/admin/navigation.js"));
  initializeNavigation();
  window.lyraAdminRequest = async (path, options = {}) => {
    const { accessToken } = await auth.client.acquireTokenSilent({ scopes: auth.scopes, account });
    const result = await fetch(path, {
      ...options,
      headers: { ...options.headers, Authorization: "Bearer " + accessToken },
      cache: "no-store",
    });
    if (!result.ok) {
      if (result.status === 401) throw new Error("Your session expired. Sign in again.");
      if (result.status === 403) throw new Error("Administrator access is required.");
      const body = await result.json().catch(() => ({}));
      const validation = Array.isArray(body.detail) && body.detail.map(error => error.msg).filter(Boolean).join(" ");
      throw new Error(typeof body.detail === "string" ? body.detail : validation || "Could not save or load configuration. Check your entries and try again.");
    }
    return result.json();
  };
  if (page === "home") {
    const { mountOverview } = await import(assetUrl("/admin/overview.js"));
    await mountOverview(document.querySelector("#home-page"));
  }
  if (page === "agents" || page === "prompts") {
    const { mountConfiguration } = await import(assetUrl("/admin/configuration.js"));
    await mountConfiguration(page);
  }
  let flowLookupError;
  if (page === "personalities") {
    const { mountPersonalities } = await import(assetUrl("/admin/personalities.js"));
    await mountPersonalities();
  }
  if (page === "continuity") {
    const { mountContinuity } = await import(assetUrl("/admin/continuity.js"));
    await mountContinuity();
  }
  if (page === "memories") {
    const { mountMemories } = await import(assetUrl("/admin/memories.js"));
    await mountMemories();
  }
  if (page === "flow") {
    const stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = assetUrl("/admin/flow-assets/editor.css");
    document.head.append(stylesheet);
    try {
      await prepareFlowView(account.homeAccountId, () => window.lyraAdminRequest("/api/admin/chat-flow"));
    } catch (error) {
      flowLookupError = "Could not select the chat flow. " + error.message;
    }
    await import(assetUrl("/admin/flow-assets/editor.js"));
  }
  if (["models", "model-groups", "protocols", "providers"].includes(page)) {
    const { mountCatalogEditor } = await import(assetUrl("/admin/providers.js"));
    await mountCatalogEditor(page);
  }
  const descriptions = {
    home: "Your Lyra workspace at a glance.",
    models: "Register the model identifiers accepted by your endpoints.",
    "model-groups": "Choose the models available to providers using this group.",
    protocols: "Choose a client adapter for each protocol.",
    providers: "Set an API base URI, protocol, and model group.",
    flow: "Drag from a right output port to an Agent’s left input port to connect them.",
    agents: "Define reusable agents using instructions and prompts from PromptStudio.",
    prompts: "Manage the versioned Lyra prompt library.",
    memories: "Browse saved memories and the turns that created, reinforced or recalled them.",
    personalities: "Write a personality’s prompt, goals and curated memories, then load it into a flow.",
    continuity: "Explore private identity, interests and memories. Each user has their own continuity across flows and models.",
  };
  statusLine.textContent = flowLookupError || descriptions[page];
}

async function initialize() {
  auth = await createLyraClient("admin");
  if (!auth) return;
  try {
    const result = await auth.client.handleRedirectPromise();
    account = result?.account ?? auth.client.getActiveAccount();
    if (!account) {
      statusLine.textContent = "Sign in with your administrator account to view the configuration.";
      return;
    }
    auth.client.setActiveAccount(account);
    accountButton.textContent = "Sign out";
    await loadHome();
  } catch (error) {
    if (error instanceof msal.InteractionRequiredAuthError) {
      account = null;
      accountButton.textContent = "Sign in";
      statusLine.textContent = "Sign in to request access to the admin workspace.";
    } else {
      statusLine.textContent = error.message || "Could not open the admin workspace.";
    }
  } finally {
    accountButton.disabled = false;
  }
}

accountButton.addEventListener("click", async () => {
  accountButton.disabled = true;
  try {
    if (account) {
      await auth.client.logoutRedirect({ account, postLogoutRedirectUri: auth.redirectUri });
    } else {
      await auth.client.acquireTokenRedirect({ scopes: auth.scopes, redirectStartPage: location.href });
    }
  } catch (error) {
    statusLine.textContent = error.message || "Microsoft sign-in is unavailable.";
    accountButton.disabled = false;
  }
});

initialize().catch(error => {
  statusLine.textContent = error.message || "Could not initialize Microsoft sign-in.";
});
