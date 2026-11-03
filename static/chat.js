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
const configureLink = document.querySelector("#configure");
const markdown = window.markdownit?.({ html: false, breaks: true }).disable("image");

function lockChat() {
  document.body.classList.add("chat-locked");
  configureLink.hidden = true;
}

async function initializeAuth() {
  const auth = await createLyraClient("chat");
  if (!auth) return;
  ({ client, scopes, redirectUri } = auth);
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
  if (account) {
    try {
      const token = await client.acquireTokenSilent({ scopes, account });
      const response = await fetch("/api/chat/session", {
        headers: { Authorization: "Bearer " + token.accessToken }, cache: "no-store",
      });
      if (response.ok) {
        const session = await response.json();
        configureLink.hidden = session.can_configure !== true;
        document.body.classList.remove("chat-locked");
        statusLine.textContent = savedDraft ? "Signed in. Your draft is ready to send." : "";
        updateControls();
        return;
      }
      if (response.status !== 401) {
        throw new Error(response.status === 403 ? "Your account does not have access to Lyra chat." :
          "Could not verify chat access. Refresh to try again.");
      }
    } catch (error) {
      if (!(error instanceof msal.InteractionRequiredAuthError)) throw error;
    }
  }
  needsSignIn = true;
  accountButton.textContent = "Sign in";
  // A failed or interrupted redirect should offer a retry instead of looping.
  if (savedDraft === null && !result) {
    await changeSignIn();
    return;
  }
  statusLine.textContent = "Sign-in did not complete. Select Sign in to try again.";
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
      lockChat();
      await client.logoutRedirect({ account, postLogoutRedirectUri: redirectUri });
    }
  } catch {
    sessionStorage.removeItem(signInDraftKey);
    statusLine.textContent = "Microsoft authentication is unavailable. Refresh to try again.";
    accountButton.disabled = false;
    updateControls();
  }
}
accountButton.addEventListener("click", changeSignIn);

initializeAuth().catch((error) => {
  statusLine.textContent = error.message || "Could not initialize Microsoft sign-in.";
});

function updateControls() {
  send.disabled = busy || needsSignIn || accountButton.disabled || !draft.value.trim();
  reset.disabled = busy || (!history.length && !draft.value);
  draft.readOnly = busy;
}

function appendTurn(role, content) {
  const entry = document.createElement("article");
  entry.className = role;
  const name = document.createElement("strong");
  name.textContent = role === "user" ? "You" : "Lyra";
  let text;
  if (typeof content !== "string") {
    text = document.createElement("pre");
    text.textContent = JSON.stringify(content, null, 2);
  } else if (role === "assistant" && markdown) {
    text = document.createElement("div");
    text.className = "markdown";
    text.innerHTML = markdown.render(content);
  } else {
    text = document.createElement("p");
    text.textContent = content;
  }
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
  const message = draft.value;
  if (!message.trim() || busy || accountButton.disabled) return;
  if (needsSignIn) {
    await changeSignIn();
    return;
  }
  busy = true;
  welcome.hidden = true;
  draft.value = "";
  updateControls();
  const pending = appendTurn("user", message);
  let inspectLink;
  statusLine.textContent = "Lyra is thinking…";
  try {
    const token = await client.acquireTokenSilent({ scopes, account });
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + token.accessToken },
      body: JSON.stringify({ message, history }),
    });
    const data = await response.json();
    const runId = response.headers.get("X-Lyra-Run-Id");
    const flowId = response.headers.get("X-Lyra-Flow-Id");
    if (runId && flowId && !configureLink.hidden) {
      inspectLink = document.createElement("a");
      inspectLink.href = "/admin/flow/?" + new URLSearchParams({ id: flowId, run: runId });
      inspectLink.textContent = "Inspect run";
    }
    if (response.status === 401) needsSignIn = true;
    if (response.status === 403) configureLink.hidden = true;
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Lyra couldn’t process that message.");
    }
    const reply = typeof data.reply === "string" ? data.reply : JSON.stringify(data.reply, null, 2);
    const entry = appendTurn("assistant", data.reply);
    if (inspectLink) entry.append(inspectLink);
    history.push({ role: "user", content: message });
    if (reply.trim()) history.push({ role: "assistant", content: reply.slice(0, 8000) });
    history = history.slice(-12);
    statusLine.textContent = "";
  } catch (error) {
    pending.remove();
    welcome.hidden = history.length > 0;
    draft.value = message;
    statusLine.textContent = error instanceof TypeError ? "Couldn’t reach Lyra. Please try again." : error.message;
    if (inspectLink) statusLine.append(" ", inspectLink);
    if (error instanceof msal.InteractionRequiredAuthError || needsSignIn) {
      needsSignIn = true;
      lockChat();
      accountButton.textContent = "Sign in";
      await changeSignIn();
    }
  } finally {
    busy = false;
    updateControls();
    draft.focus();
  }
});
