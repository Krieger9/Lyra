"""Bounded native tool loops. Execution authority is the supplied private session."""
import json

from fastapi import HTTPException

from identity import MAX_TOOL_CALLS, tool_specs
from json_mapping import json_text


class ToolModel:
    def __init__(self, model, session, observe=None, rounds=6, *, specs=None, responses=False):
        self.base, self.session, self.observe, self.rounds = model, session, observe, rounds
        self.model = model.model
        self.specs = specs
        self.responses = responses

    async def complete(self, prompt):
        result, _ = await self.complete_observed(prompt, self.observe)
        return result

    async def complete_observed(self, prompt, observe):
        messages = prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}]
        key = getattr(self.base, "key", None)
        if self.specs is None and key not in ("openai", "anthropic"):
            raise HTTPException(422, "Identity tools require an OpenAI or Claude provider.")
        specs = self.specs if self.specs is not None else tool_specs()
        total = 0
        chat_tools = self.specs is not None and key in ("openai", "ollama") and not self.responses
        if chat_tools:
            tools = [{"type": "function", "function": spec} for spec in specs]
            conversation = list(messages)
        elif key == "openai":
            tools = [{"type": "function", **s, "strict": False} for s in specs]
            conversation = list(messages)
        else:
            tools = [{"name": s["name"], "description": s["description"], "input_schema": s["parameters"]} for s in specs]
            system = "\n\n".join(m["content"] for m in messages if m["role"] in ("system", "developer"))
            conversation = [m for m in messages if m["role"] not in ("system", "developer")]
        for _ in range(self.rounds):
            if len(json_text(conversation).encode()) > 160_000:
                raise HTTPException(422, "Tool context budget exhausted.")
            if chat_tools:
                payload = {"model": self.model.id, "messages": conversation, "tools": tools, "stream": False}
                response = await self.base.request(self.base.path, payload, observe)
                if key == "openai":
                    choice = response["choices"][0]
                    if choice.get("finish_reason") in ("length", "content_filter"):
                        raise HTTPException(502, "Tool model response did not complete.")
                    message = choice["message"]
                    total += (response.get("usage") or {}).get("total_tokens", 0)
                else:
                    message = response["message"]
                    if response.get("done_reason") == "length":
                        raise HTTPException(502, "Tool model response did not complete.")
                    total += response.get("eval_count", 0) + response.get("prompt_eval_count", 0)
                conversation.append(message)
                calls = [c["function"] | {"call_id": c.get("id")} for c in message.get("tool_calls") or []]
                answer = message.get("content") or ""
            elif key == "openai":
                response = await self.base.request("responses", {"model": self.model.id, "input": conversation,
                    "tools": tools, "parallel_tool_calls": False, "max_output_tokens": 4096,
                    "store": False, "include": ["reasoning.encrypted_content"]}, observe)
                if response.get("status") in ("failed", "incomplete", "cancelled"):
                    raise HTTPException(502, "Tool model response did not complete.")
                blocks = response.get("output", [])
                calls = [b for b in blocks if b.get("type") == "function_call"]
                answer = "".join(c.get("text", c.get("refusal", "")) for b in blocks if b.get("type") == "message"
                                 for c in b.get("content", []))
                total += (response.get("usage") or {}).get("total_tokens", 0)
                conversation.extend(blocks)
            else:
                response = await self.base.request("messages", {"model": self.model.id, "system": system,
                    "messages": conversation, "tools": tools, "max_tokens": 4096}, observe)
                if response.get("stop_reason") in ("max_tokens", "pause_turn"):
                    raise HTTPException(502, "Tool model response did not complete.")
                blocks = response.get("content", [])
                calls = [b for b in blocks if b.get("type") == "tool_use"]
                answer = "".join(b["text"] for b in blocks if b.get("type") == "text")
                usage = response.get("usage") or {}
                total += sum(usage.get(k, 0) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
                conversation.append({"role": "assistant", "content": blocks})
            if not calls:
                if not answer.strip():
                    raise HTTPException(502, "Tool model returned no answer.")
                return answer, {"total_token_count": total}
            if len(calls) + self.session.calls > MAX_TOOL_CALLS:
                raise HTTPException(422, "Tool call budget exhausted.")
            results = []
            for call in calls:
                try:
                    args = call["input"] if key == "anthropic" else call["arguments"]
                    if isinstance(args, str):
                        args = json.loads(args)
                except (ValueError, KeyError, TypeError):
                    args = None
                result = await self.session.call(call.get("name", ""), args)
                if chat_tools:
                    reference = {"tool_call_id": call["call_id"]} if key == "openai" else {"tool_name": call["name"]}
                    conversation.append({"role": "tool", **reference, "content": json_text(result)})
                elif key == "openai":
                    conversation.append({"type": "function_call_output", "call_id": call["call_id"], "output": json_text(result)})
                else:
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": json_text(result),
                                    "is_error": "error" in result})
            if results:
                conversation.append({"role": "user", "content": results})
        raise HTTPException(422, "Tool reasoning round budget exhausted.")
