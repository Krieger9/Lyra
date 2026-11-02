import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from agent_studio import (
    AgentStudio, Endpoint, PostgresTraceAdapter, Model, ModelGroup,
    OllamaAgentAdapter, PromptStudioAdapter, Protocol,
)
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from prompt_studio import NotFoundError, PromptLibrary, SQLAlchemyStorage
from pydantic import BaseModel, Field, StringConstraints

from auth import require_admin_access, require_chat_access, settings

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


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: MessageText


class ChatRequest(BaseModel):
    message: MessageText
    history: list[Turn] = Field(default_factory=list, max_length=12)


class ChatReply(BaseModel):
    reply: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("Set DATABASE_URL in .env to the Lyra PostgreSQL database")
    storage = SQLAlchemyStorage.from_postgres_url(os.environ["DATABASE_URL"])
    try:
        storage.create_schema()
        traces = PostgresTraceAdapter(storage.engine)
        traces.create_schema()
        library = PromptLibrary(storage, name="lyra")
        try:
            library.get(PROMPT)
        except NotFoundError:
            library.add(PROMPT, TEMPLATE)

        model = Model(os.getenv("OLLAMA_MODEL", DEFAULT_MODEL))
        endpoint = Endpoint(
            os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
            Protocol.OLLAMA, (ModelGroup("local", (model,)),),
        )
        app.state.studio = AgentStudio(
            PromptStudioAdapter(library), OllamaAgentAdapter(endpoint, model),
            traces,
        )
        yield
    finally:
        storage.engine.dispose()


app = FastAPI(title="Lyra Chat", lifespan=lifespan)
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
    return {"prompt_library": studio.prompts.library.name, "chat_model": studio.model.model.id}


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
async def chat(body: ChatRequest, request: Request):
    try:
        reply = await request.app.state.studio.run(
            body.message, prompt=PROMPT,
            parameters={"history": [turn.model_dump() for turn in body.history]},
        )
    except Exception as error:
        logging.getLogger(__name__).error("AgentStudio run failed (%s)", type(error).__name__)
        raise HTTPException(503, "Chat failed. Check Ollama and the PostgreSQL connection.") from error
    if not reply or not reply.strip():
        raise HTTPException(502, "The model returned an empty reply. Please try again.")
    return ChatReply(reply=reply)


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="ui")
