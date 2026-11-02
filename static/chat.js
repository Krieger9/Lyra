const draft = document.querySelector("#draft");
const send = document.querySelector("#send");
const reset = document.querySelector("#reset");
const form = document.querySelector("#composer");
const messages = document.querySelector("#messages");
const welcome = document.querySelector("#welcome");
const statusLine = document.querySelector("#status");
let history = [];
let busy = false;
let client;
let scopes;
let redirectUri;
let account;
let needsSignIn = true;
const accountButton = document.querySelector("#account");
const signInDraftKey = "lyra.signInDraft";

async function initializeAuth() {
  const response = await fetch("/auth/config", { cache: "no-store" });
  const config = await response.json();
  if (!response.ok) throw new Error(config.detail);
  const origin = new URL(config.redirectUri).origin;
  if (location.origin !== origin) {
    location.replace(origin);
    return;
  }
  scopes = [config.scope];
  redirectUri = config.redirectUri;
  client = new msal.PublicClientApplication({
    auth: { clientId: config.clientId, authority: config.authority, redirectUri: config.redirectUri },
    cache: { cacheLocation: "sessionStorage" },
  });
  await client.initialize();
  const savedDraft = sessionStorage.getItem(signInDraftKey);
  if (savedDraft !== null) {
    draft.value = savedDraft;
    sessionStorage.removeItem(signInDraftKey);
  }
  let result;
  try {
    result = await client.handleRedirectPromise();
  } catch {
    accountButton.disabled = false;
    updateControls();
    throw new Error("Sign-in did not complete. Select Sign in to try again.");
  }
  account = result?.account ?? client.getActiveAccount();
  if (account) client.setActiveAccount(account);
  needsSignIn = !account;
  accountButton.disabled = false;
  accountButton.textContent = account ? "Sign out" : "Sign in";
  statusLine.textContent = needsSignIn ? "Sign in with Microsoft to send a message." :
    savedDraft ? "Signed in. Your draft is ready to send." : "";
  updateControls();
}

async function changeSignIn() {
  if (accountButton.disabled) return;
  accountButton.disabled = true;
  updateControls();
  try {
    if (needsSignIn) {
      sessionStorage.setItem(signInDraftKey, draft.value);
      statusLine.textContent = "Opening Microsoft sign-in…";
      await client.acquireTokenRedirect({ scopes, account, redirectStartPage: location.origin + "/" });
    } else {
      await client.logoutRedirect({ account, postLogoutRedirectUri: redirectUri });
    }
  } catch {
    sessionStorage.removeItem(signInDraftKey);
    statusLine.textContent = "Microsoft sign-in is unavailable. Please try again.";
    accountButton.disabled = false;
    updateControls();
  }
}
accountButton.addEventListener("click", changeSignIn);

initializeAuth().catch((error) => {
  statusLine.textContent = error.message || "Could not initialize Microsoft sign-in.";
});

function updateControls() {
  send.disabled = busy || accountButton.disabled || !draft.value.trim();
  const label = needsSignIn ? "Sign in to send" : "Send message";
  send.textContent = needsSignIn ? label : "↑";
  send.setAttribute("aria-label", label);
  reset.disabled = busy || (!history.length && !draft.value);
  draft.readOnly = busy;
}

function appendTurn(role, content) {
  const entry = document.createElement("article");
  entry.className = role;
  const name = document.createElement("strong");
  name.textContent = role === "user" ? "You" : "Lyra";
  const text = document.createElement("p");
  text.textContent = content;
  entry.append(name, text);
  messages.append(entry);
  entry.scrollIntoView({ block: "nearest" });
  return entry;
}

draft.addEventListener("input", updateControls);
draft.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!send.disabled) form.requestSubmit();
  }
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    draft.value = button.dataset.prompt;
    updateControls();
    draft.focus();
  });
});

reset.addEventListener("click", () => {
  history = [];
  messages.replaceChildren();
  welcome.hidden = false;
  draft.value = "";
  statusLine.textContent = "";
  updateControls();
  draft.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = draft.value.trim();
  if (!message || busy || accountButton.disabled) return;
  if (needsSignIn) {
    await changeSignIn();
    return;
  }
  busy = true;
  welcome.hidden = true;
  draft.value = "";
  updateControls();
  const pending = appendTurn("user", message);
  statusLine.textContent = "Lyra is thinking…";
  try {
    const token = await client.acquireTokenSilent({ scopes, account });
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + token.accessToken },
      body: JSON.stringify({ message, history }),
    });
    const data = await response.json();
    if (response.status === 401) needsSignIn = true;
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Lyra couldn’t process that message.");
    }
    appendTurn("assistant", data.reply);
    history.push({ role: "user", content: message }, { role: "assistant", content: data.reply.slice(0, 8000) });
    history = history.slice(-12);
    statusLine.textContent = "";
  } catch (error) {
    pending.remove();
    welcome.hidden = history.length > 0;
    draft.value = message;
    statusLine.textContent = error instanceof TypeError ? "Couldn’t reach Lyra. Please try again." : error.message;
    if (error instanceof msal.InteractionRequiredAuthError || needsSignIn) {
      needsSignIn = true;
      accountButton.textContent = "Sign in again";
      statusLine.textContent = "Sign in again to continue chatting.";
    }
  } finally {
    busy = false;
    updateControls();
    draft.focus();
  }
});
