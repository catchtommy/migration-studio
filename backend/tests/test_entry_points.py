"""Functionality first: a feature's entry points are every caller of its functionality, whichever domain or system
they start in (client creation from the Loan flow, a partner web service). Analysis finds them with a bounded search;
the design, the module, the services, the tests and the integration carry every one of them to the new API."""

import itertools

import pytest

from app import discovery_store
from app.agents import defaults
from app.contracts_store import InvalidDocument
from app.db.models import Feature
from app.db.session import session_scope
from tests.conftest import start_run

_ids = itertools.count()


def flat(text: str) -> str:
    return " ".join(text.split())


def test_stay_on_this_feature_includes_every_caller_and_keeps_its_bounds():
    rules = flat(defaults.FEATURE_RULES)
    assert "include every caller of its functionality, in any domain or external system" in rules
    assert "Trace each only from its entry point to the call" in rules  # the caller's own logic stays out
    assert "the domain's other features are out of scope" in rules  # kept
    assert "an operation one of this feature's entry points needs (whichever domain it is in) is this feature's" in rules
    assert "external interfaces (web service, message and file formats)" in flat(defaults._FIXED_RULES)
    assert "the code behind them is migrated and routed to the new API like any other entry point" in \
        flat(defaults._FIXED_RULES)


def test_analysis_searches_for_every_caller_within_bounds():
    analysis = flat(defaults.INSTRUCTIONS["analysis"])
    assert "search the monolith for usages of the functionality's own service and DAO methods" in analysis
    assert "stop at the call" in analysis and "more than about 10 distinct entry points" in analysis
    assert "how it calls the functionality (the legacy service's bean, a concrete class" in analysis
    assert '"unused" with the evidence' in analysis
    assert "shares its caller's transaction" in analysis
    assert "Where the functionality's own trace reaches another domain's code" in analysis  # outbound only
    assert "each way the function is started" in flat(defaults.FUNCTIONAL_SPEC_FORMAT)
    assert "has at least one scenario of its own" in flat(defaults.TEST_SCENARIOS_FORMAT)


def test_every_later_step_uses_the_entry_points():
    design = flat(defaults.INSTRUCTIONS["modulith_design"])
    assert '"Entry points": a table' in design and "Never work out the caller from a header" in design
    assert "The router decides per method of the legacy interface, not per caller" in design
    assert "a toggle branch in that caller that calls a router method made for it" in design
    assert "a contract this domain provides to it" in design
    assert "except the contracts this domain provides to other domains' entry points" in design  # domain-only
    assert "the feature isn't switched to its service" in design
    deps = flat(defaults.INSTRUCTIONS["dependency_analysis"])
    assert "Every entry point of another domain" in deps and "never make the feature cross-domain" in deps
    assert "`relation: shares-transaction` instead" in deps
    module = flat(defaults.INSTRUCTIONS["modular_monolith"] + defaults.GUARDRAILS["modular_monolith"])
    assert "must reach the module this way" in module and "Changes in other domains' files" in module
    assert "never in the DAO itself" in module
    assert "other domains' and external entry points use" in flat(defaults.INSTRUCTIONS["microservice_scaffold"])
    assert "with that entry point's inputs" in flat(defaults.INSTRUCTIONS["independent_tests"])
    assert "with that entry point's inputs" in flat(defaults.INSTRUCTIONS["orchestrator_tests"])
    integration = flat(defaults.INSTRUCTIONS["monolith_integration"])
    assert '"Before switching to service"' in integration and "router test for each other domain's" in integration


def _features(used_by: str = "") -> str:
    return f"""features:
  - key: create-client
    name: Create client
    entry_points: [ClientController.create]
    tables: []
    calls: []
{used_by}"""


def test_used_by_is_optional_and_checked(client):
    assert discovery_store.parse_features(_features())[0]["used_by"] == []
    parsed = discovery_store.parse_features(_features(
        "    used_by:\n      - {domain: loan, entry_point: LoanService.create}\n"
        "      - {domain: external, entry_point: ClientWebService.register}\n"))
    assert parsed[0]["used_by"] == [{"domain": "loan", "entry_point": "LoanService.create"},
                                    {"domain": "external", "entry_point": "ClientWebService.register"}]
    with pytest.raises(InvalidDocument, match="not a known domain"):
        discovery_store.parse_features(_features("    used_by:\n      - {domain: nowhere, entry_point: X.y}\n"))


def test_a_features_used_by_reaches_the_runs_brief(client):
    from app.orchestrator import graph

    operation = f"used{next(_ids)}"
    run_id = start_run(client, {"domain": "loan", "operation": operation, "workflow": "lean"}).json()["id"]
    with session_scope() as s:
        row = s.query(Feature).filter(Feature.domain == "loan", Feature.key == operation).one()
        row.used_by = [{"domain": "broker", "entry_point": "BrokerOnboarding.register -> LoanService.create"}]
    brief = graph._inputs(run_id, "analysis").as_markdown()
    assert "Also used by" in brief and "BrokerOnboarding.register -> LoanService.create" in brief
    assert any(f["used_by"] for f in discovery_store.features_of("loan") if f["key"] == operation)


def test_a_re_run_of_the_feature_list_is_shown_the_existing_features(client):
    operation = f"listed{next(_ids)}"
    start_run(client, {"domain": "loan", "operation": operation, "workflow": "lean"})
    brief = discovery_store.domains_brief("loan")
    assert "already listed (keep their keys and grouping)" in brief
    assert f"`{operation}`" in brief and "(being migrated)" in brief
    assert "never a feature of their own" in flat(defaults.INSTRUCTIONS["domain_features"])


def test_an_edited_scoping_text_is_reported_at_startup(client, monkeypatch):
    from app import shared_texts
    from app.db.models import SHARED, SharedText

    warned = []
    monkeypatch.setattr(shared_texts.log, "warning", lambda msg, *a: warned.append(msg % a))
    with session_scope() as s:
        row = s.get(SharedText, (SHARED, "feature_rules"))
        before = (row.text, row.updated_by)
        row.text, row.updated_by = "Our own scoping rules.", "Ada"
    try:
        shared_texts.seed()
        assert warned and "Stay on this feature" in warned[0] and "Reset them" in warned[0]
    finally:
        with session_scope() as s:
            row = s.get(SharedText, (SHARED, "feature_rules"))
            row.text, row.updated_by = before
