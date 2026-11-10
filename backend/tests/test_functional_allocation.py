"""The functional allocation: where each functionality of a feature goes (domain, orchestrator, event handler, another
domain, the UI), written by the dependency step, approved and possibly amended by a reviewer, and followed by the
design; and the design's two APIs (the domain's and the orchestrator's)."""

import itertools

import pytest

from app import contracts_store as cs
from app import functional_spec as fs
from app import modulith_scope
from tests.conftest import approve, start_run, wait_for
from tests.test_agents_domains import ctx  # noqa: F401  (fixture)

ALLOCATION = """feature: update-bank-account
domain: client
functionalities:
  - id: F1
    name: Update the client's bank account
    requirements: [FR-001, BR-001]
    placement: domain
    owner: client
    reason: Writes only CLIENT_BANK
  - id: F2
    name: Add a note to every loan of the client
    requirements: [FR-002]
    placement: event-handler
    owner: loan
    event:
      name: client.bank-account-changed
      triggered_from: domain
      publisher: client
      handler: Fetches the client's loans and adds a note to each
      delegates_to: ["com.acme.loan.LoanNoteService#addNote"]
    depends_on: [F1]
    reason: A side effect in loan nobody waits for
"""

DEPENDENCIES = """feature: update-bank-account
domain: client
decision: {scope: cross-domain, reason: Notes on loans}
dependencies:
  - domain: loan
    relation: writes-data
    direction: outbound
    strength: side-effect
    touchpoints: [{kind: dao, ref: LoanDao#insertNote, owner: loan}]
"""

SPEC = """# Functional specification: update-bank-account
## 5. Functional requirements
| ID | The system shall … | Criticality | Rules | Other party |
|---|---|---|---|---|
| FR-001 | update the bank account | high | BR-001 | - |
| FR-002 | note the change on every loan | high | - | loan |
## 6. Business rules
| ID | Rule | When it applies | Outcome |
|---|---|---|---|
| BR-001 | An IBAN must be valid | On save | Rejected |
## 9. Messages and outcomes
| ID | When | What the user sees (exact text) |
|---|---|---|
| MSG-01 | BR-001 fails | Invalid IBAN |
"""


def _write(tmp_path, **files):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def _bad(text: str, change: str, by: str) -> str:
    assert change in text
    return text.replace(change, by)


def test_the_example_parses_and_publishes_from_the_domain():
    a = cs.parse_functional_allocation(ALLOCATION)
    assert [cs.allocation_owner(f) for f in a["functionalities"]] == ["domain", "domain"]
    assert not cs.has_orchestrator(a)


@pytest.mark.parametrize("change,by,message", [
    ("placement: domain", "placement: microservice", "must be one of"),
    ("  - id: F2", "  - id: F1", "duplicate id"),
    ("    event:\n      name: client.bank-account-changed\n      triggered_from: domain\n      publisher: client\n"
     "      handler: Fetches the client's loans and adds a note to each\n"
     '      delegates_to: ["com.acme.loan.LoanNoteService#addNote"]\n', "", "needs its event"),
    ("triggered_from: domain", "triggered_from: somewhere", "triggered_from"),
    ("depends_on: [F1]", "depends_on: [F9]", "depends_on names F9"),
    # Placements done by this feature's own code belong to this domain; another domain is an event handler's or
    # an other-domain functionality's owner
    ("    placement: domain\n    owner: client", "    placement: domain\n    owner: loan", "this domain's own"),
    # Only a direct call or the orchestrator calls other domains
    ("    placement: domain\n", "    placement: domain\n    calls: [loan]\n", "only a domain-call or orchestrator"),
    ("    placement: domain\n", "    placement: domain-call\n", "names the domains the domain module calls"),
    ("    placement: event-handler\n", "    placement: other-domain\n    via: orchestrator\n",
     "only an event-handler has an event"),
])
def test_invalid_allocations_are_refused(change, by, message):
    with pytest.raises(cs.InvalidDocument, match=message):
        cs.parse_functional_allocation(_bad(ALLOCATION, change, by))


def test_an_event_only_belongs_to_an_event_handler():
    text = _bad(ALLOCATION, "placement: event-handler\n    owner: loan", "placement: domain")
    with pytest.raises(cs.InvalidDocument, match="only an event-handler has an event"):
        cs.parse_functional_allocation(text)


def test_checked_against_the_dependencies_and_the_specification(tmp_path):
    _write(tmp_path, **{cs.DOMAIN_DEPENDENCIES_FILE: DEPENDENCIES, fs.SPEC_FILE: SPEC,
                        cs.FUNCTIONAL_ALLOCATION_FILE: ALLOCATION})
    assert cs.validate_outputs(tmp_path, {cs.FUNCTIONAL_ALLOCATION_FILE}) == []

    # A domain the feature isn't related to, and a requirement left out
    text = _bad(_bad(ALLOCATION, "owner: loan", "owner: payments"), "requirements: [FR-002]", "requirements: []")
    _write(tmp_path, **{cs.FUNCTIONAL_ALLOCATION_FILE: text})
    problems = cs.validate_outputs(tmp_path, {cs.FUNCTIONAL_ALLOCATION_FILE})
    assert any("owner payments is neither this domain nor" in p for p in problems)
    assert any("FR-002 of functional-spec.md not allocated" in p for p in problems)


