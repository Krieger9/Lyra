const accountButton = document.querySelector("#account");
const statusLine = document.querySelector("#status");
let auth;
let account;

const sidebar = document.querySelector("#sidebar");
const toggle = document.querySelector("#nav-toggle");
const narrow = matchMedia("(max-width: 700px)");
const activeLink = [...sidebar.querySelectorAll("nav a")].find(link => link.pathname === location.pathname);
const page = activeLink ? activeLink.pathname.split("/")[2] : "home";
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
  for (const field of document.querySelectorAll("[data-model]")) field.textContent = home.chat_model;
  for (const field of document.querySelectorAll("[data-library]")) field.textContent = home.prompt_library;
  sidebar.hidden = false;
  document.querySelector("#workspace").hidden = false;
  if (page === "flow") {
    const stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = "/admin/flow-assets/editor.css";
    document.head.append(stylesheet);
    await import("/admin/flow-assets/editor.js");
  }
  const descriptions = {
    home: "Your Lyra workspace at a glance.",
    providers: "The provider serving chat requests.",
    flow: "Every flow starts with API Input. Add items to build your draft.",
  };
  statusLine.textContent = descriptions[page];
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
