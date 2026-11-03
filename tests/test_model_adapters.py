import json
import unittest
from unittest.mock import patch

import httpx

from model_adapter import provider_model


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_turns_reach_each_provider_in_order(self):
        messages = [{"role": "system", "content": "Follow the instructions."},
                    {"role": "assistant", "content": "Earlier reply"},
                    {"role": "user", "content": "Continue"}]
        for provider in ("ollama", "openai", "anthropic"):
            with self.subTest(provider=provider):
                body = {}

                async def respond(transport, request):
                    body.update(json.loads(request.content))
                    responses = {"ollama": {"model": "sample", "message": {"role": "assistant", "content": "ok"}, "done": True},
                                 "openai": {"choices": [{"message": {"content": "ok"}}]},
                                 "anthropic": {"content": [{"type": "text", "text": "ok"}]}}
                    return httpx.Response(200, json=responses[provider])

                model = provider_model({"uri": "http://localhost:11434", "adapter": provider, "models": ["sample"]}, "sample")
                with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", respond):
                    self.assertEqual(await model.complete(messages), "ok")
                expected = messages
                if provider == "anthropic":
                    self.assertEqual(body["system"], messages[0]["content"])
                    expected = messages[1:]
                self.assertEqual([{k: turn[k] for k in ("role", "content")} for turn in body["messages"]], expected)

    async def test_observed_ollama_call_preserves_the_wire_exchange(self):
        model = provider_model({"uri": "http://localhost:11434", "models": ["local"]}, "local")
        events = []

        async def respond(transport, request):
            self.assertEqual(request.url.path, "/api/chat")
            return httpx.Response(200, json={
                "model": "local", "message": {"role": "assistant", "content": "reply"},
                "done": True, "prompt_eval_count": 2, "eval_count": 3,
            })

        with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", respond):
            result, usage = await model.complete_observed(
                "hello", lambda kind, **data: events.append((kind, data)))
        self.assertEqual(result, "reply")
        self.assertEqual(usage["total_token_count"], 5)
        self.assertEqual([kind for kind, _ in events], ["provider.request", "provider.response"])
        self.assertEqual(events[0][1]["body"]["messages"][-1]["content"], "hello")
        self.assertEqual(events[1][1]["body"]["message"]["content"], result)

    async def test_same_server_different_client_protocols(self):
        for adapter in ("anthropic", "openai"):
            with self.subTest(adapter=adapter):
                provider = {"adapter": adapter, "uri": "https://custom.test/v1", "models": ["model-x"]}
                model = provider_model(provider, "model-x")
                requests = []

                def handle(request):
                    requests.append(request)
                    if adapter == "anthropic":
                        return httpx.Response(200, json={"content": [{"type": "text", "text": "reply"}],
                                              "usage": {"input_tokens": 2, "output_tokens": 3}})
                    return httpx.Response(200, json={"choices": [{"message": {"content": "reply"}}],
                                          "usage": {"total_tokens": 5}})

                client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
                events = []
                with patch.dict("os.environ", {"LYRA_PROVIDER_KEY_ENV": '{"https://custom.test/v1":"TEST_KEY"}',
                                               "TEST_KEY": "fixture-secret"}), \
                     patch("model_adapter.httpx.AsyncClient", return_value=client):
                    output, usage = await model.complete_observed("hello", lambda kind, **data: events.append(data))
                self.assertEqual((output, usage), ("reply", {"total_token_count": 5}))
                req, = requests
                self.assertEqual(req.url.path, "/v1/messages" if adapter == "anthropic" else "/v1/chat/completions")
                body = json.loads(req.content)
                self.assertEqual(body["model"], "model-x")
                self.assertEqual(body["messages"], [{"role": "user", "content": "hello"}])
                if adapter == "anthropic":
                    self.assertGreater(body["max_tokens"], 0)
                    self.assertEqual(req.headers["x-api-key"], "fixture-secret")
                    self.assertIn("anthropic-version", req.headers)
                else:
                    self.assertEqual(req.headers["authorization"], "Bearer fixture-secret")
                self.assertNotIn("fixture-secret", repr(events))

    async def test_local_openai_compatible_endpoint_does_not_receive_hosted_key(self):
        provider = {"adapter": "openai", "uri": "http://localhost:11434", "models": ["local"]}
        def handle(req):
            self.assertNotIn("authorization", req.headers)
            self.assertEqual(str(req.url), "http://localhost:11434/v1/chat/completions")
            return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fixture", "LYRA_PROVIDER_KEY_ENV": "{}"}), \
             patch("model_adapter.httpx.AsyncClient", return_value=client):
            self.assertEqual(await provider_model(provider, "local").complete("hello"), "hi")