def test_another_domains_event_handler_names_what_does_the_work_at_monolith(tmp_path):
    """At monolith the event is published in-process only: without delegates_to nothing would do the work."""
    unnamed = _bad(ALLOCATION, '      delegates_to: ["com.acme.loan.LoanNoteService#addNote"]\n', "")
    _write(tmp_path, **{cs.DOMAIN_DEPENDENCIES_FILE: DEPENDENCIES, cs.FUNCTIONAL_ALLOCATION_FILE: unnamed})
    assert any("F2 name no delegates_to" in p for p in cs.validate_outputs(tmp_path, {cs.FUNCTIONAL_ALLOCATION_FILE}))
    agreed = _bad(ALLOCATION, '      delegates_to: ["com.acme.loan.LoanNoteService#addNote"]\n',
                  "      no_temporary_handler: The user accepts that loan notes are not written until loan is live\n")
    _write(tmp_path, **{cs.FUNCTIONAL_ALLOCATION_FILE: agreed})
    assert cs.validate_outputs(tmp_path, {cs.FUNCTIONAL_ALLOCATION_FILE}) == []
    both = _bad(ALLOCATION, '      delegates_to: ["com.acme.loan.LoanNoteService#addNote"]\n',
                '      delegates_to: ["com.acme.loan.LoanNoteService#addNote"]\n      no_temporary_handler: x\n')
    with pytest.raises(cs.InvalidDocument, match="not both"):
        cs.parse_functional_allocation(both)


def test_the_monolith_subscribes_every_temporary_handler(tmp_path):
    decisions = """decisions:
  - {interaction: X2, target: loan, kind: side-effect, pattern: event, contract: client.bank-account-changed,
     temporary_handler: {listener: NotesHandler, delegates_to: ['L#addNotes'], remove_when: loan subscribes}}
contracts: []
"""
    _write(tmp_path, **{cs.DECISIONS_FILE: decisions})
    assert any("add NotesHandler to temporary_subscribers" in p
               for p in cs._temporary_subscriber_problems(tmp_path, {"temporary_subscribers": []}))
    subscribed = {"temporary_subscribers": [{"listener": "NotesHandler", "event": "client.bank-account-changed",
                                             "enabled_property": "notes.enabled"}]}
    assert cs._temporary_subscriber_problems(tmp_path, subscribed) == []
    stray = {"temporary_subscribers": [*subscribed["temporary_subscribers"],
                                       {"listener": "Other", "event": "x", "enabled_property": "o"}]}
    assert any("temporary subscriber Other is no event handler (or temporary_handler)" in p
               for p in cs._temporary_subscriber_problems(tmp_path, stray))
    (tmp_path / cs.DECISIONS_FILE).unlink()
    assert cs._temporary_subscriber_problems(tmp_path, stray) == []  # nothing to compare with


def test_an_event_from_the_orchestrator_needs_an_orchestrator(tmp_path):
    text = _bad(ALLOCATION, "triggered_from: domain", "triggered_from: orchestrator")
    _write(tmp_path, **{cs.DOMAIN_DEPENDENCIES_FILE: DEPENDENCIES, cs.FUNCTIONAL_ALLOCATION_FILE: text})
    assert any("no functionality is placed on it" in p
               for p in cs.validate_outputs(tmp_path, {cs.FUNCTIONAL_ALLOCATION_FILE}))


def test_a_domain_only_feature_has_no_orchestrator_functionality(tmp_path):
    deps = DEPENDENCIES.replace("scope: cross-domain", "scope: domain-only").replace(
        "direction: outbound", "direction: inbound")
    text = _bad(ALLOCATION, "placement: domain", "placement: orchestrator")
    _write(tmp_path, **{cs.DOMAIN_DEPENDENCIES_FILE: deps, cs.FUNCTIONAL_ALLOCATION_FILE: text})
    assert any("domain-only" in p for p in cs.validate_outputs(tmp_path, {cs.FUNCTIONAL_ALLOCATION_FILE}))


def test_the_design_follows_the_approved_placement(tmp_path):
    _write(tmp_path, **{cs.FUNCTIONAL_ALLOCATION_FILE: ALLOCATION})
    approved = cs.approved_owners(tmp_path)
    assert approved == {"FR-001": {"domain"}, "BR-001": {"domain"}, "FR-002": {"domain"}}
    table = ("## Functional allocation\n| Id | Owner | Where |\n|---|---|---|\n| FR-001 | domain | API |\n"
             "| BR-001 | domain | API |\n| MSG-01 | domain | API |\n| FR-002 | {owner} | event |\n")
    assert fs.allocation_problems(SPEC, table.format(owner="domain"), "cross-domain", approved) == []
    problems = fs.allocation_problems(SPEC, table.format(owner="orchestrator"), "cross-domain", approved)
    assert problems and "differs from the approved functional-allocation.yaml for FR-002" in problems[0]


DESIGN = """domain: client
feature: update-bank-account
orchestrator_module: {orchestrator}
owned_tables: [CLIENT_BANK]
read_only_tables: []
utility_modules: []
other_domain_dependencies: []
context_ports: []
schema_changes: []
ui_changes: []
stack_changes: []
god_module_dependencies: []
"""
ORCHESTRATOR = "{module: client-orchestrator, artifact: client-orchestrator, base_package: com.acme.orchestrator.client}"


