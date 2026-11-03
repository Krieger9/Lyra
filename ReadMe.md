# Lyra

Lyra is a local chat application. A single FastAPI process serves the browser UI and chat API. Vite bundles the React Flow editor for the admin Flow View. Chat and the rest of the admin workspace use plain HTML, CSS and JavaScript.

## Foundation

- **AgentStudio runs individual agent calls.** Built-in chat uses the configured Ollama model. When a flow is activated for chat, Lyra executes the saved graph and uses AgentStudio for each agent node. [AgentStudio](../Infrastructure/AgentStudio/README.md)
- **PromptStudio owns prompt versions, groups and rendering.** Built-in chat renders the latest `chat` template in the `lyra` library through AgentStudio’s `PromptStudioAdapter`. [PromptStudio](../Infrastructure/PromptStudio/README.md)

Lyra composes the library and runner at startup. The admin API and MCP inspector access the library directly. Publish edits with `PromptLibrary.add` and seed defaults with `ensure_initial`, which preserves version history across concurrent startups. A saved agent pins the root instruction and prompt versions. If either template includes other prompts, those dependencies resolve to their latest versions. PromptStudio supports pinning transitive dependencies with a group. Lyra’s agent editor currently stores individual version references.

AgentStudio owns the Ollama call and client lifecycle. Lyra’s metered adapter supplies HTTP event hooks for the flow debugger. Lyra also has HTTP clients for OpenAI-compatible and Claude endpoints so the debugger can capture wire events and the operator can bind credentials to exact API bases.

## Run locally

Keep the two infrastructure checkouts at the relative paths in `requirements.txt`. From the Lyra repository root, run the following in PowerShell with Python 3.10+ and npm installed. Install Ollama if you want to serve local models.

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
npm ci
npm run build
ollama list
```

### Configure PostgreSQL

Create a database named `lyra` and set `DATABASE_URL` in `.env` to its PostgreSQL SQLAlchemy URL. The application user needs permission to create schemas and tables at initial setup. PromptStudio owns `prompt_studio.prompt_versions`, `prompt_groups`, and `prompt_group_members`. AgentStudio owns `agent_studio.traces`. Lyra's configuration adapter stores reusable definitions in `agent_studio.agent_definitions`, with foreign keys to PromptStudio versions.

For local development, the included Compose project provides this database on `127.0.0.1:55432`. On a fresh checkout, generate a password with `python -c "import secrets; print(secrets.token_urlsafe(36))"` and use it for both database settings shown in `.env.example`. Start the database manually with `docker compose up -d --wait postgres` before launching FastAPI. Stop it with `docker compose stop postgres`. It uses a named volume and has automatic restart disabled.

Lyra serializes database setup across startup workers. After the two Studio libraries initialize their tables, Lyra applies pending revisions to its agent, catalog, flow, and run tables. It records revisions in `agent_studio.lyra_schema_migrations`. The first upgrade adopts existing tables, preserves pinned prompts and provider assignments, and imports model names from legacy groups. Later startups read the revision history and skip completed work. A failed upgrade rolls back all pending Lyra revisions. Correct the cause and restart to retry.

For future schema changes, append a numbered revision to `schema_migrations.py`, update the SQLAlchemy table declarations, and test the upgrade against the preceding layout. Keep published revisions fixed. To inspect an installation, run `SELECT revision, applied_at FROM agent_studio.lyra_schema_migrations ORDER BY revision`.

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

Choose **Configure Lyra** from chat to open the read-only admin home. It uses the existing SPA registration and callback, requests the backend scope named by `ENTRA_ADMIN_SCOPE`, and returns to the requested admin page after sign-in. The frontend registration needs delegated permission to that backend scope. After FastAPI checks access, the left navigation opens [Providers](http://localhost:8765/admin/providers/) and [Flow View](http://localhost:8765/admin/flow/). The panel collapses to an icon rail and slides over the page on smaller screens. Select the Lyra brand to return to the configuration home. `ENTRA_ADMIN_ROLES` defaults to `User.Admin`. Chat continues to request `ENTRA_CHAT_SCOPE`.

Register the model identifiers served by your endpoints in **Models**. In **Model groups**, select the models to offer together. In **Protocols**, select the installed client adapter for a named protocol. Lyra includes OpenAI Chat Completions, Claude Messages and Ollama native. A provider groups an API base URI, a protocol and a model group. An agent selects a provider and one model from that group. Create and edit links beside selectors preserve the calling draft and assign the saved item on return.

For example, an Ollama server can use OpenAI Chat Completions at `http://localhost:11434/v1` or Ollama native at `http://localhost:11434`. For hosted APIs, use `https://api.openai.com/v1` with `OPENAI_API_KEY` or `https://api.anthropic.com/v1` with `ANTHROPIC_API_KEY`. For a custom authenticated endpoint, set `LYRA_PROVIDER_KEY_ENV` to a JSON object mapping the exact API base to a server-side environment variable name. Keys are resolved when requests run. Add future client implementations to `ADAPTERS` in `model_adapter.py` to expose them in the protocol editor.

