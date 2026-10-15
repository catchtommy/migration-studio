"""The agent library, persisted in `agents` and edited from the Agents page. Workflows use agents as stages."""

import hashlib
import json
from typing import Any

from sqlalchemy import inspect, select, text

from app.agents.defaults import AUTO, CLAUDE_MODELS, DOCS, default_config
from app.db.models import Agent, Setting
from app.db.session import get_engine, session_scope
from app.orchestrator.stages import BUILTIN_STAGES

FIELDS = ("title", "description", "mode", "model", "effort", "jdk", "instructions", "tools", "outputs", "max_turns",
          "checks")
# Built-in agents added after the first release, keyed by the settings marker that records they were added once.
LATER_BUILTINS = {"_added_integration_design": ["integration_design"],
                  "_added_discovery_agents": ["bounded_contexts", "domain_features"],
                  "_added_modulith_design_agents": ["domain_dependencies", "domain_design", "wrapper_design",
                                                    "orchestrator_design"]}

# SHA-256 of default instructions from earlier releases. A built-in agent still holding one of these was never edited,
# so it is safe to bring its instructions, required documents, tools and checks up to the current defaults.
PREVIOUS_DEFAULT_HASHES: dict[str, set[str]] = {
    "boundary_design": {"54005c5822e8c7380780ed9bd94127f80e83e35d8733616754436a17334070b5"},
    "deep_dive": {"9a50019f34790f8b4de82b88a85d7d0b5c7d877100d052ad2886fd66252dc4a3"},
    "domain_dependencies": {"e414b29584fc2df4dfd5d5a4bf1b67f2202e015b1bdbc9e992f937e41a7d51cd"},
    "domain_design": {"183bbce289171e4d9b20d7d1506417c2aaf10d85c76f278ae91f80e1cadb6405"},
    "wrapper_design": {"0b7340f3ee69cbe7160f80ba51c4fa93dfb94fadf32e7518ee62b4975f948c2a"},
    "orchestrator_design": {"8ee1b3ec61e2994c53791f681523bed3f44c8861ae4576a7cd6c01a9b509bfe7"},
    "integration_design": {"22b0c4621eb6a97503c878794caed382350840e41c8ee9dd174b6338d7674bf7"},
    "independent_tests": {"d36bccc1f7537976e2b700ce1d597efa5283e7e435cd2aca25aaac50ef889a47"},
    "microservice_scaffold": {"869be47dce9dcbb7f70c95cf4c4ab19d00772b4b0c5851c71c9e23c481319145"},
    "modernise": {"a0185bf6df97033362944d60bf67817711ba448fda661677bec19d9f87f5a457"},
    "modular_monolith": {"07718da8f27f714d734e7edbbad7067e8df6bc5191426931c2b2f8a316ff1dee",
                         "a2faa355ff82f2ded46ea8c43c9f551c16ff2e09a9f5ae0af29931bfac559025"},
    "monolith_integration": {"08a60fa01d2f984772a8098e024a65cf5d680316227473e39125c87c0ac8b791"},
}
UPGRADE_MARKER = "_upgraded_builtin_defaults_v4"
BUILTIN = tuple(BUILTIN_STAGES)


def _as_dict(row: Agent) -> dict[str, Any]:
    return {"key": row.key, **{f: getattr(row, f) for f in FIELDS}, "checks": row.checks or [],
            "builtin": row.key in BUILTIN,
            "created_at": row.created_at, "updated_at": row.updated_at}


def builtin_default(key: str) -> dict[str, Any]:
    stage = BUILTIN_STAGES[key]
    return {"title": stage.title, "description": stage.description, **default_config(key)}


def custom_default(key: str, title: str, description: str) -> dict[str, Any]:
    return {
        "title": title, "description": description, "mode": "stub", "model": default_config("deep_dive")["model"],
        "effort": "high", "jdk": "legacy", "tools": list(DOCS), "outputs": [f"{key}.md"], "max_turns": 80, "checks": [],
        "instructions": f"You are the {title} agent.\n\nDescribe what this agent should do, which code and documents "
                        "it should read, and what it must produce.",
    }


def _legacy_configs() -> dict[str, dict]:
    """Per-stage configs saved before agents became a library (table `agent_configs`)."""
    engine = get_engine()
    if not inspect(engine).has_table("agent_configs"):
        return {}
    with engine.connect() as conn:
        return {row["stage"]: dict(row) for row in conn.execute(text("SELECT * FROM agent_configs")).mappings()}


def seed() -> None:
    """On first start, create the built-in agents, keeping choices saved under older versions."""
    with session_scope() as s:
        if s.scalar(select(Agent).limit(1)) is not None:
            _models_to_auto(s)
            _add_later_builtins(s)
            _upgrade_untouched_builtins(s)
            return
        for marker in [*LATER_BUILTINS, UPGRADE_MARKER]:  # a fresh database gets the current built-ins below
            s.add(Setting(key=marker, value=True))
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


def _add_later_builtins(s) -> None:
    """Once per release: add built-in agents introduced since the database was created. A built-in the user deleted
    afterwards is not re-added."""
    for marker, keys in LATER_BUILTINS.items():
        if s.get(Setting, marker) is not None:
            continue
        for key in keys:
            if s.get(Agent, key) is None:
                s.add(Agent(key=key, **builtin_default(key)))
        s.add(Setting(key=marker, value=True))


def _upgrade_untouched_builtins(s) -> None:
    """Once: built-ins never edited since an earlier release get the current defaults. Mode, model, effort, JDK and
    max turns are kept; edited agents are left alone."""
    if s.get(Setting, UPGRADE_MARKER) is not None:
        return
    for key, hashes in PREVIOUS_DEFAULT_HASHES.items():
        row = s.get(Agent, key)
        if row is not None and hashlib.sha256(row.instructions.encode()).hexdigest() in hashes:
            current = default_config(key)
            row.instructions, row.outputs = current["instructions"], current["outputs"]
            row.tools, row.checks = current["tools"], current["checks"]
    s.add(Setting(key=UPGRADE_MARKER, value=True))


def _models_to_auto(s) -> None:
    """Once: agents created with the old Claude-only defaults let the AI provider choose instead, since Copilot
    model ids differ."""
    if s.get(Setting, "_migrated_models_to_auto") is not None:
        return
    for agent in s.scalars(select(Agent).where(Agent.model.in_(CLAUDE_MODELS))):
        agent.model = AUTO
    s.add(Setting(key="_migrated_models_to_auto", value=True))


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
