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
from app.db.models import GLOBAL, SHARED, Agent, Run, Setting, WorkflowDef
from app.db.session import get_engine, session_scope
from app.orchestrator.stages import BUILTIN_STAGES, SUPERSEDED

FIELDS = ("title", "description", "mode", "model", "effort", "jdk", "instructions", "tools", "outputs", "max_turns",
          "checks", "guardrails")
# Built-in agents added after the first release, keyed by the settings marker that records they were added once.
LATER_BUILTINS = {"_added_integration_design": ["integration_design"],
                  "_added_linked_domain_contracts": ["linked_domain_contracts"],
                  "_added_discovery_agents": ["bounded_contexts", "domain_features"],
                  "_added_modulith_design_agents": ["domain_dependencies", "domain_design",
                                                    "wrapper_design", "orchestrator_design"],
                  "_added_monolith_orchestrator": ["monolith_orchestrator"],
                  "_added_orchestrator_service": ["orchestrator_service"],
                  "_added_service_tests": ["service_tests"],
                  "_added_lean_agents": ["analysis", "modulith_design", "modernise_and_test"]}

# SHA-256 of default instructions from earlier releases. A built-in agent still holding one of these was never edited,
# so it is safe to bring its instructions, required documents, tools and checks up to the current defaults.
PREVIOUS_DEFAULT_HASHES: dict[str, set[str]] = {
    "boundary_design": {"0afe5047252464458ead3aac6b867502a91e1e62835dd59b7b7dcec8fbc8200d",
                        "f0df743aa52f57bfccb67748684cd0004993308476d3c5e538d94a3d9ba0ceea",
        "54005c5822e8c7380780ed9bd94127f80e83e35d8733616754436a17334070b5"},
    "deep_dive": {"a0bf087fe4ccf4df5533d0d01a762c5dfea5e866fd934d0083607cad2c15544e",
                  "9a50019f34790f8b4de82b88a85d7d0b5c7d877100d052ad2886fd66252dc4a3",
                  "e7932db2bbf7b61072ab89b6480fc7d34a7f64ce91db9cb502d3ceb5bbecfdd1"},
    "domain_dependencies": {"d2e6c80aa47483757e655c6c803808e5ebfa2e90872ff8684efe257618cd3e33",
                            "e414b29584fc2df4dfd5d5a4bf1b67f2202e015b1bdbc9e992f937e41a7d51cd",
                              "d2fc147964a2e477c07072900e92b7356898e7613f3274d96dfab97576ec8476",
                            "6909abd6a1c273ef64d44d286532f0171c7c92c3d0cd78939ce8db8610c33d25",
                            "3d453659a94a2f241277ea0f3c3feb349d125fdd97057bb5bc1f1d39bd3dbcab"},
    "domain_design": {"dd198f239be4b92d142930b6e651ac90dadd1dd31d7209a8b43d70ca384b162d",
                      "9c129233aca1bf0a00dd893f4f0c44ab545f0ac5346a75b7222432e398bc2e31",
        "183bbce289171e4d9b20d7d1506417c2aaf10d85c76f278ae91f80e1cadb6405",
                              "45ad59b8a91325d3caa86f53505051bda11071e52282340470b7eef5fa0649eb",
                      "142d8a42236f9e844bcd3445647e9f34787655dece2bc6ccc0b05ec30862e3c8",
                      "5a7035410502576364098df1291b71788fdbe7e10be7644db0a102550a7f8ab7",
                      "e5ac314731ee2cea532d10917f03304365e33b91c1f7358103bbe9d314ddd8db",
        "12b7b3cd2ffc888c21f10cd8fab6141eb9140dd450081f452843ddd0662b1e84"},
    "wrapper_design": {"0b7340f3ee69cbe7160f80ba51c4fa93dfb94fadf32e7518ee62b4975f948c2a",
                       "062dd7634ace2214055e5bcb90218043bcd5f142da5a0d70fb99090d26c10a62"},
    "orchestrator_design": {"fec9b1a759bfbb513210a58472431041d014335317139309281fd8546c42b34a",
                            "44b0788e38d118d0b1a4a7cf2de98d2dbcf3bee334a7885bc8ce8788bac6c365",
                            "35da265d3395dafb7b16aaa1477ff1c3fba106c2c0bc11ca340c8f05da063b62",
                            "81cf9ffa985d209c21d9d32644ff19055335c8fbb6b7941d140ccae39c05d133",
        "8ee1b3ec61e2994c53791f681523bed3f44c8861ae4576a7cd6c01a9b509bfe7",
                              "b1175e5e93c69bb615256ce4684be01648dd7736bee4955052bbca5be914d9dc",
                            "f9e96fafed18fe73fe6e62b9d75184fe99b3c6bdf802d190fed21afb42c35542",
                            "95e1470cf6cff461d1caf1d4e94205c0229b696ee8c852c6bd3acdb7b257a79e",
                            "3e283b3f86a097de59173a64e5068d92ee6fc631799dfaac7c2617f903a91dc9"},
    "linked_domain_contracts": {"082994b819661585759f03856a7188552dd1aec9960aed6d63413ce956b4b5bc",
                                "2f1fde8befb5a2d452aea0a989261f084433a5465e64dced1eb3205a53654609",
        "5d19465bca4616a7f4a6e3581fbadcd30b9707efb9c43e993ddddd96ed8cc464",
        "5ef353e2455182ccc268bfd59999b18449c873a8bb41be559aee177df7c6d941",
                                "7373d95b05eadcacff8638d41bc2da63c8627647d323f824a12159828a41f3d7",
                                "f769606aba568416e0e3892f88a6fd147ab3eeb1ff082d9be446ec6ea3fc9dbf",
                                "7a589e02ca2bb06072c304f2f0d99e47482f4eba4d3a7846a4986cfbaef45a8a"},
    "integration_design": {"17fb32be72076d00361fca9333d38ee203dc223b996db012f277e13b44c8b4e5",
                           "11231d4efa53ca45a9e53af4e52a3666c5258092e03dff6ae17f948ca462ab6d",
        "50062f4c3b3dcc7aab9e4ace195d0134977a341a4a81a22518962ed7f87474c2",
        "22b0c4621eb6a97503c878794caed382350840e41c8ee9dd174b6338d7674bf7",
                              "b53ca7a0466106e58ce2f99daf90e85f88ca28309d8ec35f2a3c0d49186527e5"},
    "independent_tests": {"10597691f0c0608c4b73927a597d8a9e39fdbd6997549ceb0b35976375a005da",
                          "8a7b319eb255ecea335e60322b4e11c207a2c23e3e698999b749ea81f442b27a",
                          "97d564115dc60807860254fee3ecfa541ca999e29cf9870d487a5efe93031f02",
                          "4b1b34967d2944c200b2b4bd8c0a81768cd6292da94cac47c000f9ba30d0de8e",
                          "d36bccc1f7537976e2b700ce1d597efa5283e7e435cd2aca25aaac50ef889a47"},
    "microservice_scaffold": {"d10bb3879c92380cc05bd23f1c91a377749a367067c79bb40d4ad7c4733530e9",
                              "433fa24641180e7ee1a61b9c122707e5088afd85fcfff8238ad9953a37aafe56",
                              "181fe29546e7075f3e007a79554c5c168620b9e23364fea592f1aea97673972c",
                              "775065ebf6599df3036965a91a20399a3bc78702ef5ac7ae7f8b1a107ed59c21",
                              "869be47dce9dcbb7f70c95cf4c4ab19d00772b4b0c5851c71c9e23c481319145",
                              "c2ad8fdcb1f1c9011741b780496fed42b80971e5d67bd0ba1c8a1d1402270ba1",
        "9159384ab952c67a5c905e0f23f2c42d52c30c8e859b4eabe8a3e41090442b56",
        "7356c75d8d7aa56e00af010e64538773939c378a956da6fa58aada1ea347db38",
        "0c42c2258509f9ae8614d72c2986d867f5b7b15aaf6abad4b819fd43f24d24a5"},
    "modernise": {"f5199989f39ae16646acbf79d9c246d424fb9d507aeadc12217647c63b70aa41",
                  "3a97d57409eb272a0311e1bc55d47da3fa1ccf655e303b135937d7e3f8734366",
                  "a0185bf6df97033362944d60bf67817711ba448fda661677bec19d9f87f5a457"},
    "modular_monolith": {"ae6adcee76691ce042b38a21231c497ea4ff3dcf880fa94905f5c0098571bfde",
                         "496c048b744624038c78d0318f8576b486700d5936d2a2b60215dde6e2bdab5a",
                         "07718da8f27f714d734e7edbbad7067e8df6bc5191426931c2b2f8a316ff1dee",
                         "a2faa355ff82f2ded46ea8c43c9f551c16ff2e09a9f5ae0af29931bfac559025",
                              "31d5f8f0bbaa53df1978c04257e99924dade1b2b41f230a985b8bd08830d9adc",
                         "9bf4007e9337fafdab89a901c912245fcd3d44a29989b7732603c7fcada2129b",
                         "164e46fabe89f188ecce8525da9ebda4b9fea4d35b97992f92542414da55c7b9",
                         "db8c731241fbb0878e4b93b12357d93ff4cd2f2160b350346523dcdd883e9765",
        "96dfbf2199cd58075a513f4725fd874e7e8c9affec24e412e666dc32a02e38a8",
        "bf380585492e901711e22a95479416a47e226cbcb962a457bd4c74fca23d02b0",
        "018556caac91c817f3094eb211d8cf971a0d33e110f92bf2741b2a7cf922c134",
        "1ef74b0a61e385d549568080a9464b1330314e6957b8819114716b1d2cb4a52a",
        "5fa33af0b039ab07f2cb732abc0a97fad8bac30609f1aa366c42433f3900cb80",
        "c606bd6e69de66270b01248c20cd160602c92fafc5d0682783ae5998c34c825f"},
    "monolith_integration": {"b41c0cbf33c34303d4822e5c32ece71077a54659ec51cc621870a6e015eb4eaf",
                             "29b5cee315c086c998bba657793a71713eb7f46bf71a98b67fcff29efaa32738",
                             "11fddb537cfb88b83b055c2c32163d278f4d1aa29087d8b9cea2b11a428fd930",
                             "686fec3600f472af2216b6e1805795367b9dd761933e094e3bf424dfbbf8d6b1",
                             "08a60fa01d2f984772a8098e024a65cf5d680316227473e39125c87c0ac8b791",
        "b82822208202ff2dfd10ff0e77918503973178a46c10673641e4a95fad988f2c",
        "e031ab929248d9c9b40ade7b6f9b782f099ad0daefaa39db19e50624a7722887"},
    "as_is_doc": {"9fb0392d1747e6a478a3e0cd6d77ea8cd4564fd9e815959b5baf2f48ee52c892"},
    "bounded_contexts": {"0fe37358d0947d3a11a0fc7d5440eb9ebaa72ec9171caff82a4612112c073294"},
    "monolith_orchestrator": {"35a24835d71b95dc27b3516c23305bb5ba7eb9525e031f7931aa5d1fb4af382e",
        "cae3d71b1aea57fadae64e5ddcf348386475d4f6890dc311606ccbf486e96e47",
        "74d58902f2f8850ab0f40a8ae931b69d0d12745d6b21071d848463d786cfe630",
        "f8d6c72fa43ec08d5245ad37fcd0be1e83d52c32ea37296345ef2751a6fd496b",
        "2e6d1a954a8a63a21f10dbd5bdeec4af00bea1ee01a10142ada6f57fca975129",
        "5b1954381befa4b650c27376281f54dcdd5e53b1b8b844eb308f315dd59e632a"},
    "orchestrator_service": {"84da51f2092f27ab9f264d98062fd5444fa6b35e27b63cb83fed187c824a6fee"},
}
# Built-ins whose step now needs more turns than before: an upgrade raises (never lowers) their limit.
MORE_TURNS = {"modular_monolith", "microservice_scaffold"}
UPGRADE_MARKER = "_upgraded_builtin_defaults_v20"
GUARDRAILS_MARKER = "_builtin_guardrails_v1"  # built-ins got their guardrail tools and checks (_add_guardrails)
GUARDRAILS_MOVED_MARKER = "_guardrails_moved_to_agents"  # global and project guardrails became agents' own
GUARDRAILS_OUT_MARKER = "_guardrails_out_of_instructions"  # built-ins' guardrails moved from instructions to Guardrails
# Built-in agents the Standard migration workflow no longer uses. They stay in the code and the database, and keep
# working in custom workflows and runs that use them, but are not listed on the Agents page or offered in the workflow
# editor unless a workflow uses them.
HIDDEN = tuple(stage.key for stage in SUPERSEDED)
BUILTIN = tuple(BUILTIN_STAGES)


