"""Conversation checkpoints reserve delivered replies and publish State when the run succeeds."""
from dataclasses import dataclass
import asyncio
from hashlib import sha256
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Column, ForeignKey, Integer, String, Table, UniqueConstraint, Uuid, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from starlette.concurrency import run_in_threadpool

from agent_store import metadata
from json_mapping import check_size, json_text
from run_store import runs, last_event_at, run_summary


checkpoints = Table("flow_state_turns", metadata,
    Column("library", String(255), primary_key=True), Column("owner", String, primary_key=True),
    Column("conversation_id", Uuid, primary_key=True), Column("flow_id", Uuid, primary_key=True),
    Column("version", Integer, primary_key=True), Column("turn_id", Uuid, nullable=False),
    Column("request_hash", String(64), nullable=False), Column("objects", JSONB, nullable=False),
    Column("run_id", Uuid, ForeignKey(runs.c.id), nullable=False),
    UniqueConstraint("library", "owner", "conversation_id", "flow_id", "turn_id"), schema="agent_studio")


@dataclass
class ConversationTurn:
    scope: dict
    version: int
    turn_id: UUID
    request_hash: str
    objects: dict
    initial_objects: dict
    reply_reserved: bool = False

    def commit(self, conn, run_id):
        check_size(self.objects)
        if self.reply_reserved:
            conn.execute(checkpoints.update().where(checkpoints.c.run_id == run_id).values(objects=self.objects))
        else:
            self._insert(conn, run_id, self.objects)

    def reserve_reply(self, conn, run_id):
        # Replays can see the reply immediately. Only finalization publishes new State.
        self._insert(conn, run_id, self.initial_objects)
        self.reply_reserved = True

    def _insert(self, conn, run_id, objects):
        inserted = conn.execute(insert(checkpoints).values(**self.scope, version=self.version + 1,
            turn_id=self.turn_id, request_hash=self.request_hash, objects=objects, run_id=run_id)
            .on_conflict_do_nothing().returning(checkpoints.c.version)).scalar_one_or_none()
        if inserted is None:
            raise HTTPException(409, "Another turn completed concurrently. Retry this message.")


def open_turn(library, owner, flow_id, conversation_id, turn_id, body, *, wait_for_previous=False):
    scope = dict(library=library.name, owner=owner, flow_id=flow_id, conversation_id=conversation_id)
    digest = sha256(json_text(body.model_dump()).encode()).hexdigest()
    scoped = select(checkpoints).filter_by(**scope)
    with library.storage.engine.connect() as conn:
        prior = conn.execute(scoped.where(checkpoints.c.turn_id == turn_id)).mappings().first()
        if prior:
            if prior["request_hash"] != digest:
                raise HTTPException(409, "This turn ID was already used for a different request.")
            reply = conn.execute(select(runs.c.result).where(runs.c.id == prior["run_id"])).scalar_one()
            return None, (prior["run_id"], reply)
        latest = conn.execute(scoped.add_columns(runs.c.status, runs.c.started_at, last_event_at)
            .join(runs, runs.c.id == checkpoints.c.run_id)
            .order_by(checkpoints.c.version.desc()).limit(1)).mappings().first()
        if wait_for_previous and latest and run_summary(latest)["status"] == "running":
            return None, None
    objects = latest["objects"] if latest else {}
    return ConversationTurn(scope, latest["version"] if latest else 0, turn_id, digest, dict(objects), objects), None


async def open_chat_turn(*args):
    while True:
        turn, replay = await run_in_threadpool(open_turn, *args, wait_for_previous=True)
        if turn or replay:
            return turn, replay
        await asyncio.sleep(0.2)