**Test agent**, flow runs and activated chat flows use the selected provider and model. **Use default chat model** uses the existing environment configuration. Activating a flow for chat captures the provider URI, adapter and model. Activate again to pick up later changes.

An Azure-hosted backend can use managed identity for its own calls to downstream Azure resources. [Microsoft managed identity overview](https://learn.microsoft.com/en-us/azure/app-service/overview-managed-identity)

### Start the server

After editing the TypeScript or CSS in `flow-editor/`, run `npm run build` and reload Flow View. Use Node 22.12+ for the frontend build. Each draft starts with one permanent **API Input** for the incoming request. Choose **Agent** or **Generic item** under **Item type**, then select **Add to canvas**. An Agent starts as an unconfigured placeholder with a validation error. Select it and choose a saved definition or follow **Create agent** in its properties panel. Assigning a definition supplies its name, instructions and prompt and clears the error. Each flow item has its own assignment. New items start disconnected. Drag from the right port of API Input or an Agent to another Agent’s left port. You can also select an Agent and use its inspector to connect or disconnect individual input sources.

Use **Agents** in the left navigation to list, create and edit reusable definitions. Each definition has a name, system instructions and an input configuration. **Prompt** defaults to **Raw user input**, meaning incoming flow text is passed through directly. Select a versioned PromptStudio template to format that input instead. The API represents raw input as `prompt: null`.

Under **Output**, select **Structured object** and add required or optional fields. For **User Input Classifier**, use a required single-choice field named `category` with `conversation` and `intelligence` on separate lines, and a required text field named `explanation` with a description asking for the reason. Save the definition. In Flow View, select the agent and open **Test agent** to run a sample against the saved definition and inspect the validated output.

Fields can also be objects or arrays. For a transcript, add an array field named `history`, set its item type to **Object**, and add a required `role` choice and a required `content` text field. Give `role` the choices `user` and `assistant`. An empty array represents no prior turns. The agent prompt and the runtime validator use the same definition. If the second turn has an invalid role, the run identifies `history[1].role`.

A JSON node can copy the array with `{{ result.history }}`, a turn with `{{ result.history[0] }}`, or its text with `{{ result.history[0].content }}` when the incoming wire is named `result`. An indexed mapping requires that element to exist.

### Select chat input for a flow agent

In Flow View, choose **Agent input** on the agent node. For chat selections, **Chat source** controls where the message and prior turns come from.

| Selection | What the agent receives |
| --- | --- |
| Whole wire value (default) | The incoming string, or the entire incoming JSON value serialized as text. |
| Current message only | The current message, rendered with the agent’s optional prompt template. |
| Current message + recent turns | The last 1–12 prior messages in chronological order, followed by the rendered current message. |
| Current message + all available turns | Every supplied prior message in chronological order, followed by the rendered current message. |

Choose **Original chat request** when a routing node forwards a classification or another object. The wire still determines whether the agent runs. The current message and selected history come from the chat request that started this run, regardless of the API Input format. The agent’s prompt template can also use the routed value, for example `{{ wire.request.category }}` alongside `{{ request }}`.

Choose **Incoming wire** to read a chat request supplied by an upstream node. A plain string supplies a current message with empty history. To put the original chat request on outgoing wires, choose **API Input → Input format → Chat request with history**. A turn is one prior user or assistant message. Selected turns keep their roles at the provider.

Use **Test agent** to try the selection or **Run flow** to supply sample prior turns. Debug and synchronous run APIs accept `chatContext` separately from the graph input, for example `{"input":"Which color?","chatContext":{"message":"Which color?","history":[{"role":"user","content":"I picked turquoise."}]}}`. With the chat API Input format, `input` itself can supply the original chat request. Run View shows the wire value, the selected source, the prior turn count and the provider request.

On startup Lyra seeds `Instructions.default` with `You are a helpful agent` if its first version is absent. This is a normal PromptStudio entry, and edits publish new versions. New agents default to its latest version. Agents retain their selected instruction and template versions until explicitly edited. Requests that omit instructions use the latest default, and requests that omit the prompt use raw input. The startup schema update allows existing definitions to use raw input while retaining their template references.

The editor previews selected library text and links to **PromptStudio** to create new prompts or publish revisions. Saving a prompt opened from an agent field returns to that agent form with the new version selected in that field. Saving from the standalone PromptStudio list returns to the list. Creating or editing an agent from a flow item returns to that flow and assigns the saved definition to the originating item.

Breadcrumbs show the context across admin pages. Use them or the Back and Forward controls to navigate. Unsaved forms and flow layouts, including wiring and canvas position, are restored from browser session storage in the current tab. Saved definitions and prompt versions persist in PostgreSQL.

Every flow also includes a permanent **API Response**. Connect one Agent's right output port to its left input port to return its text or structured object as the response body. A successful run returns HTTP 200, with an empty body when API Response is unconnected. Failed runs return an error status. Existing browser drafts gain this response node automatically.

Choose **Switch** under **Item type** to route an input. Connect an Agent or JSON output to the Switch, then select **Add check** in its properties. Text fields support **is empty** and **is not empty**. Missing, null and whitespace-only text count as empty. Choice fields offer **equals** and **does not equal**, with values from the connected classifier, including choices copied through JSON mappings. A missing choice matches neither comparison.

For example, combine the request text and classifier output in a JSON node. Give the Switch a first path named **Incomplete** with **Any check** on an empty text or reason field. Give the next path an equality check on the classifier's category. Connect each named port and **Otherwise** to its destination. The first matching path forwards the original input. Renaming or moving a path preserves its wires. Run View records the selected path and skipped nodes.

Outputs from mutually exclusive paths can connect to the same API Response. Exactly one connected response wire must be active on each run. Other nodes require all their inputs, so a JSON node with an inactive input is skipped. A removed choice or incompatible field must be repaired before the flow can run or be activated for chat.

Choose **JSON** under Item type to combine inputs into an object. In **Inputs**, enter a **Wire name** and choose **Apply name**. Names like `request` or `classification` are local to the receiving node. Use letters, digits and underscores, starting with a letter or underscore. Wires display their names on the canvas. Select a wire or inspect its input or mapping row for the full connection ID.

For a structured classifier, leave both wires **As received**. Choose **Source field** in a mapping row or enter the following object template. Agents that emit JSON as text can still use **Read as JSON**.

```json
{"Request":{"Category":"{{ classification.category }}","Text":"{{ request }}"}}
```

Quoted whole-value expressions resolve to the original JSON type. A bare name copies the whole wire value. A dot reads an object field. Use `items[0]` for indices or bracketed JSON strings for keys containing punctuation. Bindings in the template and field mapping rows are two views of the same ID-based rule. Renaming a wire updates its displayed references. Rewiring retains its ID. Disconnecting creates a missing reference to repair in the mapping row. To store a literal whole-value expression in the template, prefix it with an escaped backslash, for example `"\\{{ request }}"`.

**Preview with sample inputs** applies the wire interpretation and evaluates the same rules as a saved run. The sample format describes what the upstream node supplies. For structured classifier output, use a JSON sample such as `{"category":"conversation","explanation":"The input is a greeting."}`. Previewing requires admin access and does not call the model.

- In the row editor, `.value` takes the whole interpreted wire value and `.value/category` reads a field. With **As received**, a string that looks like JSON stays a string. `.text` explicitly requests text, displayed inline as `text(request)`. Structured values become compact JSON text with sorted object keys.
- Existing `.json` and `.json/path` rules retain their parsing behavior and appear inline as `json(request)` or `json(request).field`. Arrays, objects, booleans, numbers and null retain their types.
- Paths use slash-separated keys and zero-based array indices, such as `/messages/0/content`. Use `~1` for a slash in a key and `~0` for a tilde. Destination fields and indices must exist in the base. Each mapping owns one destination, so two rules cannot write to the same field or its parent and child.
- Runs validate connected wires, read expressions and destinations before calling agents. Missing values and invalid JSON input produce HTTP 422 when that input is read. Each run starts with a fresh copy of the base. Nodes support up to 64 mappings and an assembled object up to 256 KiB.

The run API accepts text, an object or an array in the `input` field. JSON nodes can feed other JSON nodes directly. API Response sends a mapped object as `application/json`. An Agent receives compact JSON text when its input comes from an object. Session values travel through the current run. Cross-run session storage is a separate capability.

Enter a **Flow name** and select **Save flow** to persist the graph, Agent identities, canvas viewport and panel size in `agent_studio.flows`. Use **Saved flows** and **Open flow** to restore a design, or **New flow** to start another. Updates use a revision check to protect against stale edits. Unfinished configurations can be saved as drafts.

Flow management and execution use the existing admin scope and role. `GET/POST /api/admin/flows` lists or creates flows. `GET/PUT /api/admin/flows/{id}` opens or updates one. `POST /api/admin/flows/{id}/run` accepts `{"input":"incoming text"}`. Agent calls use AgentStudio, render versioned instructions and optional templates through PromptStudio, and record traces. Each connected Agent currently requires exactly one incoming source. Connected unconfigured agents, cycles and unsupported joins return HTTP 422 before any model call. Model failures return 503 and execution timeouts return 504. Text responses preserve whitespace. Structured responses use `application/json`. Invalid model output fails at the producing agent with HTTP 502.

All definition and prompt APIs require `ENTRA_ADMIN_SCOPE` and `User.Admin`. Updates to an agent carry its revision and return HTTP 409 if another editor has saved a newer revision. Prompt edits publish new versions, and existing agents retain their selected versions until explicitly updated.

The default model is `qwen2.5-14b-q5:latest`, available on the original development machine. On another machine, set `OLLAMA_MODEL` to an exact name from `ollama list`. For example, to use `llama3.2`, run `ollama pull llama3.2` and set `$env:OLLAMA_MODEL = "llama3.2"`. Lyra connects to `http://127.0.0.1:11434` by default. Set `OLLAMA_HOST` to use another Ollama server.

With PostgreSQL already running, use the Windows PowerShell scripts from the Lyra checkout:

```powershell
.\start.ps1
.\stop.ps1
```

`start.ps1` starts FastAPI in the background and waits for the chat UI. It reuses a running local Ollama server, or starts one when installed if the configured loopback endpoint is offline. Use `.\start.ps1 -SkipOllama` for a workspace using external providers. A remote `OLLAMA_HOST` is managed separately. `stop.ps1` stops only processes these scripts started. Both commands can be run repeatedly. Logs are in `.lyra/run/`. Manage PostgreSQL separately using the commands above.

Open [Lyra Chat](http://localhost:8765/). Signed-out visitors go directly to Microsoft sign-in. After FastAPI verifies the chat access token, users with an admin role see **Configure Lyra**. Enter sends a message and Shift + Enter adds a line. **New chat** or a page refresh clears the tab’s conversation.

## Run and inspect a saved flow

Open a saved flow and choose **Run flow** (or **Save and run** for a draft). Enter raw input and run the displayed revision. The design stays in its editor while a read-only diagram shows the selected execution. Select a node to see the values on its incoming wires, its output, elapsed times and detailed events. The recent-runs selector includes API, debugger and chat executions. **Export run JSON** includes the captured graph and agent definitions.

Under **Chat connection**, **Use this revision in Lyra chat** pins the graph and resolved agent definitions for that library. Later design edits apply to debug runs when saved. To publish changed agents on the same flow revision, disconnect and activate again. Active chat sends the API Input format configured in the pinned revision and displays the API Response body, including structured JSON. Admins can follow **Inspect run** from chat. Disconnecting restores the built-in chat prompt and its history input.

The persisted `FlowSnapshot` contains the flow ID, name, revision, graph, and resolved agents keyed by agent ID. Each selected provider contributes its endpoint, protocol adapter, and permitted models. Agents using the default model resolve it from the running application. At execution, PromptStudio renders the pinned root versions and resolves ungrouped includes to their latest versions. Provider credentials are looked up for the saved API base when the call runs. Historical run inspection reads the original stored JSON. The Python chat test and TypeScript editor tests share `tests/fixtures/router-snapshot.json`, captured from a synthetic PostgreSQL run before the snapshot model was introduced.

### Execution state and integration

| Boundary | Responsibility |
|---|---|
| Agent store and flow graph | Persist design-time definitions, validate the graph, resolve agent revisions for a snapshot. |
| Flow execution | Plan dependencies once, dispatch nodes, validate outputs and return the response. Chat, synchronous API calls and background debugger runs share this executor. |
| Agent and model adapters | Render versioned templates, insert the default schema, correlate AgentStudio traces and record model requests, responses and available usage. The Ollama adapter captures the actual provider JSON bodies and timing fields. |
| Run store | Keep the activated chat snapshot, per-run snapshots and sequenced execution events in PostgreSQL. Each event carries a run-relative time and node ID when applicable. Record the operands and outcomes of schema checks and the value written by each mapping. |
| Admin REST, Flow View and MCP | Inspect the same evidence. Inspecting a recorded execution does not call the model again. |

`POST /api/admin/flows/{id}/runs` takes `input` and the expected `revision` and returns HTTP 202 with a run ID. Use `GET /api/admin/runs?flow_id=…` for history and `GET /api/admin/runs/{id}?after=0&limit=100` for ordered events. Fetch subsequent pages using the last sequence number and `include_snapshot=false`. Synchronous flow responses include `X-Lyra-Run-Id`. The run record distinguishes a successful model call from an invalid agent output. A terminated worker's unfinished run is reported as interrupted after the timeout and grace period.

**MCP export.** Connect a Streamable HTTP client to `http://localhost:8765/mcp` with an Entra bearer token for the Lyra backend, delegated admin scope and assigned admin role. The Lyra frontend client ID is accepted. To use a separately registered client with delegated admin permission, add its client ID to `ENTRA_MCP_CLIENT_IDS` and restart Lyra. That allowlist applies only to MCP. The client supplies its access token through its authorization configuration.

- `list_flow_runs` lists recent runs, optionally filtered by flow ID, with offset and limit.
- `inspect_flow_run` exports the graph, agents, result and events as structured data. Use `after` and `limit` to page through larger runs. These tools are read-only. An agent assisting with a design can inspect actual values and propose a change.

Run evidence includes original messages, prompt text and model output. It persists in the local database until removed by an operator. Debug runs execute in the serving process. After a process interruption the available evidence remains inspectable.

## Chat API

`POST /api/chat` accepts a message of up to 8,000 characters and optional history in chronological order. The UI includes up to 12 prior messages, with up to 8,000 characters per turn. Built-in chat passes the latest message as AgentStudio’s `request` and the earlier turns as the template parameter `history`.

The [interactive API docs](http://localhost:8765/docs) accept a delegated API access token through **Authorize**. Missing or invalid credentials return HTTP 401. A valid token with insufficient permissions returns HTTP 403.

```json
{"message":"What color did I pick?","history":[{"role":"user","content":"I picked turquoise."},{"role":"assistant","content":"Understood."}]}
```

A successful response has the shape `{"reply":"Turquoise."}`. Invalid input returns HTTP 422. A failed run returns 503, and an empty model response returns 502. The UI restores the draft after a failed send.

## Templates and traces

To publish a revision, connect PromptStudio to the same PostgreSQL database, select `PromptLibrary(storage, name="lyra")` and call `library.add("chat", new_template)`. The next request uses that revision. The template receives `request` and a list of `history` entries with `role` and `content`.

Each trace row contains the run UUID, start and finish timestamps, status and the full JSONB payload. The payload retains the input, rendered prompt and reply after the tab is cleared. Restrict database access accordingly.

## Test a change across the three checkouts

From Lyra, use the development environment containing the editable Studio packages. Install the Studio test extra for pytest, run `npm ci`, and use Node 22.18 or later for the TypeScript editor tests. Set `LYRA_TEST_DATABASE_URL` in the environment or local `.env` to a PostgreSQL test database whose user can create schemas. Tests create and remove isolated schemas. Full verification requires that database and stops at the first failed stage.

```powershell
.\.venv\Scripts\python -m pip install -e "../Infrastructure/PromptStudio[test]"
.\.venv\Scripts\python scripts/verify.py
```
