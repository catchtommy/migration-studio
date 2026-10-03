"""The agent library, persisted in `agents` and edited from the Agents page. Workflows use agents as stages."""

import json
from typing import Any

from sqlalchemy import inspect, select, text

from app.agents.defaults import DOCS, default_config
from app.db.models import Agent, Setting
from app.db.session import engine, session_scope
from app.orchestrator.stages import STANDARD

FIELDS = ("title", "description", "mode", "model", "effort", "jdk", "instructions", "tools", "outputs", "max_turns")
BUILTIN = tuple(STANDARD.agent_keys)


def _as_dict(row: Agent) -> dict[str, Any]:
    return {"key": row.key, **{f: getattr(row, f) for f in FIELDS}, "builtin": row.key in BUILTIN,
            "created_at": row.created_at, "updated_at": row.updated_at}


def builtin_default(key: str) -> dict[str, Any]:
    stage = STANDARD.by_key[key]
    return {"title": stage.title, "description": stage.description, **default_config(key)}


def custom_default(key: str, title: str, description: str) -> dict[str, Any]:
    return {
        "title": title, "description": description, "mode": "stub", "model": default_config("deep_dive")["model"],
        "effort": "high", "jdk": "legacy", "tools": list(DOCS), "outputs": [f"{key}.md"], "max_turns": 80,
        "instructions": f"You are the {title} agent.\n\nDescribe what this agent should do, which code and documents "
                        "it should read, and what it must produce.",
    }


def _legacy_configs() -> dict[str, dict]:
    """Per-stage configs saved before agents became a library (table `agent_configs`)."""
    if not inspect(engine).has_table("agent_configs"):
        return {}
    with engine.connect() as conn:
        return {row["stage"]: dict(row) for row in conn.execute(text("SELECT * FROM agent_configs")).mappings()}


def seed() -> None:
    """On first start, create the built-in agents, keeping choices saved under older versions."""
    with session_scope() as s:
        if s.scalar(select(Agent).limit(1)) is not None:
            return
        legacy = _legacy_configs()
        old_settings = {row.key: row.value for row in s.query(Setting).filter(
            Setting.key.in_(["agent_mode", "agent_models", "agent_effort"]))}
        for key in BUILTIN:
            config = builtin_default(key)
            if key in legacy:
                config.update({f: legacy[key][f] for f in FIELDS if f in legacy[key] and legacy[key][f] is not None})
                for f in ("tools", "outputs"):  # stored as JSON text by the raw query
                    if isinstance(config[f], str):
                        config[f] = json.loads(config[f])
            else:
                if "agent_mode" in old_settings:
                    config["mode"] = old_settings["agent_mode"]
                if key in (old_settings.get("agent_models") or {}):
                    config["model"] = old_settings["agent_models"][key]
                if "agent_effort" in old_settings:
                    config["effort"] = old_settings["agent_effort"]
            s.add(Agent(key=key, **config))


def list_all() -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.scalars(select(Agent)).all()
        order = {k: i for i, k in enumerate(BUILTIN)}
        rows = sorted(rows, key=lambda r: (order.get(r.key, len(order)), r.created_at))
        return [_as_dict(r) for r in rows]


def get(key: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Agent, key)
        if row is None:
            raise KeyError(key)
        return _as_dict(row)


def exists(key: str) -> bool:
    with session_scope() as s:
        return s.get(Agent, key) is not None


def titles() -> dict[str, tuple[str, str]]:
    """key -> (title, description) for every agent."""
    with session_scope() as s:
        return {r.key: (r.title, r.description) for r in s.scalars(select(Agent))}


def create(key: str, title: str, description: str) -> dict[str, Any]:
    with session_scope() as s:
        if s.get(Agent, key) is not None:
            raise ValueError(f"An agent with key {key} already exists")
        s.add(Agent(key=key, **custom_default(key, title, description)))
    return get(key)


def update(key: str, values: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Agent, key)
        if row is None:
            raise KeyError(key)
        for field in FIELDS:
            if field in values:
                setattr(row, field, values[field])
    return get(key)


def reset(key: str) -> dict[str, Any]:
    if key not in BUILTIN:
        raise ValueError("Only built-in agents have a default to reset to")
    return update(key, builtin_default(key))


def delete(key: str) -> None:
    with session_scope() as s:
        row = s.get(Agent, key)
        if row is None:
            raise KeyError(key)
        s.delete(row)