def _api(*paths: str) -> str:
    return "openapi: 3.1.0\ninfo: {title: t, version: '1'}\npaths:\n" + "".join(f"  {p}: {{}}\n" for p in paths)


def test_the_orchestrator_has_its_own_api(tmp_path):
    _write(tmp_path, **{cs.DOMAIN_DESIGN_FILE: DESIGN.format(orchestrator=ORCHESTRATOR),
                        cs.DOMAIN_OPENAPI_FILE: _api("/internal/api/v1/client/bank-accounts")})
    assert any("the design has an orchestrator" in p for p in cs.orchestrator_api_problems(tmp_path))

    _write(tmp_path, **{cs.ORCHESTRATOR_OPENAPI_FILE: _api("/internal/api/v1/client/bank-accounts")})
    assert any("must be under /internal/api/v1/client/orchestrator/" in p for p in cs.orchestrator_api_problems(tmp_path))

    _write(tmp_path, **{cs.ORCHESTRATOR_OPENAPI_FILE: _api("/internal/api/v1/client/orchestrator/bank-accounts")})
    assert cs.orchestrator_api_problems(tmp_path) == []

    # The domain's API holds no orchestrator operation
    _write(tmp_path, **{cs.DOMAIN_OPENAPI_FILE: _api("/internal/api/v1/client/orchestrator/bank-accounts")})
    assert any("belong in orchestrator-openapi.yaml" in p for p in cs.orchestrator_api_problems(tmp_path))


def test_no_orchestrator_needs_no_orchestrator_api(tmp_path):
    _write(tmp_path, **{cs.DOMAIN_DESIGN_FILE: DESIGN.format(orchestrator="none"),
                        cs.DOMAIN_OPENAPI_FILE: _api("/internal/api/v1/client/bank-accounts")})
    assert cs.orchestrator_api_problems(tmp_path) == []


def test_orchestrator_steps_are_not_skipped_when_the_allocation_needs_one(tmp_path):
    design = DESIGN.format(orchestrator="none") + "no_orchestrator_reason: Only publishes the change\n"
    _write(tmp_path, **{cs.DOMAIN_DESIGN_FILE: design, cs.DOMAIN_DEPENDENCIES_FILE: DEPENDENCIES,
                        cs.FUNCTIONAL_ALLOCATION_FILE: ALLOCATION})
    assert modulith_scope.skip_reason("monolith_orchestrator", tmp_path).startswith(
        modulith_scope.NO_ORCHESTRATOR_DESIGNED)
    _write(tmp_path, **{cs.FUNCTIONAL_ALLOCATION_FILE: _bad(ALLOCATION, "placement: domain", "placement: orchestrator")})
    assert modulith_scope.skip_reason("monolith_orchestrator", tmp_path) is None


# --- end to end, with the stub agents -------------------------------------------------------------------------------

_ids = itertools.count()


def test_an_invalid_amendment_blocks_approval_and_a_valid_one_is_kept(client):
    r = start_run(client, {"domain": "loan", "operation": f"fa{next(_ids)}"})
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    run = wait_for(client, run_id, {"awaiting_approval"})
    # Approved with the analysis, before any design step runs
    assert run["pending_gate"]["gate"] == "gate_analysis"
    assert "domain_dependencies" in run["pending_gate"]["reviews"]
    listed = next(a for a in client.get(f"/api/runs/{run_id}/artifacts").json()
                  if a["name"] == cs.FUNCTIONAL_ALLOCATION_FILE)
    allocation = client.get(f"/api/artifacts/{listed['id']}").json()
    parsed = cs.parse_functional_allocation(allocation["content"])
    assert {f["placement"] for f in parsed["functionalities"]} >= {"domain", "orchestrator", "event-handler"}

    client.put(f"/api/artifacts/{allocation['id']}", json={"content": "feature: x\ndomain: loan\nfunctionalities: []\n"})
    r = approve(client, run_id, reviewer="t")
    assert r.status_code == 422 and "functional allocation is invalid" in r.json()["detail"]

    # A placement moved to the domain while its interaction stays unlinked: the design couldn't follow it
    import re as _re
    orphaned = _re.sub(r"placement: orchestrator\n    calls: .*\n    interactions: .*\n", "placement: domain\n",
                       allocation["content"])
    assert orphaned != allocation["content"]
    client.put(f"/api/artifacts/{allocation['id']}", json={"content": orphaned})
    r = approve(client, run_id, reviewer="t")
    assert r.status_code == 422 and "linked by no functionality" in r.json()["detail"]

    amended = allocation["content"] + "    review: {amended: true, comment: checked}\n"
    assert client.put(f"/api/artifacts/{allocation['id']}", json={"content": amended}).status_code == 200
    r = approve(client, run_id, reviewer="t")
    assert r.status_code == 200, r.text
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_design"
    names = {a["name"] for a in client.get(f"/api/runs/{run_id}/artifacts").json()}
    assert {cs.DOMAIN_OPENAPI_FILE, cs.ORCHESTRATOR_OPENAPI_FILE} <= names


