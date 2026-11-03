"""Ollama adapter exposing provider-reported usage to the flow recorder."""
import json

from agent_studio import OllamaAgentAdapter


class MeteredOllamaAdapter(OllamaAgentAdapter):
    async def complete_observed(self, prompt, observe):
        from agent_framework import Agent
        from agent_framework.ollama import OllamaChatClient

        client = OllamaChatClient(host=self.endpoint.uri, model=self.model.id)

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

        # Ollama exposes its managed httpx transport through _client. Keep that
        # integration here so the executor never depends on transport internals.
        hooks = client.client._client.event_hooks
        hooks["request"].append(sent)
        hooks["response"].append(received)
        async with Agent(client=client, name="AgentStudio") as agent:
            result = await agent.run(prompt)
            return result.text, result.usage_details