def _as_dict(row: Agent, customised: bool = False) -> dict[str, Any]:
    return {"key": row.key, **{f: getattr(row, f) for f in FIELDS}, "checks": row.checks or [],
            "guardrails": row.guardrails or "",
            "builtin": row.key in BUILTIN, "scope": "shared" if row.project == SHARED else "project",
            "customised": customised, "created_at": row.created_at, "updated_at": row.updated_at}


def builtin_default(key: str) -> dict[str, Any]:
    stage = BUILTIN_STAGES[key]
    return {"title": stage.title, "description": stage.description, **default_config(key)}


def custom_default(key: str, title: str, description: str) -> dict[str, Any]:
    return {
        "title": title, "description": description, "mode": "stub", "model": default_config("deep_dive")["model"],
        "effort": "high", "jdk": "legacy", "tools": list(DOCS), "outputs": [f"{key}.md"], "max_turns": 80, "checks": [],
        "guardrails": "", "instructions": f"You are the {title} agent.\n\nDescribe what this agent should do, which code and documents "
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
            _add_guardrails(s)
            _move_guardrails_to_agents(s)
            _move_guardrails_out_of_instructions(s)
            return
        for marker in [*LATER_BUILTINS, UPGRADE_MARKER, GUARDRAILS_MARKER]:  # a fresh database: the current built-ins
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
        s.flush()
        _move_guardrails_to_agents(s)
        _move_guardrails_out_of_instructions(s)  # settings saved under older versions may hold them


def _changes_code(row: Agent) -> bool:
    """An agent that can change code: the kind the global and project guardrails were given to."""
    return bool({"edit_file", "run_command"} & set(row.tools or []))


def _joined(*texts: str | None) -> str:
    return "\n\n".join(t.strip() for t in texts if t and t.strip())


def _move_guardrails_to_agents(s) -> None:
    """Once: guardrails are kept per agent only. The global guardrails (Agents page) and each project's own (Project
    settings, added to the global ones or in their place) went to every agent whose step changed code; their text is
    moved into the guardrails of each agent that can change code, so every agent keeps the rules it was given:
    the global text into the shared agents (and projects' copies), a project's text into that project's copies (made
    where the project had none). The old settings are then removed."""
    if s.get(Setting, (GLOBAL, GUARDRAILS_MOVED_MARKER)) is not None:
        return
    keys = ("custom_guardrails", "project_guardrails", "project_guardrails_mode")
    rows = list(s.scalars(select(Setting).where(Setting.key.in_(keys))))
    stored = {(r.project, r.key): r.value for r in rows}
    shared_text = str(stored.get((GLOBAL, "custom_guardrails")) or "")
    own = {p: (str(stored.get((p, "project_guardrails")) or ""), stored.get((p, "project_guardrails_mode")) or "add")
           for p, k in stored if k == "project_guardrails" and p != GLOBAL}

    def text_for(project: str, current: str) -> str:
        project_text, mode = own.get(project, ("", "add"))
        if not project_text.strip():
            return _joined(current, shared_text)
        return _joined(current, project_text) if mode == "replace" else _joined(current, shared_text, project_text)

    agents = list(s.scalars(select(Agent)))
    have = {(a.project, a.key) for a in agents}
    coders = [a for a in agents if _changes_code(a)]
    # A project with guardrails of its own gets a copy of each shared agent that changes code, made from the shared
    # agent as it is before the move (its own guardrails only), so the copy's text is the project's effective one.
    copies = [Agent(project=project, key=shared.key, **{f: getattr(shared, f) for f in FIELDS})
              for project, (project_text, _mode) in own.items() if project_text.strip()
              for shared in coders if shared.project == SHARED and (project, shared.key) not in have]
    for row in [*coders, *copies]:
        row.guardrails = _joined(row.guardrails, shared_text) if row.project == SHARED else \
            text_for(row.project, row.guardrails)
    s.add_all(copies)
    for r in rows:
        s.delete(r)
    s.add(Setting(key=GUARDRAILS_MOVED_MARKER, value=True))


def _move_guardrails_out_of_instructions(s) -> None:
    """Once: built-in agents' guardrails (the fixed rules, the Modulith guardrails, the step's hard rules) moved from
    their instructions to their Guardrails. In every copy of a built-in agent, shared or a project's, each guardrail
    section still in the instructions word for word is taken out and put first in its guardrails, so an agent edited
    elsewhere moves too; a section someone changed stays where it is."""
    if s.get(Setting, (GLOBAL, GUARDRAILS_OUT_MARKER)) is not None:
        return
    from app.agents.defaults import GUARDRAIL_SECTION_TEMPLATES, GUARDRAIL_SECTIONS, without

    for row in s.scalars(select(Agent).where(Agent.key.in_(list(GUARDRAIL_SECTIONS)))):
        text = row.instructions or ""
        moved = []
        for stored, written in zip(GUARDRAIL_SECTION_TEMPLATES[row.key], GUARDRAIL_SECTIONS[row.key]):
            # As stored (shared texts as placeholders), or written out where instructions never got placeholders.
            if stored in text:
                moved.append(stored)
            elif written in text:
                moved.append(written)
        if moved:
            row.instructions = without(text, moved)
            row.guardrails = _joined(*moved, row.guardrails)
    s.add(Setting(key=GUARDRAILS_OUT_MARKER, value=True))


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
            row.guardrails = _joined(current["guardrails"], row.guardrails)  # moved out of the instructions
            row.tools, row.checks = current["tools"], current["checks"]
            if key in MORE_TURNS:
                row.max_turns = max(row.max_turns, current["max_turns"])
    s.add(Setting(key=UPGRADE_MARKER, value=True))


def _add_guardrails(s) -> None:
    """Once per release: give every built-in agent its current guardrail tools and checks, edited or not (only added,
    never removed). The checks are enforced anyway (common.required_checks); the tools let the agent check itself."""
    if s.get(Setting, (GLOBAL, GUARDRAILS_MARKER)) is not None:
        return
    for row in s.scalars(select(Agent).where(Agent.key.in_(BUILTIN))):
        current = default_config(row.key)
        guard = [t for t in current["tools"] if t.startswith("check_")]
        row.tools = [*row.tools, *(t for t in guard if t not in row.tools)]
        row.checks = [*(row.checks or []), *(c for c in current["checks"] if c not in (row.checks or []))]
    s.add(Setting(key=GUARDRAILS_MARKER, value=True))


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


def list_all(project: str | None = None, include_hidden: bool = False) -> list[dict[str, Any]]:
    """The agents a project uses: shared ones, replaced by the project's copies, plus its own. Superseded built-ins
    (HIDDEN) are left out unless a workflow still uses them or `include_hidden` is set."""
    project = _project(project)
    with session_scope() as s:
        rows = {r.key: r for r in s.scalars(select(Agent).where(Agent.project == SHARED))}
        shared = set(rows)
        if project:
            for r in s.scalars(select(Agent).where(Agent.project == project)):
                rows[r.key] = r
        if not include_hidden:
            used = {step["key"] for w in s.scalars(select(WorkflowDef)) for step in w.steps
                    if step.get("kind") == "agent"}
            rows = {k: r for k, r in rows.items() if k not in HIDDEN or k in used}
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
    return {a["key"]: (a["title"], a["description"]) for a in list_all(project, include_hidden=True)}


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


def usage(key: str, scope: str = "shared") -> dict[str, Any]:
    """What deleting the agent would affect, and why it can't be deleted when it can't:
    {"title", "workflows": [{"name", "library"}] with a step for it, "runs": [...] unfinished runs whose steps include
    it, "copies": projects with their own copy (deleted with the shared agent), "blocked": the reason, or None}.
    A shared agent is used by every project, so every project's workflows and runs count; a project's own agent, only
    the project's."""
    project = projects.current_or_none()
    with session_scope() as s:
        row = s.get(Agent, (_target(scope), key))
        if row is None:
            raise KeyError(key)
        if scope == "shared":
            workflows = [w for w in s.scalars(select(WorkflowDef))]
        else:  # the project's own workflows and copies, and the shared ones it hasn't copied
            mine = {w.key: w for w in s.scalars(select(WorkflowDef).where(WorkflowDef.project == project))}
            workflows = [*mine.values(), *(w for w in s.scalars(select(WorkflowDef).where(WorkflowDef.project == SHARED))
                                          if w.key not in mine)]
        using = [{"name": w.name, "library": "shared" if w.project == SHARED else w.project} for w in workflows
                 if any(st.get("kind") == "agent" and st.get("key") == key for st in w.steps)]
        runs_query = select(Run).where(Run.status != "completed")
        if scope != "shared":
            runs_query = runs_query.where(Run.project == project)
        runs = [{"id": r.id, "project": r.project, "domain": r.domain, "operation": r.operation, "status": r.status}
                for r in s.scalars(runs_query.order_by(Run.created_at))
                if any(st.get("key") == key for st in r.stage_defs)]
        copies = sorted(a.project for a in s.scalars(select(Agent).where(Agent.key == key, Agent.project != SHARED))) \
            if scope == "shared" else []
        reasons = []
        if using:
            where = ", ".join(w["name"] + (" (shared)" if w["library"] == "shared" else f" (project {w['library']})")
                              for w in using)
            reasons.append(f"it is a step of {len(using)} workflow(s): {where}. Remove its step from each on the "
                           "Workflows page first")
        if runs:
            listed = ", ".join(f"{r['domain']}/{r['operation']} ({r['status'].replace('_', ' ')})" for r in runs[:8])
            more = f" and {len(runs) - 8} more" if len(runs) > 8 else ""
            reasons.append(f"{len(runs)} unfinished run(s) still use it: {listed}{more}. Let them finish, or reset "
                           "their features on the domain's Features tab")
        return {"title": row.title, "workflows": using, "runs": runs, "copies": copies,
                "blocked": "; and ".join(reasons) or None}


def set_mode_all(mode: str, project: str | None = None) -> int:
    """Run every agent the project uses as a stub or with AI (`mode`): the shared library and the project's own agents
    and copies. Returns how many changed."""
    project = _project(project)
    with session_scope() as s:
        rows = s.scalars(select(Agent).where(Agent.project.in_([SHARED, *([project] if project else [])]),
                                             Agent.mode != mode)).all()
        for row in rows:
            row.mode = mode
        return len(rows)


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