def test_another_domains_rule_says_how_it_is_asked():
    text = """feature: f
domain: client
functionalities:
  - {id: F1, name: Fraud check, placement: other-domain, owner: fraud, reason: fraud decides}
"""
    with pytest.raises(cs.InvalidDocument, match="missing via"):
        cs.parse_functional_allocation(text)
    a = cs.parse_functional_allocation(text.replace("owner: fraud", "owner: fraud, via: orchestrator"))
    assert cs.allocation_owner(a["functionalities"][0]) == "other domain" and cs.has_orchestrator(a)
    a = cs.parse_functional_allocation(text.replace("owner: fraud", "owner: fraud, via: domain-call"))
    assert not cs.has_orchestrator(a)


def test_every_outbound_dependency_is_allocated(tmp_path):
    deps = DEPENDENCIES + """  - domain: customer
    relation: calls
    direction: outbound
    strength: hard
    touchpoints: [{kind: service, ref: CustomerService#get, owner: customer}]
"""
    _write(tmp_path, **{cs.DOMAIN_DEPENDENCIES_FILE: deps, cs.FUNCTIONAL_ALLOCATION_FILE: ALLOCATION})
    assert any("no functionality covers the outbound dependency on customer" in p
               for p in cs.validate_outputs(tmp_path, {cs.FUNCTIONAL_ALLOCATION_FILE}))
    asked = ALLOCATION.replace("    placement: domain\n    owner: client",
                               "    placement: orchestrator\n    calls: [customer]")
    _write(tmp_path, **{cs.FUNCTIONAL_ALLOCATION_FILE: asked})
    assert cs.validate_outputs(tmp_path, {cs.FUNCTIONAL_ALLOCATION_FILE}) == []


def test_a_direct_call_only_to_a_domain_the_setting_allows(client, tmp_path):
    """The domain's Direct calls setting: which other domains it may call without an orchestrator."""
    assert client.put("/api/domains/loan", json={"direct_calls": ["nowhere"]}).status_code == 422
    assert client.put("/api/domains/loan", json={"direct_calls": ["loan"]}).status_code == 422  # not itself
    deps = """feature: f
domain: loan
decision: {scope: cross-domain, reason: x}
dependencies:
  - domain: customer
    relation: calls
    strength: hard
    touchpoints: [{kind: service, ref: CustomerService#get, owner: customer}]
"""
    allocation = """feature: f
domain: loan
functionalities:
  - {id: F1, name: Check the customer, placement: domain-call, calls: [customer], reason: inside the rule}
"""
    _write(tmp_path, **{cs.DOMAIN_DEPENDENCIES_FILE: deps, cs.FUNCTIONAL_ALLOCATION_FILE: allocation})
    try:
        assert client.put("/api/domains/loan", json={"direct_calls": []}).status_code == 200
        problems = cs.validate_outputs(tmp_path, {cs.FUNCTIONAL_ALLOCATION_FILE})
        assert any("loan may not call customer directly" in p for p in problems)
        r = client.put("/api/domains/loan", json={"direct_calls": ["customer"]})
        assert r.status_code == 200 and r.json()["direct_calls"] == ["customer"]
        assert cs.validate_outputs(tmp_path, {cs.FUNCTIONAL_ALLOCATION_FILE}) == []
        assert "loan may call customer directly" in cs.catalogue_brief("loan", {})
    finally:
        client.put("/api/domains/loan", json={"direct_calls": []})


def test_a_temporary_handler_names_what_it_delegates_to():
    base = """decisions:
  - interaction: X1
    target: loan
    kind: side-effect
    pattern: event
    contract: client.changed
    temporary_handler: {handler}
contracts:
  - {{key: client.changed, provider: client, kind: event, name: Changed, consumers: [loan]}}
"""
    ok = "{listener: ChangedLoanHandler, delegates_to: ['com.acme.loan.LoanNoteService#add'], remove_when: loan subscribes}"
    assert cs.parse_decisions(base.format(handler=ok))
    with pytest.raises(cs.InvalidDocument, match="delegates_to lists"):
        cs.parse_decisions(base.format(handler=ok.replace("'com.acme.loan.LoanNoteService#add'", "'LoanDao'")))
    with pytest.raises(cs.InvalidDocument, match="missing remove_when"):
        cs.parse_decisions(base.format(handler=ok.replace(", remove_when: loan subscribes", "")))


# --- Lean: integration decisions follow the approved allocation --------------------------------------------------

LINKED = """feature: update-bank-account
domain: client
functionalities:
  - {id: F1, name: Update the account, placement: domain, reason: own tables}
  - {id: F2, name: Check the bank code, placement: domain-call, calls: [bank-directory], interactions: [X1],
     reason: inside the rule}
  - id: F3
    name: Note on every loan
    placement: event-handler
    owner: loan
    interactions: [X2]
    event: {name: client.bank-account-changed, triggered_from: domain, publisher: client, handler: Adds the notes}
    reason: side effect
"""
CROSS = """interactions:
  - {id: X1, direction: outbound, target: bank-directory, kind: query, summary: Bank code}
  - {id: X2, direction: outbound, target: loan, kind: side-effect, summary: Loan notes}
  - {id: X3, direction: inbound, target: broker, kind: query, summary: Broker reads the account}
"""
DECIDED = """decisions:
  - {interaction: X1, target: bank-directory, kind: query, pattern: sync-api, contract: bank-directory.get-bank}
  - {interaction: X2, target: loan, kind: side-effect, pattern: event, contract: client.bank-account-changed}
  - {interaction: X3, direction: inbound, target: broker, kind: query, pattern: sync-api, contract: client.get-account}
contracts: []
"""


