import logging
import os
from hashlib import sha256
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from agent_studio import (
    AgentStudio, Endpoint, PostgresTraceAdapter, Model, ModelGroup,
    PromptStudioAdapter, Protocol,
)
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from prompt_studio import PromptLibrary, SQLAlchemyStorage
from pydantic import BaseModel, Field, JsonValue, StringConstraints

from auth import require_admin_access, require_chat_access, settings
from agent_definitions import router as definitions_router
from providers import router as providers_router
from agent_store import seed_defaults
from schema_migrations import apply_migrations, schema_transaction
from flows import router as flows_router
from flow_execution import perform
from run_store import Run, activation
from run_api import router as run_router
from run_mcp import install_mcp
from model_adapter import MeteredOllamaAdapter

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
PROMPT = "chat"
DEFAULT_MODEL = "qwen2.5-14b-q5:latest"
TEMPLATE = """You are Lyra, a helpful chat assistant. Answer the user's latest message.
{% for turn in history %}
{{ turn.role }}: {{ turn.content }}
{% endfor %}
user: {{ request }}
assistant:"""

MessageText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


def ui_version():
    digest = sha256()
    static = ROOT / "static"
    for path in sorted(static.rglob("*")):
        if path.is_file() and path.suffix in (".html", ".js", ".css"):
            digest.update(str(path.relative_to(static)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


class RevalidatingStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: MessageText


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000, pattern=r"\S")
    history: list[Turn] = Field(default_factory=list, max_length=12)


class ChatReply(BaseModel):
    reply: str | dict[str, JsonValue]


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("Set DATABASE_URL in .env to the Lyra PostgreSQL database")
    storage = SQLAlchemyStorage.from_postgres_url(os.environ["DATABASE_URL"])
    try:
        traces = PostgresTraceAdapter(storage.engine)
        with schema_transaction(storage.engine) as connection:
            storage.create_schema()
            traces.create_schema()
            apply_migrations(connection)
        library = PromptLibrary(storage, name="lyra")
        seed_defaults(library)
        library.ensure_initial(PROMPT, TEMPLATE)
        app.state.library = library

        model = Model(os.getenv("OLLAMA_MODEL", DEFAULT_MODEL))
        endpoint = Endpoint(
            os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
            Protocol.OLLAMA, (ModelGroup("local", (model,)),),
        )
        app.state.studio = AgentStudio(
            PromptStudioAdapter(library), MeteredOllamaAdapter(endpoint, model),
            traces,
        )
        async with mcp_server.session_manager.run():
            yield
    finally:
        storage.engine.dispose()


app = FastAPI(title="Lyra Chat", lifespan=lifespan)
app.include_router(definitions_router)
app.include_router(providers_router)
app.include_router(flows_router)
app.include_router(run_router)
mcp_server = install_mcp(app)
MSAL = ROOT / "node_modules/@azure/msal-browser/lib"


@app.get("/auth/config", include_in_schema=False)
def auth_config(site: Literal["chat", "admin"] = "chat"):
    config = settings()
    if site == "admin":
        config.check_admin_config()
    scope = config.admin_scope if site == "admin" else config.scope
    return {"clientId": config.frontend, "authority": config.authority,
            "redirectUri": config.redirect_uri, "scope": f"{config.resource}/{scope}"}


@app.get("/auth/callback", include_in_schema=False)
def spa_callback():
    return FileResponse(ROOT / "static/callback.html", headers={"Cache-Control": "no-store"})


@app.get("/api/admin/home", dependencies=[Depends(require_admin_access)])
def admin_home(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    studio = request.app.state.studio
    return {"prompt_library": request.app.state.library.name, "chat_model": studio.model.model.id,
            "ui_version": ui_version()}


@app.get("/admin/providers/", include_in_schema=False)
@app.get("/admin/models/", include_in_schema=False)
@app.get("/admin/model-groups/", include_in_schema=False)
@app.get("/admin/protocols/", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
@app.get("/admin/flow/", include_in_schema=False)
@app.get("/admin/agents/", include_in_schema=False)
@app.get("/admin/prompts/", include_in_schema=False)
def admin_page():
    version = ui_version()
    html = (ROOT / "static/admin/index.html").read_text(encoding="utf-8")
    for asset in ("/chat.css", "/admin/admin.css", "/vendor/msal.js", "/entra.js", "/admin/admin.js"):
        html = html.replace(f'"{asset}"', f'"{asset}?v={version}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/api/chat/session")
def chat_session(response: Response, roles: list[str] = Depends(require_chat_access)):
    response.headers["Cache-Control"] = "no-store"
    return {"can_configure": any(role in roles for role in settings().admin_roles)}


@app.get("/vendor/msal.js", include_in_schema=False)
def msal_browser():
    return FileResponse(MSAL / "msal-browser.min.js", media_type="application/javascript")


@app.get("/vendor/redirect-bridge.js", include_in_schema=False)
def msal_bridge():
    return FileResponse(MSAL / "redirect-bridge/msal-redirect-bridge.min.js", media_type="application/javascript")


@app.post("/api/chat", response_model=ChatReply, dependencies=[Depends(require_chat_access)])
async def chat(body: ChatRequest, request: Request, response: Response):
    studio = request.app.state.studio
    library = request.app.state.library
    document = activation(library)
    if document:
        run = Run(library, document, "chat")
        response.headers["X-Lyra-Run-Id"] = str(run.id)
        response.headers["X-Lyra-Flow-Id"] = str(document.id)
        try:
            reply = await perform(document, library, studio, body.message, run)
        except HTTPException as exc:
            raise HTTPException(exc.status_code, "Chat flow failed. An administrator can inspect the run.",
                                headers={"X-Lyra-Run-Id": str(run.id), "X-Lyra-Flow-Id": str(document.id)}) from exc
        return ChatReply(reply=reply)
    try:
        reply = await studio.run(
            body.message, prompt=PROMPT,
            parameters={"history": [turn.model_dump() for turn in body.history]},
        )
    except Exception as error:
        logging.getLogger(__name__).error("AgentStudio run failed (%s)", type(error).__name__)
        raise HTTPException(503, "Chat failed. Check Ollama and the PostgreSQL connection.") from error
    if not reply or not reply.strip():
        raise HTTPException(502, "The model returned an empty reply. Please try again.")
    return ChatReply(reply=reply)


app.mount("/", RevalidatingStaticFiles(directory=ROOT / "static", html=True), name="ui")
