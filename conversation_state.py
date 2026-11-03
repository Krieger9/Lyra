"""Committed conversation State checkpoints. Each successful turn advances one version."""
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Column, ForeignKey, Integer, String, Table, UniqueConstraint, Uuid, select
from sqlalchemy.dialects.postgresql import JSONB, insert

from agent_store import metadata
from json_mapping import check_size, json_text
from run_store import runs


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

    def commit(self, conn, run_id):
        check_size(self.objects)
        inserted = conn.execute(insert(checkpoints).values(**self.scope, version=self.version + 1,
            turn_id=self.turn_id, request_hash=self.request_hash, objects=self.objects, run_id=run_id)
            .on_conflict_do_nothing().returning(checkpoints.c.version)).scalar_one_or_none()
        if inserted is None:
            raise HTTPException(409, "Another turn completed concurrently. Retry this message.")


def open_turn(library, owner, flow_id, conversation_id, turn_id, body):
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
        latest = conn.execute(scoped.order_by(checkpoints.c.version.desc()).limit(1)).mappings().first()
    return ConversationTurn(scope, latest["version"] if latest else 0, turn_id, digest,
                            latest["objects"] if latest else {}), None