def _lean_design(tmp_path, allocation=LINKED, decisions=DECIDED):
    return _write(tmp_path, **{cs.FUNCTIONAL_ALLOCATION_FILE: allocation, cs.CROSS_DOMAIN_FILE: CROSS,
                               cs.DECISIONS_FILE: decisions})


def test_decisions_that_follow_the_allocation_pass(tmp_path):
    assert cs.allocation_decision_problems(_lean_design(tmp_path)) == []  # the inbound X3 is outside the allocation


def test_a_placement_the_reviewer_changed_decides_the_pattern_not_the_kind(tmp_path):
    # The reviewer moved the loan notes to the orchestrator; cross-domain.yaml still says side-effect
    moved = LINKED.replace("placement: event-handler\n    owner: loan\n", "placement: orchestrator\n    calls: [loan]\n") \
        .replace("    event: {name: client.bank-account-changed, triggered_from: domain, publisher: client, handler: "
                 "Adds the notes}\n", "")
    problems = cs.allocation_decision_problems(_lean_design(tmp_path, moved))
    assert any("X2 is decided as event, but F3 is placed on orchestrator" in p for p in problems)
    assert any("X2 decided as events, but no event-handler functionality links them" in p for p in problems)


def test_a_decision_targets_what_the_functionality_calls(tmp_path):
    wrong = DECIDED.replace("{interaction: X1, target: bank-directory", "{interaction: X1, target: customer")
    assert any("X1 targets customer, but F2 calls or is owned by bank-directory" in p
               for p in cs.allocation_decision_problems(_lean_design(tmp_path, decisions=wrong)))


def test_every_linked_interaction_is_decided_and_the_domain_links_no_other_domain(tmp_path):
    missing = DECIDED.replace("  - {interaction: X1, target: bank-directory, kind: query, pattern: sync-api, "
                              "contract: bank-directory.get-bank}\n", "")
    assert any("F2 (domain-call) links X1, which has no decision" in p
               for p in cs.allocation_decision_problems(_lean_design(tmp_path, decisions=missing)))
    domain = LINKED.replace("{id: F1, name: Update the account, placement: domain, reason: own tables}",
                            "{id: F1, name: Update the account, placement: domain, interactions: [X1], reason: own}")
    assert any("F1 is placed on domain, which involves no other domain" in p
               for p in cs.allocation_decision_problems(_lean_design(tmp_path, domain)))


def test_another_domains_rule_is_decided_as_its_via_says(tmp_path):
    asked = LINKED.replace("{id: F2, name: Check the bank code, placement: domain-call, calls: [bank-directory],",
                           "{id: F2, name: Check the bank code, placement: other-domain, owner: bank-directory, "
                           "via: domain-call,")
    saga = DECIDED.replace("pattern: sync-api, contract: bank-directory.get-bank", "pattern: saga, "
                           "contract: bank-directory.get-bank")
    assert cs.allocation_decision_problems(_lean_design(tmp_path, asked)) == []
    assert any("placed on other-domain via domain-call" in p
               for p in cs.allocation_decision_problems(_lean_design(tmp_path, asked, saga)))  # a saga is the orchestrator's


def test_only_the_lean_design_is_held_to_it(ctx):
    from app.agents import common

    c, _, _ = ctx
    c.output_dir.mkdir(parents=True, exist_ok=True)
    _lean_design(c.output_dir, decisions=DECIDED.replace("pattern: event", "pattern: sync-api"))
    for stage, checked in (("modulith_design", True), ("integration_design", False)):
        c.stage = stage
        assert any("against functional-allocation.yaml" in p for p in common.verify(c)) is checked


def test_the_lean_design_is_given_the_approved_allocation_in_full_as_final(ctx):
    from app.agents import common, defaults

    c, _, _ = ctx
    c.output_dir.mkdir(parents=True, exist_ok=True)
    _write(c.output_dir, **{cs.FUNCTIONAL_ALLOCATION_FILE: LINKED})
    c.stage = "modulith_design"
    given = common.brief(c)
    assert "## Approved functional allocation: FINAL" in given and "Check the bank code" in given
    c.stage = "dependency_analysis"  # the step that writes it gets no such section
    assert "Approved functional allocation" not in common.brief(c)
    design = " ".join(defaults.INSTRUCTIONS["modulith_design"].split())
    assert "It is FINAL" in design and "Ask about a placement only for a conflict" in design
    # how a placement really changes: in the allocation, through Change this step on Domain dependencies
    assert '"Change this step" on the Domain dependencies step' in design
    assert "You never change `functional-allocation.yaml` yourself" in design
    assert "Never ask whether a placement is right" in design
    assert "an orchestrator operation only when the allocation places its extra steps on the orchestrator" in design



