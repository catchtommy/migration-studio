"""Texts agents share, persisted in `shared_texts` and edited from the Agents page (Shared texts): the preamble every
agent gets before its instructions, the feature-scope rules, and blocks several agents' instructions include (the
legacy stack, the Modulith guardrails, the REST rules, the machine-readable formats, ...).

Instructions include a block with its placeholder, `{{key}}`, filled in from the database when a step starts, so a
change to a block reaches every agent that uses it, edited or not. The texts in app/agents/defaults.py are only the
defaults: the database is seeded with them once (blocks added in later releases are added then), a text nobody has
edited follows its default when a release changes it, and Reset puts one back.
"""

import hashlib
import logging
import re
from typing import Any

from sqlalchemy import select

from app.agents.defaults import SHARED_TEXTS
from app.db.models import GLOBAL, SHARED, Agent, Setting, SharedText
from app.agent_store import _keep_updated_at
from app.db.session import NotConfigured, session_scope

log = logging.getLogger(__name__)
PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
_TEMPLATED_MARKER = "_agents_use_shared_texts"  # existing agents' instructions were given placeholders once

# SHA-256 of default texts from earlier releases. A shared text still holding one of these was never edited, so it is
# safe to bring it up to the current default.
PREVIOUS_DEFAULT_HASHES: dict[str, set[str]] = {
    "domain_architecture": {"55ff46a6b067591ace014c742a3a90e1da82930518191db577ae0158c9284802"},
    "modulith_guardrails": {"cd47ed977ecce9c1a15de5b4984f282c89332c4f04a203af06941579dbf4bea5",
        "4dac012e5480a51b232adb345911da8b44362a2253ceb6110722cefcf2c0d8db"},
    "rest_rules": {"b54eabfb50dbc3fa6638c9e39678892b4304ff9da150d634520049bb3444ba98"},
    "integration_rules": {"4b3eb1458b581abd4d48dbc3c1c691ffce3bcba4268a97f844490928070abbd7"},
    "module_extraction_format": {"d13a3bd1c9a23f1d750dcbe2d06559ee7358edf7e185752934bc9ef41b6b0373",
        "96f46e906618b6ede29b8905653b0c6e33310d3c7e81e0a801c24101d4494e53"},
    "orchestrator_extraction_format": {"ca56b228ce6695a9c1f552788aaea064cee99cc2d457722f887e18fbf2e9f9ea"},
    "integration_routing_format": {"2b149e1029b57ace3b1ed643fb9094d21f34192aa65539015931b27c9342e7d5"},
}


def _as_dict(row: SharedText) -> dict[str, Any]:
    default = SHARED_TEXTS.get(row.key)
    return {"key": row.key, "title": row.title, "description": row.description, "text": row.text,
            "builtin": default is not None, "customised": default is not None and row.text != default[2],
            "used_by": [], "updated_by": row.updated_by, "updated_at": row.updated_at}


def seed() -> None:
    """Add the shared texts the database doesn't have yet, bring the ones never edited up to the current defaults,
    then (once) give existing agents' instructions placeholders wherever they still hold a block's default text word
    for word, so later changes to the block reach them."""
    from app.agents.defaults import templated

    added, upgraded, kept = [], [], []
    with session_scope() as s:
        have = {r.key: r for r in s.scalars(select(SharedText).where(SharedText.project == SHARED))}
        for key, (title, description, text) in SHARED_TEXTS.items():
            row = have.get(key)
            if row is None:
                s.add(SharedText(project=SHARED, key=key, title=title, description=description, text=text))
                added.append(title)
            elif row.text == text:
                if row.description != description:
                    _keep_updated_at(row)
                    row.description = description
            elif row.updated_by is None or \
                    hashlib.sha256((row.text or "").encode()).hexdigest() in PREVIOUS_DEFAULT_HASHES.get(key, ()):
                # nobody edited it (every edit records who), or it is a known earlier default: it follows the default
                _keep_updated_at(row)  # a release's upgrade, not an edit: runs don't report their agents as changed
                row.text, row.description = text, description
                upgraded.append(title)
            else:
                kept.append(title)  # edited: a newer default is available (Reset takes it)
        if s.get(Setting, (GLOBAL, _TEMPLATED_MARKER)) is None:
            for agent in s.scalars(select(Agent)):
                agent.instructions = templated(agent.instructions or "")
            s.add(Setting(key=_TEMPLATED_MARKER, value=True))
    for what, titles in (("added", added), ("upgraded to this release's default", upgraded),
                         ("kept as edited (a newer default is available; Reset takes it)", kept)):
        if titles:
            log.info("Shared texts %s: %s", what, ", ".join(titles))


