"""The agent library, persisted in `agents` and edited from the Agents page. Workflows use agents as stages.

Agents are shared by every project (project SHARED). A project can customise one: it gets its own copy (same key,
project = the project's key), which that project uses instead of the shared one until it is reverted.
"""

import hashlib
import json
from typing import Any

from sqlalchemy import inspect, select, text

from app.agents.defaults import AUTO, CLAUDE_MODELS, DOCS, default_config
from app import projects
from app.db.models import GLOBAL, SHARED, Agent, Setting
from app.db.session import get_engine, session_scope
from app.orchestrator.stages import BUILTIN_STAGES

FIELDS = ("title", "description", "mode", "model", "effort", "jdk", "instructions", "tools", "outputs", "max_turns",
          "checks")
# Built-in agents added after the first release, keyed by the settings marker that records they were added once.
LATER_BUILTINS = {"_added_integration_design": ["integration_design"],
                  "_added_linked_domain_contracts": ["linked_domain_contracts"],
                  "_added_discovery_agents": ["bounded_contexts", "domain_features"],
                  "_added_modulith_design_agents": ["domain_dependencies", "domain_design", "wrapper_design",
                                                    "orchestrator_design"]}

# SHA-256 of default instructions from earlier releases. A built-in agent still holding one of these was never edited,
# so it is safe to bring its instructions, required documents, tools and checks up to the current defaults.
PREVIOUS_DEFAULT_HASHES: dict[str, set[str]] = {
    "boundary_design": {"54005c5822e8c7380780ed9bd94127f80e83e35d8733616754436a17334070b5"},
    "deep_dive": {"9a50019f34790f8b4de82b88a85d7d0b5c7d877100d052ad2886fd66252dc4a3"},
    "domain_dependencies": {"e414b29584fc2df4dfd5d5a4bf1b67f2202e015b1bdbc9e992f937e41a7d51cd",
                              "d2fc147964a2e477c07072900e92b7356898e7613f3274d96dfab97576ec8476"},
    "domain_design": {"183bbce289171e4d9b20d7d1506417c2aaf10d85c76f278ae91f80e1cadb6405",
                              "45ad59b8a91325d3caa86f53505051bda11071e52282340470b7eef5fa0649eb"},
    "wrapper_design": {"0b7340f3ee69cbe7160f80ba51c4fa93dfb94fadf32e7518ee62b4975f948c2a"},
    "orchestrator_design": {"8ee1b3ec61e2994c53791f681523bed3f44c8861ae4576a7cd6c01a9b509bfe7",
                              "b1175e5e93c69bb615256ce4684be01648dd7736bee4955052bbca5be914d9dc"},
    "integration_design": {"22b0c4621eb6a97503c878794caed382350840e41c8ee9dd174b6338d7674bf7",
                              "b53ca7a0466106e58ce2f99daf90e85f88ca28309d8ec35f2a3c0d49186527e5"},
    "independent_tests": {"d36bccc1f7537976e2b700ce1d597efa5283e7e435cd2aca25aaac50ef889a47"},
    "microservice_scaffold": {"869be47dce9dcbb7f70c95cf4c4ab19d00772b4b0c5851c71c9e23c481319145"},
    "modernise": {"a0185bf6df97033362944d60bf67817711ba448fda661677bec19d9f87f5a457"},
    "modular_monolith": {"07718da8f27f714d734e7edbbad7067e8df6bc5191426931c2b2f8a316ff1dee",
                         "a2faa355ff82f2ded46ea8c43c9f551c16ff2e09a9f5ae0af29931bfac559025",
                              "31d5f8f0bbaa53df1978c04257e99924dade1b2b41f230a985b8bd08830d9adc"},
    "monolith_integration": {"08a60fa01d2f984772a8098e024a65cf5d680316227473e39125c87c0ac8b791"},
}
UPGRADE_MARKER = "_upgraded_builtin_defaults_v5"
BUILTIN = tuple(BUILTIN_STAGES)


def _as_dict(row: Agent, customised: bool = False) -> dict[str, Any]:
    return {"key": row.key, **{f: getattr(row, f) for f in FIELDS}, "checks": row.checks or [],
            "builtin": row.key in BUILTIN, "scope": "shared" if row.project == SHARED else "project",
            "customised": customised, "created_at": row.created_at, "updated_at": row.updated_at}


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
            Setting.project == GLOBAL, Setting.key.in_(["agent_mode", "agent_models", "agent_effort"]))}
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
            s.add(Agent(project=SHARED, key=key, **config))


def _add_later_builtins(s) -> None:
    """Once per release: add built-in agents introduced since the database was created. A built-in the user deleted
    afterwards is not re-added."""
    for marker, keys in LATER_BUILTINS.items():
        if s.get(Setting, (GLOBAL, marker)) is not None:
            continue
        for key in keys:
            if s.get(Agent, (SHARED, key)) is None:
                s.add(Agent(project=SHARED, key=key, **builtin_default(key)))
        s.add(Setting(key=marker, value=True))


