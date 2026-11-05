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

For local development, the included Compose project provides this database on `127.0.0.1:55432`. On a fresh checkout, generate a password with `python -c "import secrets; print(secrets.token_urlsafe(36))"` and use it for both database settings shown in `.env.example`. Start the database manually with `docker compose up -d --build --wait postgres` before launching FastAPI. Stop it with `docker compose stop postgres`. It uses a named volume and has automatic restart disabled.

The PostgreSQL image builds pgvector 0.8.7 on the existing PostgreSQL 18 Alpine base. Existing local installations must rebuild and recreate the database container with the command above before starting the updated app. The migration requires the `vector` extension in `public`, even when memory capture is disabled. On separately managed databases, install pgvector and have a database administrator run `CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public` before the application upgrade.

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

### Share a JSON object with State

Add a **State** element, then open **State object** above the canvas. Add named properties with JSON defaults, such as `summary` with `""` or `decisions` with `[]`. Save the Flow to save the declarations. State placements with the same key and scope access the same runtime object. Connect accesses in execution order whenever either placement writes.

In the State inspector, **Map input fields** copies whole incoming values or selected fields to declared properties. For example, select the incoming wire, choose `.value/answer`, and map it to `summary`. **Generate a property with an agent** runs a saved agent with the current State and all inputs keyed by wire name, then assigns its result to one property. **Map a structured agent result** requires a saved agent with an object output contract. Each mapping selects a field from that validated result. **Read**, **Update fields**, and **Replace object** also remain available.

Choose **Pass through input** to forward the original text or object after updating State. With several wires, select one to forward or return the object keyed by wire name. Choose **State property** to return its current value, including a list, number, or null. Existing placements that return the whole State keep that output.

For rolling context, declare a `Context` property with an empty string default. Connect the right port of API Response to a conversation-scoped State. Select a generation operation and enable **Include completed chat turn**. Its agent receives the current `state`, named wire `inputs`, prior `history`, and a separate `latestTurn` containing `user` and the selected `assistant` reply. Have the agent fold durable details from that pair into the prior Context and map the result back to the property. Chat sends the selected reply first. The State update then runs under the same run ID, and a subsequent turn in that conversation waits for the update to finish. To use a property earlier in a flow, add a State read with the same key and scope.

**This run** starts fresh each time. **This conversation** commits after a successful chat turn and is private to the signed-in user, Flow, library, and conversation. The chat page starts a new conversation on reset or reload. Debug and synchronous admin runs use temporary State. Custom chat clients using conversation State must send UUID headers `X-Lyra-Conversation-Id` and `X-Lyra-Turn-Id`. Keep the first stable across the conversation and reuse the second only when retrying the same request.

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

Every flow also includes a permanent **API Response**. Connect one Agent's right output port to its left input port to return its text or structured object as the response body. Chat and the flow run endpoint send HTTP 200 when the reply is ready. An unconnected API Response produces an empty result. Failures before the reply produce an HTTP error. Follow the run ID to see downstream work and any later failures. Existing browser drafts gain this response node automatically.

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

## System Flow Designer

To delegate from Lyra, open **Configuration → Agents → Lyra → Tool sets**, select **Flow Design**
and save. Reactivate the flow to pin the updated definition. The assignment follows the agent
across flows. Existing Flow Designer card connections also supply the tool. Administrator chat
defaults to **Query and explain** when the active snapshot includes the designer.
Select **Work on drafts** to enable edits. Lyra delegates a
plain-language task using one tool. **Query** inspects design data, **Explain** investigates
behavior or design options, and **Update** can edit flows, agents and prompts and test a draft.
Other chats continue using their pinned active snapshot while the designer works.

To switch chat, select **Allow activation this turn** and ask Lyra to activate the draft.
The runtime checks for a successful debug run with the same graph, agent definitions and
personalities. The binding changes atomically when the design turn completes. A newer draft
revision or a competing activation requires another review. Draft edits are saved as each
operation succeeds and remain available if later steps fail. The run inspector records the
delegated request, API operations and activation.