def texts() -> dict[str, str]:
    """key -> text, from the database (the defaults when no database is open, e.g. in unit tests)."""
    try:
        with session_scope() as s:
            stored = {r.key: r.text for r in s.scalars(select(SharedText).where(SharedText.project == SHARED))}
    except NotConfigured:
        stored = {}
    return {**{k: v[2] for k, v in SHARED_TEXTS.items()}, **stored}


def text(key: str) -> str:
    return texts()[key]


def expand(instructions: str, values: dict[str, str] | None = None) -> str:
    """Instructions with every placeholder of a known shared text filled in; unknown ones are left as they are."""
    values = texts() if values is None else values
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), instructions)


def template(instructions: str) -> str:
    """The reverse of expand, for instructions read from an agent.md file (which has the shared texts written out):
    each block's current text, where it appears word for word, is put back as its placeholder."""
    values = texts()
    for key, value in sorted(values.items(), key=lambda kv: -len(kv[1])):
        if key not in ("preamble", "feature_rules") and value:
            instructions = instructions.replace(value, "{{" + key + "}}")
    return instructions


def unknown(instructions: str) -> list[str]:
    """Placeholders in the instructions that name no shared text."""
    known = texts()
    return sorted({m.group(1) for m in PLACEHOLDER.finditer(instructions)} - set(known))


def list_all() -> list[dict[str, Any]]:
    order = {k: i for i, k in enumerate(SHARED_TEXTS)}
    with session_scope() as s:
        rows = list(s.scalars(select(SharedText).where(SharedText.project == SHARED)))
        out = [_as_dict(r) for r in sorted(rows, key=lambda r: (order.get(r.key, len(order)), r.key))]
        agents = list(s.scalars(select(Agent)))
    from app import domain_modules
    # Texts the studio adds to a step's prompt itself (not through a placeholder in the instructions)
    given = {"domain_architecture": {*domain_modules.STEPS, *domain_modules.DESIGN_STEPS},
             "lift_rules": {"modernise", "modernise_and_test"}}
    for item in out:
        token = item["key"]
        item["used_by"] = sorted({a.title for a in agents if a.key in given.get(token, ()) or any(
            m.group(1) == token for m in PLACEHOLDER.finditer(f"{a.instructions or ''}\n{a.guardrails or ''}"))})
    return out


def get(key: str) -> dict[str, Any]:
    item = next((t for t in list_all() if t["key"] == key), None)
    if item is None:
        raise KeyError(key)
    return item


DATA = re.compile(r"\{\{\s*data\.([a-z_]+)\s*\}\}")  # run data the studio fills in (not another shared text)


def fill(key: str, data: dict[str, str]) -> str:
    """A shared text with the run's data filled in ({{data.name}}); a name it doesn't know is left out."""
    return DATA.sub(lambda m: str(data.get(m.group(1), "")), text(key))


def update(key: str, value: str, by: str | None = None) -> dict[str, Any]:
    if PLACEHOLDER.search(value):
        raise ValueError("A shared text can't include another one ({{...}}); write its text out instead")
    from app.agents.defaults import SHARED_TEXT_DATA, SHARED_TEXT_FILLED
    lost = [k for k in SHARED_TEXT_DATA.get(key, ()) if k not in DATA.findall(value)]
    if lost:
        raise ValueError("Keep " + ", ".join("{{data.%s}}" % k for k in lost) + ": the studio fills in the run's data "
                         "there, and the agents would go without it")
    filled = SHARED_TEXT_FILLED.get(key, ())
    unknown = sorted({k for k in DATA.findall(value) if k not in filled})
    if unknown:
        raise ValueError(", ".join("{{data.%s}}" % k for k in unknown) + ": the studio fills in no such data here ("
                         + (", ".join("{{data.%s}}" % k for k in filled) if filled else "this text takes none") + ")")
    with session_scope() as s:
        row = s.get(SharedText, (SHARED, key))
        if row is None:
            raise KeyError(key)
        row.text, row.updated_by = value, by
    return get(key)


def reset(key: str, by: str | None = None) -> dict[str, Any]:
    """Back to the default, and following the defaults again (as if never edited)."""
    if key not in SHARED_TEXTS:
        raise KeyError(key)
    update(key, SHARED_TEXTS[key][2], by)
    with session_scope() as s:
        s.get(SharedText, (SHARED, key)).updated_by = None
    return get(key)
