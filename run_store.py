"""Durable execution evidence. The graph, definitions and events belong to the run."""
from datetime import datetime, timedelta, timezone
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException
from agent_studio.postgres_tracing import traces
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import JSONB, insert

from agent_store import metadata
from flow_graph import FlowSnapshot
from memory_store import memories_for_run

RUN_TIMEOUT = 120
runs = Table("flow_runs", metadata,
    Column("id", Uuid, primary_key=True), Column("library", String(255), nullable=False),
    Column("flow_id", Uuid, nullable=False), Column("revision", Integer, nullable=False),
    Column("source", String, nullable=False), Column("status", String, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False), Column("finished_at", DateTime(timezone=True)),
    Column("snapshot", JSONB, nullable=False), Column("result", JSON), Column("error", String),
    Index("ix_flow_runs_history", "library", "flow_id", "started_at"),
    schema="agent_studio")
events = Table("flow_run_events", metadata,
    Column("run_id", Uuid, ForeignKey(runs.c.id, ondelete="CASCADE"), primary_key=True),
    Column("seq", Integer, primary_key=True), Column("node", String), Column("kind", String, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False), Column("elapsed_ms", Integer, nullable=False),
    Column("data", JSON, nullable=False), schema="agent_studio")
active = Table("chat_flow", metadata, Column("library", String(255), primary_key=True),
               Column("snapshot", JSONB, nullable=False), schema="agent_studio")


def utcnow():
    return datetime.now(timezone.utc)


class Run:
    def __init__(self, library, document: FlowSnapshot, source):
        self.engine = library.storage.engine
        self.id = uuid4()
        self.started = perf_counter()
        self.seq = 0
        self.node = None
        with self.engine.begin() as conn:
            cutoff = utcnow() - timedelta(seconds=RUN_TIMEOUT + 60)
            conn.execute(runs.update().where(runs.c.library == library.name, runs.c.status == "running",
                runs.c.started_at < cutoff, ~select(events.c.seq).where(
                    events.c.run_id == runs.c.id, events.c.at >= cutoff).exists()
                ).values(status="interrupted", error="Execution worker stopped."))
            conn.execute(runs.insert().values(id=self.id, library=library.name, flow_id=document.id,
                revision=document.revision, snapshot=document.model_dump(mode="json", exclude_unset=True),
                source=source, status="running", started_at=utcnow()))

    def _append(self, conn, kind, data):
        self.seq += 1
        conn.execute(events.insert().values(run_id=self.id, seq=self.seq, node=self.node, kind=kind,
            at=utcnow(), elapsed_ms=round((perf_counter()-self.started)*1000), data=data))

    def emit(self, kind, **data):
        with self.engine.begin() as conn:
            self._append(conn, kind, data)

    def publish_reply(self, result, turn):
        with self.engine.begin() as conn:
            if turn is not None:
                turn.reserve_reply(conn, self.id)
            conn.execute(runs.update().where(runs.c.id == self.id).values(result=result))
            self._append(conn, "response.ready", {})

    def finish(self, status, result=None, error=None, turn=None, memory=None, identities=()):
        with self.engine.begin() as conn:
            if turn is not None:
                turn.commit(conn, self.id)
            if memory is not None:
                links = memory.commit(conn, self.id)
                self._append(conn, "memory.committed", {"count": len(memory.pending), "memories": links})
            for identity in identities:
                committed = identity.commit(conn, self.id, str(identity.source.get("message", "")))
                self._append(conn, "identity.committed", committed)
            if error and self.node:
                self._append(conn, "node.failed", {"error": error})
            self.node = None
            self._append(conn, "run." + status, {"error": error} if error else {})
            terminal = dict(status=status, finished_at=utcnow(), error=error)
            if status == "completed":
                terminal["result"] = result
            conn.execute(runs.update().where(runs.c.id == self.id).values(**terminal))


def activation(library) -> FlowSnapshot | None:
    with library.storage.engine.connect() as conn:
        saved = conn.execute(select(active.c.snapshot).where(active.c.library == library.name)).scalar_one_or_none()
        return FlowSnapshot.model_validate(saved) if saved is not None else None


def activate(library, document: FlowSnapshot):
    saved = document.model_dump(mode="json", exclude_unset=True)
    with library.storage.engine.begin() as conn:
        conn.execute(insert(active).values(library=library.name, snapshot=saved).on_conflict_do_update(
            index_elements=[active.c.library], set_={"snapshot": saved}))


def deactivate(library, flow_id, revision):
    with library.storage.engine.begin() as conn:
        removed = conn.execute(active.delete().where(active.c.library == library.name,
            active.c.snapshot["id"].astext == str(flow_id), active.c.snapshot["revision"].as_integer() == revision))
        if not removed.rowcount:
            raise HTTPException(409, "The active chat flow has changed. Refresh the run view.")


last_event_at = select(events.c.at).where(events.c.run_id == runs.c.id).order_by(
    events.c.seq.desc()).limit(1).correlate(runs).scalar_subquery().label("last_event_at")
summary = [runs.c.id, runs.c.flow_id, runs.c.revision, runs.c.source, runs.c.status,
           runs.c.started_at, runs.c.finished_at, runs.c.error, last_event_at]


def run_summary(row):
    # A killed worker cannot close its run. Readers can recognize abandoned
    # runs even before the next writer reconciles them.
    result = dict(row)
    activity = result.pop("last_event_at", None) or row["started_at"]
    if row["status"] == "running" and activity < utcnow() - timedelta(seconds=RUN_TIMEOUT + 60):
        result.update(status="interrupted", error="Execution worker stopped.")
    return result


def recent(library, flow_id=None, offset=0, limit=30):
    query = select(*summary).where(runs.c.library == library.name)
    if flow_id:
        query = query.where(runs.c.flow_id == flow_id)
    with library.storage.engine.connect() as conn:
        return [run_summary(r) for r in conn.execute(query.order_by(runs.c.started_at.desc(), runs.c.id)
            .offset(offset).limit(limit)).mappings()]


def inspect_run(library, run_id, after=0, limit=100, include_snapshot=True):
    fields = [*summary, runs.c.result]
    if include_snapshot:
        fields.append(runs.c.snapshot)
    with library.storage.engine.connect() as conn:
        row = conn.execute(select(*fields).where(runs.c.library == library.name, runs.c.id == run_id)).mappings().first()
        if row is None:
            raise HTTPException(404, "Run not found.")
        page = conn.execute(select(events.c.seq, events.c.node, events.c.kind, events.c.at,
            events.c.elapsed_ms, events.c.data).where(events.c.run_id == run_id, events.c.seq > after)
            .order_by(events.c.seq).limit(limit+1)).mappings().all()
        return run_summary(row) | {"schemaVersion": 1, "events": [dict(e) for e in page[:limit]], "hasMore": len(page)>limit,
                                   "memories": memories_for_run(conn, library.name, run_id)}


def linked_trace(library, run_id, trace_id):
    linked = select(events.c.seq).select_from(events.join(runs)).where(
        runs.c.library == library.name, runs.c.id == run_id, events.c.kind == "agent.trace",
        events.c.data["traceId"].as_string().in_([trace_id.hex, str(trace_id)])).exists()
    with library.storage.engine.connect() as conn:
        payload = conn.execute(select(traces.c.payload).where(traces.c.id == trace_id, linked)).scalar_one_or_none()
    if payload is None:
        raise HTTPException(404, "Trace not found in this run.")
    return payload