def test_placements_that_involve_another_domain_name_their_interactions_and_every_outbound_one_is_linked(tmp_path):
    deps = """feature: update-bank-account
domain: client
decision: {scope: cross-domain, reason: x}
dependencies:
  - {domain: bank-directory, relation: calls, strength: hard, touchpoints: [{kind: service, ref: B#get}]}
  - {domain: loan, relation: writes-data, strength: side-effect, touchpoints: [{kind: dao, ref: L#note}]}
"""
    _write(tmp_path, **{cs.DOMAIN_DEPENDENCIES_FILE: deps, cs.CROSS_DOMAIN_FILE: CROSS, cs.FUNCTIONAL_ALLOCATION_FILE: LINKED})
    assert [p for p in cs.validate_outputs(tmp_path, {cs.FUNCTIONAL_ALLOCATION_FILE}) if "interaction" in p] == []
    no_link = LINKED.replace("    interactions: [X2]\n", "")
    _write(tmp_path, **{cs.FUNCTIONAL_ALLOCATION_FILE: no_link})
    problems = cs.validate_outputs(tmp_path, {cs.FUNCTIONAL_ALLOCATION_FILE})
    assert any("F3 (event-handler): name the interactions" in p for p in problems)
    assert any("outbound interaction(s) X2 of cross-domain.yaml are linked by no functionality" in p for p in problems)


def test_an_other_domain_rules_message_belongs_to_the_side_that_asks(tmp_path):
    text = """feature: f
domain: client
functionalities:
  - {id: F1, name: Fraud check, placement: other-domain, owner: fraud, via: orchestrator,
     requirements: [BR-004, MSG-02], reason: fraud decides}
"""
    _write(tmp_path, **{cs.FUNCTIONAL_ALLOCATION_FILE: text})
    assert cs.approved_owners(tmp_path) == {"BR-004": {"other domain"}, "MSG-02": {"orchestrator"}}
    _write(tmp_path, **{cs.FUNCTIONAL_ALLOCATION_FILE: text.replace("via: orchestrator", "via: domain-call")})
    assert cs.approved_owners(tmp_path)["MSG-02"] == {"domain"}


def test_an_invalid_allocation_never_skips_the_orchestrator_steps(tmp_path):
    design = DESIGN.format(orchestrator="none") + "no_orchestrator_reason: Only publishes the change\n"
    _write(tmp_path, **{cs.DOMAIN_DESIGN_FILE: design, cs.DOMAIN_DEPENDENCIES_FILE: DEPENDENCIES,
                        cs.FUNCTIONAL_ALLOCATION_FILE: "functionalities: not a list\n"})
    assert modulith_scope.skip_reason("monolith_orchestrator", tmp_path) is None


# --- Group 2: the code steps build what the allocation decides ------------------------------------------------------

ENTRY_CONTRACTS = """feature: update-bank-account
domain: client
contracts:
  - key: loan.add-notes
    provider: loan
    kind: command
    name: Add notes to the client's loans
    spec: contracts/loan.add-notes.yaml
    binding:
      delegates_to: ['com.acme.loan.L#addNotes']
      remove_when: loan provides it
      entry_point:
        operations:
          - {method: addNotes, delegates_to: ['com.acme.loan.L#addNotes']}
"""
NAMED = LINKED.replace("handler: Adds the notes}", "handler: Adds the notes, delegates_to: ['com.acme.loan.L#addNotes']}")
HANDLED = DECIDED.replace("contract: client.bank-account-changed}",
                          "contract: client.bank-account-changed, event_handler: "
                          "{domain: loan, calls: [loan.add-notes], remove_when: loan subscribes}}")


def _with_entry_points(out, contracts=ENTRY_CONTRACTS):
    (out / cs.LINKED_CONTRACTS_FILE).write_text(contracts, encoding="utf-8")
    return out


def test_an_event_handler_exactly_when_the_allocation_names_what_does_the_work_today(tmp_path):
    assert cs.allocation_decision_problems(_with_entry_points(_lean_design(tmp_path, NAMED, HANDLED))) == []
    assert any("X2 needs an event_handler" in p
               for p in cs.allocation_decision_problems(_with_entry_points(_lean_design(tmp_path, NAMED))))
    assert any("X2 has an event_handler, but F3's event names no delegates_to" in p
               for p in cs.allocation_decision_problems(_with_entry_points(_lean_design(tmp_path, decisions=HANDLED))))
    elsewhere = HANDLED.replace("{domain: loan,", "{domain: client,")
    assert any("is in client's package, but F3 is done by loan" in p for p in cs.allocation_decision_problems(
        _with_entry_points(_lean_design(tmp_path, NAMED, elsewhere))))


def test_an_event_handler_reaches_the_work_only_through_entry_points(tmp_path):
    bare = ENTRY_CONTRACTS.split("      entry_point:")[0]
    assert any("calls loan.add-notes, which has no entry point" in p for p in cs.allocation_decision_problems(
        _with_entry_points(_lean_design(tmp_path, NAMED, HANDLED), bare)))
    more = NAMED.replace("delegates_to: ['com.acme.loan.L#addNotes']",
                         "delegates_to: ['com.acme.loan.L#addNotes', 'com.acme.loan.L#flag']")
    assert any("no entry point it calls reaches com.acme.loan.L#flag" in p for p in cs.allocation_decision_problems(
        _with_entry_points(_lean_design(tmp_path, more, HANDLED))))
    unknown = HANDLED.replace("calls: [loan.add-notes]", "calls: [loan.other]")
    assert any("calls loan.other, which is not a contract in" in p for p in cs.allocation_decision_problems(
        _with_entry_points(_lean_design(tmp_path, NAMED, unknown))))


def test_a_lean_design_replaces_the_temporary_handler(tmp_path):
    old = DECIDED.replace("contract: client.bank-account-changed}",
                          "contract: client.bank-account-changed, temporary_handler: "
                          "{listener: NotesHandler, delegates_to: ['com.acme.loan.L#addNotes'], remove_when: x}}")
    assert any("X2 has a temporary_handler" in p and "replace it" in p for p in cs.allocation_decision_problems(
        _with_entry_points(_lean_design(tmp_path, NAMED, old))))