def _upgrade_untouched_builtins(s) -> None:
    """Once: built-ins never edited since an earlier release get the current defaults. Mode, model, effort, JDK and
    max turns are kept; edited agents are left alone."""
    if s.get(Setting, (GLOBAL, UPGRADE_MARKER)) is not None:
        return
    for key, hashes in PREVIOUS_DEFAULT_HASHES.items():
        row = s.get(Agent, (SHARED, key))
        if row is not None and hashlib.sha256(row.instructions.encode()).hexdigest() in hashes:
            current = default_config(key)
            row.instructions, row.outputs = current["instructions"], current["outputs"]
            row.tools, row.checks = current["tools"], current["checks"]
    s.add(Setting(key=UPGRADE_MARKER, value=True))


def _models_to_auto(s) -> None:
    """Once: agents created with the old Claude-only defaults let the AI provider choose instead, since Copilot
    model ids differ."""
    if s.get(Setting, (GLOBAL, "_migrated_models_to_auto")) is not None:
        return
    for agent in s.scalars(select(Agent).where(Agent.model.in_(CLAUDE_MODELS))):
        agent.model = AUTO
    s.add(Setting(key="_migrated_models_to_auto", value=True))


def _project(project: str | None) -> str | None:
    return project or projects.current_or_none()


def _effective(s, key: str, project: str | None) -> Agent | None:
    """The project's copy of an agent, or the shared one."""
    return (s.get(Agent, (project, key)) if project else None) or s.get(Agent, (SHARED, key))


def list_all(project: str | None = None) -> list[dict[str, Any]]:
    """The agents a project uses: shared ones, replaced by the project's copies, plus its own."""
    project = _project(project)
    with session_scope() as s:
        rows = {r.key: r for r in s.scalars(select(Agent).where(Agent.project == SHARED))}
        shared = set(rows)
        if project:
            for r in s.scalars(select(Agent).where(Agent.project == project)):
                rows[r.key] = r
        order = {k: i for i, k in enumerate(BUILTIN)}
        ordered = sorted(rows.values(), key=lambda r: (order.get(r.key, len(order)), r.created_at))
        return [_as_dict(r, customised=r.project != SHARED and r.key in shared) for r in ordered]


def get(key: str, project: str | None = None) -> dict[str, Any]:
    project = _project(project)
    with session_scope() as s:
        row = _effective(s, key, project)
        if row is None:
            raise KeyError(key)
        return _as_dict(row, customised=row.project != SHARED and s.get(Agent, (SHARED, key)) is not None)


def exists(key: str, project: str | None = None) -> bool:
    with session_scope() as s:
        return _effective(s, key, _project(project)) is not None


def titles(project: str | None = None) -> dict[str, tuple[str, str]]:
    """key -> (title, description) for every agent the project uses."""
    return {a["key"]: (a["title"], a["description"]) for a in list_all(project)}


def _target(scope: str) -> str:
    """Where a change goes: the shared library, or the current project's copies."""
    return SHARED if scope == "shared" else projects.current()


def create(key: str, title: str, description: str, scope: str = "shared") -> dict[str, Any]:
    target = _target(scope)
    with session_scope() as s:
        if s.get(Agent, (SHARED, key)) is not None or s.get(Agent, (target, key)) is not None:
            raise ValueError(f"An agent with key {key} already exists")
        s.add(Agent(project=target, key=key, **custom_default(key, title, description)))
    return get(key)


def update(key: str, values: dict[str, Any], scope: str = "shared") -> dict[str, Any]:
    """Change the shared agent, or the project's copy of it (made from the shared one when there is none yet)."""
    target = _target(scope)
    with session_scope() as s:
        row = s.get(Agent, (target, key))
        if row is None:
            shared = s.get(Agent, (SHARED, key))
            if shared is None:
                raise KeyError(key)
            row = Agent(project=target, key=key, **{f: getattr(shared, f) for f in FIELDS})
            s.add(row)
        for field in FIELDS:
            if field in values:
                setattr(row, field, values[field])
    return get(key)


def customise(key: str) -> dict[str, Any]:
    """Give the current project its own copy of a shared agent."""
    return update(key, {}, scope="project")


def revert(key: str) -> dict[str, Any]:
    """Drop the current project's copy, going back to the shared agent."""
    with session_scope() as s:
        if s.get(Agent, (SHARED, key)) is None:
            raise ValueError("This agent exists only in this project; delete it instead")
        row = s.get(Agent, (projects.current(), key))
        if row is not None:
            s.delete(row)
    return get(key)


def reset(key: str) -> dict[str, Any]:
    if key not in BUILTIN:
        raise ValueError("Only built-in agents have a default to reset to")
    return update(key, builtin_default(key), scope="shared")


def delete(key: str, scope: str = "shared") -> None:
    with session_scope() as s:
        row = s.get(Agent, (_target(scope), key))
        if row is None:
            raise KeyError(key)
        s.delete(row)
        if scope == "shared":  # project copies of a deleted shared agent go too
            s.query(Agent).filter(Agent.key == key).delete(synchronize_session=False)
