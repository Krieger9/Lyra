"""Catalog of tool sets that an administrator can assign to a saved agent."""

FLOW_DESIGN = "flow-design"
TOOL_SETS = {
    FLOW_DESIGN: {"id": FLOW_DESIGN, "name": "Flow Design", "tools": ["flow_designer"]},
}


class AgentTools:
    """Dispatch each advertised tool to the session that supplied it."""
    def __init__(self, bindings):
        self.sessions = []
        self.specs = []
        self.handlers = {}
        for session, specs in bindings:
            self.sessions.append(session)
            for spec in specs:
                name = spec["name"]
                if name in self.handlers:
                    raise ValueError(f"Duplicate tool name: {name}")
                self.handlers[name] = session
                self.specs.append(spec)

    @property
    def calls(self):
        return sum(session.calls for session in self.sessions)

    async def call(self, name, arguments):
        session = self.handlers.get(name)
        if session is None:
            return {"error": "Unknown agent tool."}
        return await session.call(name, arguments)