def test_each_build_step_is_expected_to_build_its_entry_points_and_handlers(tmp_path):
    out = _with_entry_points(_lean_design(tmp_path, NAMED, HANDLED))
    assert cs.expected_entry_points(out) == [{"key": "loan.add-notes", "provider": "loan", "operations": [
        {"method": "addNotes", "delegates_to": ["com.acme.loan.L#addNotes"]}],
        "delegates_to": ["com.acme.loan.L#addNotes"], "reused": False}]
    assert cs.expected_event_handlers(out, "domain") == [
        {"event": "client.bank-account-changed", "domain": "loan", "calls": ["loan.add-notes"],
         "delegates_to": ["com.acme.loan.L#addNotes"], "interaction": "X2"}]
    assert cs.expected_event_handlers(out, "orchestrator") == []
    from_orchestrator = NAMED.replace("triggered_from: domain", "triggered_from: orchestrator")
    out = _with_entry_points(_lean_design(tmp_path, from_orchestrator, HANDLED))
    assert [h["event"] for h in cs.expected_event_handlers(out, "orchestrator")] == ["client.bank-account-changed"]
    # An older design (no entry point, no event handler): nothing to compare with
    (out / cs.LINKED_CONTRACTS_FILE).unlink()
    (out / cs.DECISIONS_FILE).write_text(DECIDED, encoding="utf-8")
    assert cs.expected_entry_points(out) is None and cs.expected_event_handlers(out, "domain") is None


def test_the_monolith_subscribes_every_event_handler(tmp_path):
    out = _with_entry_points(_lean_design(tmp_path, NAMED, HANDLED))
    assert any("add LoanClientBankAccountChangedHandler to temporary_subscribers" in p
               for p in cs._temporary_subscriber_problems(out, {"temporary_subscribers": []}))
    routing = cs.parse_integration_routing(
        "feature: f\ndomain: client\ntarget: domain-service\napi: openapi.yaml\nrouter: R.java\n"
        "toggle: {property: p, default: legacy}\noperations: [{operation_id: a, monolith: M#a, client: C#a}]\n"
        "temporary_subscribers: [{handler: LoanClientBankAccountChangedHandler, event: client.bank-account-changed, "
        "enabled_property: e}]\n")
    assert cs._temporary_subscriber_problems(out, routing) == []


def test_a_design_gives_every_binding_an_entry_point(tmp_path):
    out = _with_entry_points(_lean_design(tmp_path, NAMED, HANDLED))
    assert cs.entry_point_problems(out) == []
    _with_entry_points(out, ENTRY_CONTRACTS.split("      entry_point:")[0])
    assert any("loan.add-notes has a temporary binding but no entry_point" in p for p in cs.entry_point_problems(out))


@pytest.mark.parametrize("entry_point,message", [
    ("{operations: []}", "list the operations"),
    ("{operations: [{method: addNotes, delegates_to: ['com.acme.loan.L#other']}]}", "is not in the binding"),
    ("{api: Wrong, operations: [{method: addNotes, delegates_to: ['com.acme.loan.L#addNotes']}]}",
     "is LoanAddNotesApi"),
])
def test_an_entry_point_is_checked_when_parsed(entry_point, message):
    text = ENTRY_CONTRACTS.split("      entry_point:")[0] + f"      entry_point: {entry_point}\n"
    with pytest.raises(cs.InvalidDocument, match=message):
        cs.parse_linked_domain_contracts(text)


def test_each_build_step_is_expected_to_build_the_handlers_of_the_events_it_publishes(tmp_path):
    named = LINKED.replace("handler: Adds the notes}", "handler: Adds the notes, delegates_to: ['L#addNotes']}")
    handled = DECIDED.replace("contract: client.bank-account-changed}",
                              "contract: client.bank-account-changed, temporary_handler: "
                              "{listener: NotesHandler, delegates_to: ['L#addNotes'], remove_when: loan subscribes}}")
    out = _lean_design(tmp_path, named, handled)
    assert cs.expected_temporary_handlers(out, "domain") == [
        {"listener": "NotesHandler", "delegates_to": ["L#addNotes"], "interaction": "X2"}]
    assert cs.expected_temporary_handlers(out, "orchestrator") == []
    # Published from the orchestrator: the Orchestrator in monolith step builds it
    from_orchestrator = named.replace("triggered_from: domain", "triggered_from: orchestrator")
    out = _lean_design(tmp_path, from_orchestrator, handled)
    assert [h["listener"] for h in cs.expected_temporary_handlers(out, "orchestrator")] == ["NotesHandler"]
    assert cs.expected_temporary_handlers(out, "domain") == []
    (out / cs.DECISIONS_FILE).unlink()
    assert cs.expected_temporary_handlers(out, "domain") is None  # nothing to compare with