Open **Configuration → Agents → System Flow Designer → Open chat** and describe the change you want.
For example, ask “Inspect the most recent failed run, read the prompt version that produced the failure,
and draft a fix.” The chat shows the API calls and HTTP statuses for each completed turn.
The designer uses the default chat model initially. Select a tool-capable Ollama, OpenAI Chat Completions
or Claude model in its agent definition to change it. Its instructions are versioned in PromptStudio.
Restarting Lyra preserves your model, instructions and other edits to the seeded definition.

Designer chat and admin flow runs use the caller's delegated administrator access. The designer can read
and publish prompts, change an agent's version pins, edit graph wiring, test agents and flows, inspect
run events and linked AgentStudio traces, activate or disconnect a chat revision, and configure models,
groups, protocols and providers. Configuration operations for personalities, memories and continuity
use the same access checks as the UI. The designer can participate in admin flows. Global chat activation
requires a flow without the System Flow Designer.

## Connect Codex to Lyra

Use the Streamable HTTP endpoint `http://localhost:8765/mcp/design` for editing. It exposes the run inspectors plus these design tools.

| Task | Tools |
| --- | --- |
| Discover configuration operations and their request schemas | `list_design_operations`, `describe_design_operation` |
| Perform any listed operation with the same permissions and revision checks as the UI | `call_design_operation` |
| Discover editable objects, pinned prompt versions and model choices | `get_catalog`, `list_flows`, `get_flow` |
| Read a prompt or publish a version | `read_prompt`, `publish_prompt` |
| Create or revise an agent definition | `create_agent`, `update_agent` |
| Create or revise a saved flow graph | `create_flow`, `update_flow` |
| Test consecutive turns in a separate design conversation | `test_flow_turn`, `inspect_flow_run` |
| Inspect or activate the chat binding | `get_chat_binding`, `activate_chat_flow` |

For example, call `call_design_operation` with
`{"call":{"operation":"GET /api/admin/prompts","query":{"name":"Instructions.flow-designer","version":1}}}`.
The result includes the HTTP status and response data. To inspect an individual model trace, use
`GET /api/admin/runs/{run_id}/traces/{trace_id}` with the ID from the run's `agent.trace` event.

