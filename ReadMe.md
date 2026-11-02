# Lyra

Lyra is a local chat application. A single FastAPI process serves the browser UI and chat API. The frontend uses HTML, CSS and JavaScript and runs without a build step.

## Foundation

- **AgentStudio owns the agentic workflow.** Each chat turn goes through `AgentStudio.run`, which renders the prompt, calls the configured Ollama model and records the run in PostgreSQL. [AgentStudio](../Infrastructure/AgentStudio/README.md)
- **PromptStudio owns prompt management and the template library.** Lyra stores the `chat` template in the `lyra` library in PostgreSQL. On first launch it seeds the template if absent. Each turn renders the latest version through AgentStudio’s `PromptStudioAdapter`. [PromptStudio](../Infrastructure/PromptStudio/README.md)

## Run locally

Keep the two infrastructure checkouts at the relative paths in `requirements.txt`. From the Lyra repository root, run the following in PowerShell with Python 3.10+, npm and Ollama installed.

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
npm ci
ollama list
```

### Configure PostgreSQL

Create a database named `lyra` and set `DATABASE_URL` in `.env` to its PostgreSQL SQLAlchemy URL. The application user needs permission to create schemas and tables at initial setup. PromptStudio owns `prompt_studio.prompt_versions`, `prompt_groups`, and `prompt_group_members`. AgentStudio owns `agent_studio.traces`.

For local development, the included Compose project provides this database on `127.0.0.1:55432`. On a fresh checkout, generate a password with `python -c "import secrets; print(secrets.token_urlsafe(36))"` and use it for both database settings shown in `.env.example`. Start the database manually with `docker compose up -d --wait postgres` before launching FastAPI. Stop it with `docker compose stop postgres`. It uses a named volume and has automatic restart disabled.

The deployment target is **Azure Database for PostgreSQL Flexible Server**. Provision the `lyra` database there and supply its TLS-enabled connection URL through the deployment environment. Local and hosted instances use the same two schemas. The Compose password setting is only used to initialize the local container.

For an existing installation, stop the old application and import both source stores **before the first PostgreSQL-backed startup**:

```powershell
.\.venv\Scripts\python migrate_storage.py .lyra/prompts.db .lyra/traces
.\.venv\Scripts\python migrate_storage.py ../Infrastructure/AgentStudio/agent-studio-prompts.db ../Infrastructure/AgentStudio/traces
```

Each command imports one source pair in a transaction. It preserves version numbers, group membership, timestamps, trace IDs and original payloads. Repeating an import verifies matching records. A conflicting record aborts that transaction. Keep the source files as backups.

### Configure Microsoft Entra sign-in

Lyra uses your existing frontend SPA and backend API registrations. On a new checkout, copy `.env.example` to `.env`. Verify the tenant and client IDs. Set the SPA redirect URI to `http://localhost:8765/auth/callback` for local development. For deployment, register the public HTTPS callback and update `ENTRA_REDIRECT_URI`. Restart FastAPI after changing settings.

MSAL Browser signs the user in with PKCE and requests `api://19a2bf26-c383-4fb6-b131-83b7b1af5d4e/access-as-user`. Each chat request carries the resulting bearer access token. FastAPI verifies the signature, tenant, issuer, audience, expiry, calling frontend, delegated scope and user role. This flow requires no client secret or session signing key. [Microsoft delegated permissions guide](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-access-web-apis)

| Operation | Delegated scope | Backend app role |
| --- | --- | --- |
| Chat | `access-as-user` | `User.Chat` or `User.Admin` |
| [Configuration home](http://localhost:8765/admin/) | `access-as-admin` | `User.Admin` |

Choose **Configure Lyra** from chat to open the read-only admin home. It uses the existing SPA registration and callback, requests the backend scope named by `ENTRA_ADMIN_SCOPE`, and returns to `/admin/` after sign-in. The frontend registration needs delegated permission to that backend scope. The home displays the current model and prompt library after FastAPI checks the access token. `ENTRA_ADMIN_ROLES` defaults to `User.Admin`. Chat continues to request `ENTRA_CHAT_SCOPE`.

An Azure-hosted backend can use managed identity for its own calls to downstream Azure resources. [Microsoft managed identity overview](https://learn.microsoft.com/en-us/azure/app-service/overview-managed-identity)

### Start the server

The default model is `qwen2.5-14b-q5:latest`, available on the original development machine. On another machine, set `OLLAMA_MODEL` to an exact name from `ollama list`. For example, to use `llama3.2`, run `ollama pull llama3.2` and set `$env:OLLAMA_MODEL = "llama3.2"`. Lyra connects to `http://127.0.0.1:11434` by default. Set `OLLAMA_HOST` to use another Ollama server.

With PostgreSQL already running, use the Windows PowerShell scripts from the Lyra checkout:

```powershell
.\start.ps1
.\stop.ps1
```

`start.ps1` starts FastAPI in the background and waits for the chat UI. It reuses a running local Ollama server, or starts one if the configured loopback endpoint is offline. A remote `OLLAMA_HOST` is managed separately. `stop.ps1` stops only processes these scripts started. Both commands can be run repeatedly. Logs are in `.lyra/run/`. Manage PostgreSQL separately using the commands above.

Open [Lyra Chat](http://localhost:8765/). Signed-out visitors go directly to Microsoft sign-in. After FastAPI verifies the chat access token, users with an admin role see **Configure Lyra**. Enter sends a message and Shift + Enter adds a line. **New chat** or a page refresh clears the tab’s conversation.

## Chat API

`POST /api/chat` accepts a message of up to 8,000 characters and optional history in chronological order. The UI includes the most recent six completed exchanges, with up to 8,000 characters per turn. FastAPI passes the latest message as AgentStudio’s `request` and the earlier turns as the template parameter `history`.

The [interactive API docs](http://localhost:8765/docs) accept a delegated API access token through **Authorize**. Missing or invalid credentials return HTTP 401. A valid token with insufficient permissions returns HTTP 403.

```json
{"message":"What color did I pick?","history":[{"role":"user","content":"I picked turquoise."},{"role":"assistant","content":"Understood."}]}
```

A successful response has the shape `{"reply":"Turquoise."}`. Invalid input returns HTTP 422. A failed run returns 503, and an empty model response returns 502. The UI restores the draft after a failed send.

## Templates and traces

To publish a revision, connect PromptStudio to the same PostgreSQL database, select `PromptLibrary(storage, name="lyra")` and call `library.add("chat", new_template)`. The next request uses that revision. The template receives `request` and a list of `history` entries with `role` and `content`.

Each trace row contains the run UUID, start and finish timestamps, status and the full JSONB payload. The payload retains the input, rendered prompt and reply after the tab is cleared. Restrict database access accordingly.