@pytest.mark.parametrize("handlers,message", [
    ("temporary_handlers: {listener: H}", "temporary_handlers must be a list"),
    ("temporary_handlers: [{listener: H}]", "missing"),
    ("temporary_handlers: [{listener: a.b.H, delegates_to: ['a.B#m']}]", "listener is the helper's class name"),
    ("temporary_handlers: [{listener: H, delegates_to: [B]}]", "package.Class#method"),
    ("temporary_handlers: [{listener: H, delegates_to: ['a.B#m']}, {listener: H, delegates_to: ['a.B#n']}]",
     "duplicate listener H"),
])
def test_a_declared_temporary_handler_names_its_class_and_what_it_calls(handlers, message):
    text = f"""feature: create-broker
domain: broker
scope: domain-only
modules: [{{path: broker-domain, kind: domain, artifact: "com.acme:broker-domain"}}]
base_package: com.acme.broker
bridge_dir: web/src/main/java/com/acme/broker/legacybridge
routing:
  property: migration.broker.create-broker.route
  default: legacy
  router: web/src/main/java/com/acme/broker/legacybridge/BrokerServiceRouter.java
  routes: [{{bean: brokerService, legacy_bean: brokerServiceLegacy, interface: com.acme.broker.BrokerService,
            module: com.acme.broker.createbroker.BrokerService}}]
"""
    good = cs.parse_module_extraction(text + "temporary_handlers: [{listener: H, delegates_to: ['a.B#m']}]\n")
    assert good["temporary_handlers"] == [{"listener": "H", "delegates_to": ["a.B#m"]}]
    assert cs.parse_module_extraction(text)["temporary_handlers"] == []
    with pytest.raises(cs.InvalidDocument, match=message):
        cs.parse_module_extraction(text + handlers + "\n")


def test_the_code_steps_are_told_what_the_allocation_means_for_them():
    from app.agents import defaults

    flat = lambda key: " ".join((defaults.INSTRUCTIONS[key] + " " + defaults.GUARDRAILS.get(key, "")).split())  # noqa: E731
    module = flat("modular_monolith")
    assert "`functional-allocation.yaml` (where each functionality goes)" in module
    assert "split the copy exactly there" in module  # the one exception to copying with the package line changed
    assert "keeps the legacy bean for `monolith` too, until the Orchestrator in monolith step" in module
    assert "the same adapter, after the commit, calls the handler interface" in module
    assert "the Orchestrator in monolith step builds them next, on the same entry points" in module
    assert "The domains not migrated yet." in module  # the integration and event-handler modules
    orchestrator = flat("monolith_orchestrator")
    assert "each port to another domain that the orchestrator calls" in orchestrator
    assert "Events the design publishes from the orchestrator" in orchestrator
    assert "implements the entry point interface" in orchestrator
    integration = flat("monolith_integration")
    assert "Each facade endpoint implements its contract's spec" in integration
    assert "calls the same handler interface (in the event-handler module)" in integration
    assert "except splitting a method at the steps the approved design gives to the orchestrator" in \
        defaults._MODULITH_GUARDRAILS


def test_the_handling_domain_learns_of_the_temporary_handlers_calling_it(client):
    """The brief of the handling domain lists the other features' temporary handlers calling it, and which the
    monolith subscribes to Service Bus (with the property that switches each off)."""
    import yaml

    from app import projects
    from app.db.models import Artifact
    from app.db.session import session_scope
    from tests.conftest import start_run, wait_for
    from tests.test_discovery import _domain

    publisher, handler = _domain(client, "pub"), _domain(client, "hdl")
    runs = {}
    for domain in (publisher, handler):
        runs[domain] = start_run(client, {"domain": domain, "operation": "f"}).json()["id"]
        wait_for(client, runs[domain], {"awaiting_approval", "awaiting_branches", "awaiting_commit", "failed"}, 30)
    event = f"{publisher}.changed"
    decisions = {"decisions": [{"interaction": "X2", "target": handler, "kind": "side-effect", "pattern": "event",
                                "contract": event, "temporary_handler": {"listener": "ChangedHandler",
                                                                         "delegates_to": ["a.HService#on"],
                                                                         "remove_when": "x"}}], "contracts": []}
    routing = {"temporary_subscribers": [{"listener": "ChangedHandler", "event": event,
                                          "enabled_property": "changed.enabled"}]}
    with projects.use(projects.DEFAULT), session_scope() as s:
        for name, content in ((cs.DECISIONS_FILE, decisions), (cs.INTEGRATION_ROUTING_FILE, routing)):
            s.add(Artifact(run_id=runs[publisher], stage="design", attempt=1, name=name, path=f"d/{name}",
                           kind="yaml", content=yaml.safe_dump(content)))
    with projects.use(projects.DEFAULT), session_scope() as s:
        found = cs.temporary_handlers_calling(s, projects.DEFAULT, handler)
    assert [(h["listener"], h["event"], h["subscribed"]) for h in found] == [("ChangedHandler", event, True)]
    brief = cs.catalogue_brief(handler, {})
    assert "ChangedHandler for `" + event + "`" in brief and "switched by `changed.enabled`" in brief
    assert "ChangedHandler" not in cs.catalogue_brief(publisher, {})


def test_a_standard_design_gives_its_event_handlers_the_same_rules(tmp_path):
    """The Standard workflow's integration design isn't held to the Lean allocation check, but its event handlers are."""
    out = _with_entry_points(_lean_design(tmp_path, NAMED, HANDLED))
    assert cs.event_handler_decision_problems(out) == []
    assert any("X2 needs an event_handler" in p
               for p in cs.event_handler_decision_problems(_with_entry_points(_lean_design(tmp_path, NAMED))))
