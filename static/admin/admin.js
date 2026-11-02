const accountButton = document.querySelector("#account");
const statusLine = document.querySelector("#status");
let auth;
let account;

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
  document.querySelector("#model").textContent = home.chat_model;
  document.querySelector("#library").textContent = home.prompt_library;
  document.querySelector("#workspace").hidden = false;
  statusLine.textContent = "Your Lyra workspace at a glance.";
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
      await auth.client.acquireTokenRedirect({ scopes: auth.scopes, redirectStartPage: location.origin + "/admin/" });
    }
  } catch (error) {
    statusLine.textContent = error.message || "Microsoft sign-in is unavailable.";
    accountButton.disabled = false;
  }
});

initialize().catch(error => {
  statusLine.textContent = error.message || "Could not initialize Microsoft sign-in.";
});
