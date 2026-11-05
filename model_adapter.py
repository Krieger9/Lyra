"""Client protocol registry. Each factory builds an AgentStudio model adapter."""
import json
import os
from urllib.parse import urlsplit

import httpx
from agent_studio import Endpoint, Model, ModelGroup, OllamaAgentAdapter, Protocol
from fastapi import HTTPException


class MeteredOllamaAdapter(OllamaAgentAdapter):
    key = "ollama"
    path = "api/chat"

    async def request(self, path, payload, observe=None):
        if observe:
            observe("provider.request", provider=self.key, body=payload)
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(self.endpoint.uri.rstrip("/") + "/" + path, json=payload)
        if not response.is_success:
            raise HTTPException(502, f"Ollama tool request returned HTTP {response.status_code}. Choose a tool-capable model.")
        result = response.json()
        if observe:
            observe("provider.response", provider=self.key, status=response.status_code, body=result)
        return result

    async def complete_result(self, prompt, *, event_hooks=None):
        if isinstance(prompt, list):
            from agent_framework import Message
            prompt = [Message(turn["role"], [turn["content"]]) for turn in prompt]
        return await super().complete_result(prompt, event_hooks=event_hooks)

    async def complete_observed(self, prompt, observe):
        async def sent(request):
            if request.url.path.endswith("/api/chat"):
                observe("provider.request", provider="ollama", body=json.loads(request.content))

        async def received(response):
            if response.request.url.path.endswith("/api/chat"):
                await response.aread()
                try:
                    body = response.json()
                except ValueError:
                    body = response.text
                observe("provider.response", provider="ollama", status=response.status_code, body=body)

        result = await self.complete_result(prompt, event_hooks={"request": [sent], "response": [received]})
        return result.text, result.usage_details


class _HttpModelAdapter:
    """Shared transport for providers with a JSON messages API."""
    def __init__(self, uri, model):
        self.uri = uri.rstrip("/")
        if not urlsplit(self.uri).path:
            self.uri += "/v1"
        self.model = model

    def body(self, prompt):
        messages = prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}]
        return {"model": self.model.id, "messages": messages}

    async def complete(self, prompt):
        text, _ = await self.complete_observed(prompt, None)
        return text

    async def complete_observed(self, prompt, observe):
        result = await self.request(self.path, self.body(prompt), observe)
        try:
            output, tokens = self.unpack(result)
            if not isinstance(output, str):
                raise ValueError
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise HTTPException(502, f"{self.key} endpoint returned an invalid text completion.") from None
        return output, {"total_token_count": tokens} if tokens is not None else None

    async def request(self, path, payload, observe=None):
        # Keys are bound by the operator to an exact API base, never to an arbitrary UI destination.
        bindings = {"https://api.openai.com/v1": "OPENAI_API_KEY", "https://api.anthropic.com/v1": "ANTHROPIC_API_KEY"}
        bindings.update(json.loads(os.getenv("LYRA_PROVIDER_KEY_ENV", "{}")))
        variable = bindings.get(self.uri)
        credential = os.getenv(variable) if variable else None
        if variable and not credential:
            raise HTTPException(503, f"The configured credential for {self.uri} is unavailable.")
        if observe:
            observe("provider.request", provider=self.key, body=payload)
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(self.uri + "/" + path, headers=self.headers(credential), json=payload)
        if not response.is_success:
            if observe:
                observe("provider.response", provider=self.key, status=response.status_code)
            raise HTTPException(502, f"{self.key} endpoint returned HTTP {response.status_code}.")
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise HTTPException(502, f"{self.key} endpoint returned an invalid text completion.") from None
        if observe:
            observe("provider.response", provider=self.key, status=response.status_code, body=result)
        return result


class ChatCompletionsAdapter(_HttpModelAdapter):
    key = "openai"
    path = "chat/completions"

    def headers(self, credential):
        return {"Authorization": "Bearer " + credential} if credential else {}

    def unpack(self, result):
        return result["choices"][0]["message"]["content"], (result.get("usage") or {}).get("total_tokens")


class ClaudeMessagesAdapter(_HttpModelAdapter):
    key = "anthropic"
    path = "messages"

    def headers(self, credential):
        return {"anthropic-version": "2023-06-01", **({"x-api-key": credential} if credential else {})}

    def body(self, prompt):
        body = super().body(prompt) | {"max_tokens": 4096}
        messages = body["messages"]
        if messages[0]["role"] == "system":
            body.update(system=messages[0]["content"], messages=messages[1:])
        return body

    def unpack(self, result):
        blocks = [block["text"] for block in result["content"] if block["type"] == "text"]
        if not blocks:
            raise ValueError
        usage = result.get("usage", {})
        tokens = sum(usage.get(key, 0) for key in (
            "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        return "".join(blocks), tokens if usage else None


def native_ollama(uri, model):
    endpoint = Endpoint(uri, Protocol.OLLAMA, (ModelGroup("selected", (model,)),))
    return MeteredOllamaAdapter(endpoint, model)


# Register a label and factory here to make another client available to the protocol editor.
ADAPTERS = {
    "ollama": ("Ollama native", native_ollama),
    "openai": ("OpenAI Chat Completions", ChatCompletionsAdapter),
    "anthropic": ("Claude Messages", ClaudeMessagesAdapter),
}


def provider_model(provider, model_name):
    adapter = ADAPTERS.get(provider.get("adapter", "ollama"))
    if not adapter or model_name not in provider["models"]:
        raise HTTPException(422, "The provider snapshot needs an available adapter and model.")
    return adapter[1](provider["uri"], Model(model_name))
