"""LangGraph checkpoints in the studio's own database, through SQLAlchemy, so one implementation serves SQLite,
PostgreSQL and Oracle (which has no official LangGraph saver). Modelled on langgraph's SqliteSaver."""

import json
import random
from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP, BaseCheckpointSaver, ChannelVersions, Checkpoint, CheckpointMetadata, CheckpointTuple,
    get_checkpoint_id, get_checkpoint_metadata,
)
from sqlalchemy import delete, select

from app.db.models import GraphCheckpoint, GraphWrite
from app.db.session import session_scope

# Oracle stores '' as NULL, which a primary key column can't hold; the root namespace is stored as this instead.
_ROOT_NS = "__root__"


def _ns_in(ns: str | None) -> str:
    return ns or _ROOT_NS


def _ns_out(ns: str) -> str:
    return "" if ns == _ROOT_NS else ns


class SqlAlchemySaver(BaseCheckpointSaver[str]):
    """Each call uses its own short session from app.db.session, so it is safe across the runner's threads and
    follows the database the studio is currently configured with."""

    def _writes(self, s, thread_id: str, ns: str, checkpoint_id: str) -> list[tuple[str, str, Any]]:
        rows = s.execute(select(GraphWrite.task_id, GraphWrite.channel, GraphWrite.type, GraphWrite.value)
                         .where(GraphWrite.thread_id == thread_id, GraphWrite.checkpoint_ns == ns,
                                GraphWrite.checkpoint_id == checkpoint_id)
                         .order_by(GraphWrite.task_id, GraphWrite.idx)).all()
        return [(task_id, channel, self.serde.loads_typed((type_, value))) for task_id, channel, type_, value in rows]

    def _tuple(self, s, row: GraphCheckpoint) -> CheckpointTuple:
        ns = _ns_out(row.checkpoint_ns)
        config = {"configurable": {"thread_id": row.thread_id, "checkpoint_ns": ns,
                                   "checkpoint_id": row.checkpoint_id}}
        parent = ({"configurable": {"thread_id": row.thread_id, "checkpoint_ns": ns,
                                    "checkpoint_id": row.parent_checkpoint_id}}
                  if row.parent_checkpoint_id else None)
        return CheckpointTuple(
            config,
            self.serde.loads_typed((row.type, row.checkpoint)),
            json.loads(row.meta) if row.meta is not None else {},
            parent,
            self._writes(s, row.thread_id, row.checkpoint_ns, row.checkpoint_id),
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = str(config["configurable"]["thread_id"])
        ns = _ns_in(config["configurable"].get("checkpoint_ns"))
        query = select(GraphCheckpoint).where(GraphCheckpoint.thread_id == thread_id,
                                              GraphCheckpoint.checkpoint_ns == ns)
        if checkpoint_id := get_checkpoint_id(config):
            query = query.where(GraphCheckpoint.checkpoint_id == checkpoint_id)
        else:
            query = query.order_by(GraphCheckpoint.checkpoint_id.desc()).limit(1)
        with session_scope() as s:
            row = s.scalar(query)
            return self._tuple(s, row) if row is not None else None

    def list(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None,
             before: RunnableConfig | None = None, limit: int | None = None) -> Iterator[CheckpointTuple]:
        query = select(GraphCheckpoint).order_by(GraphCheckpoint.checkpoint_id.desc())
        if config is not None:
            query = query.where(GraphCheckpoint.thread_id == str(config["configurable"]["thread_id"]))
            ns = config["configurable"].get("checkpoint_ns")
            if ns is not None:
                query = query.where(GraphCheckpoint.checkpoint_ns == _ns_in(ns))
            if checkpoint_id := get_checkpoint_id(config):
                query = query.where(GraphCheckpoint.checkpoint_id == checkpoint_id)
        if before is not None and (before_id := get_checkpoint_id(before)):
            query = query.where(GraphCheckpoint.checkpoint_id < before_id)
        with session_scope() as s:
            found = []
            for row in s.scalars(query):
                # Metadata filters are matched here rather than in SQL: JSON functions differ per database.
                if filter:
                    meta = json.loads(row.meta) if row.meta is not None else {}
                    if any(meta.get(k) != v for k, v in filter.items()):
                        continue
                found.append(self._tuple(s, row))
                if limit is not None and len(found) >= limit:
                    break
        yield from found

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata,
            new_versions: ChannelVersions) -> RunnableConfig:
        thread_id = str(config["configurable"]["thread_id"])
        ns = config["configurable"].get("checkpoint_ns", "")
        type_, blob = self.serde.dumps_typed(checkpoint)
        meta = json.dumps(get_checkpoint_metadata(config, metadata), ensure_ascii=False).encode("utf-8", "ignore")
        with session_scope() as s:
            s.merge(GraphCheckpoint(thread_id=thread_id, checkpoint_ns=_ns_in(ns), checkpoint_id=checkpoint["id"],
                                    parent_checkpoint_id=config["configurable"].get("checkpoint_id"),
                                    type=type_, checkpoint=blob, meta=meta))
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str,
                   task_path: str = "") -> None:
        thread_id = str(config["configurable"]["thread_id"])
        ns = _ns_in(config["configurable"].get("checkpoint_ns"))
        checkpoint_id = str(config["configurable"]["checkpoint_id"])
        replace = all(channel in WRITES_IDX_MAP for channel, _ in writes)  # special channels overwrite
        with session_scope() as s:
            for idx, (channel, value) in enumerate(writes):
                key = (thread_id, ns, checkpoint_id, task_id, WRITES_IDX_MAP.get(channel, idx))
                existing = s.get(GraphWrite, key)
                if existing is not None and not replace:
                    continue
                type_, blob = self.serde.dumps_typed(value)
                s.merge(GraphWrite(thread_id=key[0], checkpoint_ns=key[1], checkpoint_id=key[2], task_id=key[3],
                                   idx=key[4], channel=channel, type=type_, value=blob))

    def delete_thread(self, thread_id: str) -> None:
        with session_scope() as s:
            s.execute(delete(GraphCheckpoint).where(GraphCheckpoint.thread_id == str(thread_id)))
            s.execute(delete(GraphWrite).where(GraphWrite.thread_id == str(thread_id)))

    def get_next_version(self, current: str | None, channel: None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        return f"{current_v + 1:032}.{random.random():016}"
