"""Shared agent execution, rendering and evidence capture."""
from time import perf_counter

from agent_studio import AgentStudio
from fastapi import HTTPException

from json_mapping import json_text
from output_contract import OutputSchema
from model_adapter import provider_model


class AgentPrompts:
    def __init__(self, library, definition, schema, observe=None, history=None):
        self.library, self.definition, self.schema = library, definition, schema
        self.observe = observe
        self.history = history

    def render_part(self, kind, reference, parameters):
        if self.observe:
            template = self.library.get(reference["name"], reference["version"]).text
            self.observe("template.started", part=kind, reference=reference, template=template, inputs=parameters)
        rendered = self.library.render(reference["name"], parameters, version=reference["version"])
        if self.observe:
            self.observe("template.rendered", part=kind, output=rendered)
        return rendered

    def render(self, name, parameters, **options):
        instruction = self.definition["instructions"]
        system = self.render_part("instructions", instruction, parameters)
        if self.schema:
            system += "\nReturn only a JSON object matching this schema:\n" + json_text(self.schema.json_schema())
            if self.observe:
                self.observe("schema.inserted", strategy="prompt", schema=self.schema.json_schema())
        prompt = self.definition["prompt"]
        content = parameters["request"] if prompt is None else self.render_part("prompt", prompt, parameters)
        if self.history is not None:
            return [{"role": "system", "content": system}, *self.history, {"role": "user", "content": content}]
        return system + "\n\n" + content


class ObservedModel:
    def __init__(self, model, observe):
        self.model, self.observe = model, observe

    async def complete(self, prompt):
        started = perf_counter()
        self.observe("model.request", prompt=prompt, model=getattr(getattr(self.model, "model", None), "id", None))
        usage = None
        if hasattr(type(self.model), "complete_observed"):
            result, usage = await self.model.complete_observed(prompt, self.observe)
        else:
            result = await self.model.complete(prompt)
        self.observe("model.response", output=result, usage=usage, duration_ms=round((perf_counter()-started)*1000))
        return result


class LinkedTrace:
    def __init__(self, traces, observe):
        self.traces, self.observe = traces, observe

    def save(self, trace):
        self.traces.save(trace)
        self.observe("agent.trace", traceId=trace["id"], status=trace["status"])


async def run_agent(library, studio, definition, value, observe=None, *, selection=None, chat_context=None):
    schema = OutputSchema.model_validate(definition["outputSchema"]) if definition["outputSchema"] else None
    history = None
    parameters = None
    if selection:
        if selection.source == "chat":
            parameters = {"wire": value}
        value, history = selection.select(value, chat_context)
        if observe:
            observe("input.selected", source=selection.source, mode=selection.mode, priorTurns=len(history))
    if model_selection := definition.get("modelSelection"):
        model = provider_model(definition["endpoint"], model_selection["model"])
    else:
        model = studio.model
    runner = AgentStudio(AgentPrompts(library, definition, schema, observe, history),
                         ObservedModel(model, observe) if observe else model,
                         LinkedTrace(studio.traces, observe) if observe else studio.traces)
    result = await runner.run(value if isinstance(value, str) else json_text(value), prompt=definition["name"],
                              parameters=parameters)
    if not isinstance(result, str):
        raise HTTPException(502, f"{definition['name']}: model returned invalid output.")
    if schema:
        try:
            return schema.parse_output(result, observe)
        except ValueError as exc:
            raise HTTPException(502, f"{definition['name']}: {exc}") from exc
    return result