1. Obtain a delegated access token for Lyra’s backend using the full admin scope (`ENTRA_API_URI/ENTRA_ADMIN_SCOPE`) and an account with the admin role. The existing Lyra frontend registration is accepted. For a different token-issuing client, grant that client delegated access to the scope, add its application ID to `ENTRA_MCP_DESIGN_CLIENT_IDS` in Lyra’s `.env`, and restart Lyra. The inspector at `/mcp` has a separate client allowlist.
2. Register the server once. The Codex configuration stores the environment variable name. [OpenAI Docs on Codex MCP commands](https://learn.chatgpt.com/docs/developer-commands#codex-mcp)

   ```powershell
   codex mcp add lyra --url http://localhost:8765/mcp/design --bearer-token-env-var LYRA_MCP_TOKEN
   codex mcp get lyra
   ```

3. For the PowerShell launcher, authorize Azure CLI (application ID `04b07795-8ddb-461a-bbee-02f9e1bf7b46`) as the delegated client in step 1. With Lyra running, use the following on first sign-in. The script reads the tenant and scope from Lyra, obtains the token, shows its local expiration time and launches the Codex CLI in the Lyra project with `LYRA_MCP_TOKEN` set. [Azure CLI sign-in](https://learn.microsoft.com/cli/azure/authenticate-azure-cli-interactively)

   ```powershell
   .\start-codex.ps1 -Login
   ```

   On subsequent launches, run `.\start-codex.ps1` to use your Azure CLI sign-in. When the token expires, exit Codex and rerun the launcher.

**Codex Desktop.** For a local desktop connection, use the same Azure CLI authorization and sign-in. Set the `lyra` entry in the shared Codex `config.toml` to the following, adjusting the script path for your checkout. The helper obtains the authorization header from Azure CLI when Codex connects and when it retries after an expired token.

```toml
[mcp_servers.lyra]
url = "http://localhost:8765/mcp/design"
http_headers_helper = 'powershell.exe -NoProfile -NonInteractive -File "D:\Projects\Lyra\scripts\lyra-token.ps1" -McpHeaders'
```

Open a new desktop chat and ask “Use Lyra to list my flows.” If the server is missing from the tool list, fully quit and reopen the desktop app to load the updated configuration. [OpenAI Docs on local MCP configuration](https://developers.openai.com/codex/mcp#connect-an-existing-mcp-server)

Try “Use Lyra to list my flows.” Then try “Duplicate the flow named Intake as Intake Draft and tell me its new ID.” Codex can read the complete graph with `get_flow` and save a copy with `create_flow`. Updates require the current revision. On a conflict, reread the design and reconcile the edit before saving. Test a saved revision with `test_flow_turn`, reusing the conversation ID and supplying the chat history on each new turn. Its checkpoints are scoped to the design caller and revision. Activate the tested revision separately in Flow View or with `activate_chat_flow`.

## Chat API

### Long-term memory

Add **Recall** to search the current user's saved memories. Connect a message, a classifier's search phrase, or another flow value to its query input. Connect its output to the **Memories** port of each Agent or generating State that needs those results. Several agents can share one search. Use separate Recall cards for independent queries. The **Include prior conversation State in search** option adds the turn's starting context to the embedding query.

Add **Remember** to capture facts. In Properties, choose the evidence source and extraction timing. **Connected value only** requires a quote in both the connected value and an actual user message. **Completed chat exchange** accepts API Response or a generating State that includes the completed turn. Choose the default chat model or the model from a saved agent. Extraction can run when evidence arrives or after API Response. Both settings stage writes until the turn succeeds.

Set `LYRA_MEMORY_ENABLED=true` and supply `OPENAI_API_KEY` to enable these operations in activated chat. The chat client sends `X-Lyra-Conversation-Id` and `X-Lyra-Turn-Id`. Admin/debug runs can inspect wiring without accessing a user's memory store. Opening an older flow converts its API Input recall and enabled State capture into cards and wires. Save and reactivate it to apply edits to chat.

Open **Memories** in the configuration sidebar to browse or search stored memories. Each memory shows its original evidence and links to the turns that created, reinforced or recalled it, including the input, conversation ID, turn ID and flow node. A run's **Memories associated with this turn** section links back to those memory records. Administrator views use the same library-wide permissions as run inspection. Chat APIs at `GET /api/memories`, `GET /api/memories/{id}` and `GET /api/memories/turns/{run_id}` restrict results to the authenticated owner. Lists and per-memory turn history support `offset` and `limit` pagination.

The `memory_turns` relation records successful turns atomically with the conversation checkpoint. Deduplication preserves the original creation link and adds a reinforcement link for each later turn selecting the same content. Migration 6 restores creation links from existing source metadata and recall links from successful recorded events. Earlier reinforcement links were not retained and cannot be fully reconstructed.

Each Recall search is bounded to 6,000 UTF-8 bytes and returns up to eight memories, with the query first. Run history identifies the card that recalled or extracted each memory. Remember selects up to eight facts, preferences, decisions or goals with supporting user quotes. Memory rows, embeddings, provenance links and updated conversation State commit together when the run finishes successfully. Once a reply is recorded, retrying the same conversation and turn IDs returns that reply, even while extraction is running or if it later fails. A request that failed before recording a reply can retry with the same IDs.

`agent_studio.memories` holds text, evidence, type, source run/turn/node and timestamps, with a GIN full-text index. `agent_studio.memory_embeddings` references each memory UUID and records model, dimensions and full-precision pgvector data. Embeddings use OpenAI `text-embedding-3-large` with 3,072 dimensions. The separate embedding records preserve the information needed to export vectors to another backend. [OpenAI embedding guide](https://developers.openai.com/api/docs/guides/embeddings)

Retrieval combines English full-text ranking and exact cosine similarity through reciprocal rank fusion. Semantic candidates must have cosine similarity greater than 0.35. Ownership and model identity are filtered before ranking. Exact vector search is the initial implementation and should be measured against corpus size and latency before adding ANN indexes or another vector service. One full-size embedding space serves both storage and recall. [pgvector search documentation](https://github.com/pgvector/pgvector#hybrid-search)

`POST /api/memories/search` uses the same chat authentication and owner scope. Send `{"query":"preferred drinks","mode":"hybrid","limit":8}` for combined search or `{"query":"tea","mode":"text"}` for full-text search without an embedding call. Memory query context and selected memory text are sent to OpenAI for embedding. Extraction uses the model selected on Remember. The run inspector records recalled IDs and extraction model calls.

### Request and response

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

## Personalities

Open **Configuration → Personalities** to create a reusable personality with a prompt template,
goals and up to 24 curated memories. The supported placeholders are `{{name}}`, `{{goals}}` and
`{{memories}}`; omitted goals and memories are appended automatically. Preview renders the exact
personality section without calling a model. Template substitution is literal and does not execute
Jinja code. Goals are guidance for the model, not scheduled tasks or a separate goal executor.

Add a **Personality** card in Flow View and choose a saved profile in **Properties**. Connect its output
to the **Personality** port on each agent that should use it, or select the card in the agent’s
**Personality connection** field. Each agent accepts one profile alongside its ordinary data input.
Multiple cards may reference the same profile, and different agents may use different profiles.
Save and activate the flow for chat. Opening an older flow converts its participating agents to visible connections.
State updaters and memory extraction retain their own instructions. The run inspector records the
resolved prompt and the profile revision, goals and memories used. The personality editor links to
its latest 50 runs. Activate again using **Reactivate with latest personality and agents** after edits;
an active chat flow keeps its existing snapshot until reactivated, and historical runs never change.

Personality knowledge lives in `personality_memories`, separately from owner-scoped general
`memories`. All assigned personality memories are pinned into the prompt, independently of automatic
general-memory recall and `LYRA_MEMORY_ENABLED`. These curated facts are shared with everyone using
the flow. Copying a general memory into a personality is an explicit admin action that preserves a
source link; it does not move or edit the original. Automatic extraction never writes into personality
memory. System/agent instructions remain in the existing versioned prompt library.

Each new or edited personality memory receives the same OpenAI `text-embedding-3-large` embedding
as general memory, stored in `personality_memory_embeddings`; unchanged memories reuse their vectors.
The separate records include a PostgreSQL full-text index. Saving memory content requires the configured
OpenAI embedding key; prompt/goals-only edits do not call the embedding provider. Saves are atomic and
revision checked. Admin APIs are under `/api/admin/personalities`, including `/preview` and `/{id}/runs`.

## Private continuity and library

Enable **private continuity, library tools and automatic reflection** in a personality, then reactivate the flow. The first
chat copies its pinned foundation into a private continuation keyed by signed-in user and personality.
Every flow using that personality shares that user's continuation, even after a model change. Later
foundation edits seed new continuations; they do not overwrite existing ones. Constitution, persona,
goals and formative memories are stored independently of the model. In continuity mode these structured
fields replace the static personality template; an empty constitution initially falls back to the template.

**Configuration → Continuity** lets you inspect your continuation, browse from broad sections to detailed
items, search within a branch, follow typed connections, edit, archive, restore a version, and export/import
the current state as `lyra-continuity-v1` JSON. Restore and import create new versions and retain history.
The UI shows the latest 50 versions and source events. Export includes source references; full source
evidence stays in the original installation. Imported records are labeled accordingly.

Conversation agents can read and organize their own library, revise their profile with reasons and evidence
IDs, and adjust temporary focus and mood. A bounded post-turn reflection records significant choices and
insights. **Reflect now** runs one explicit cycle using the active flow's conversation model. User-memory
extraction remains separate. Debug/design runs use the shared foundation and do not modify private continuity.

OpenAI continuity agents use native Responses API function calls (including Sol); Claude agents use native
Messages API tools. Other adapters can still use static personalities. Runtime limits live outside editable
identity: 16 tool calls per turn, six conversation rounds, three reflection rounds, 25 seconds for reflection,
500 library items, 2,000 links and 512 KB per identity document. Writes are staged and commit atomically with
the successful chat turn. Failed or replayed turns cannot duplicate identity changes; concurrent changes
return a conflict. Reflection failures discard their own staged edits while preserving a successful answer.
Working mood changes are bounded and decay toward baseline with a six-hour half-life; focus expires after a day.

Continuations are isolated between users at runtime and in the Continuity API. As with existing chat traces,
administrators and database operators can inspect run prompts and tool activity. They are not encrypted from
the installation operator. Authored foundations remain shared. The model cannot change ownership, tools,
credentials or host code through its personality. No background schedule, external research, shell access or
autonomous code editing is enabled by this feature. Library retrieval currently uses lexical relevance and
one-hop links; semantic reranking and larger-scale graph navigation remain future work.
