"""Moving features to another domain, new or existing: what the review offers, and what a confirmed move does."""

import pytest
import yaml

from app import discovery_store, maintenance, projects
from app.contracts_store import DOMAIN_DESIGN_FILE, DOMAIN_DESIGN_LOCKED
from app.db.models import Artifact, Contract, Dependency, Domain, Feature, Run
from app.db.session import session_scope
from tests.conftest import wait_for
from tests.test_delivery import as_project, new_project, setup_project


@pytest.fixture
def proj(client):
    """A project of its own, with Loan (three features) and Billing."""
    key = new_project(client)
    p = as_project(client, key)
    p.key = key
    for domain, tables in (("loan", ["LOAN", "SCHEDULE", "RATE", "IDLE"]), ("billing", ["INVOICE"]), ("reports", [])):
        assert p.post("/api/domains", json={"key": domain, "name": domain.capitalize()}).status_code == 201
        assert p.put(f"/api/domains/{domain}", json={"owned_tables": tables,
                                                     "owned_packages": [f"com.old.{domain}"]}).status_code == 200
    add(p, "loan", "approve", tables=["LOAN", "RATE"], entry_points=["LoanController.approve"])
    add(p, "loan", "pay-fee", tables=["SCHEDULE", "RATE"], entry_points=["FeeController.pay"], calls=["billing"])
    add(p, "loan", "close", tables=["LOAN"], entry_points=["LoanController.close"])
    return p


def add(p, domain, key, **fields):
    r = p.post(f"/api/domains/{domain}/features", json={"key": key, "name": key.title(), **fields})
    assert r.status_code == 201, r.text


def body(features, **kw):
    if "target" not in kw and "new_domain" not in kw:
        kw["target"] = "billing"
    return {"features": features, **kw}


def preview(p, features, source="loan", **kw):
    r = p.post(f"/api/domains/{source}/features/move/preview", json=body(features, **kw))
    assert r.status_code == 200, r.text
    return r.json()


def move(p, features, source="loan", expect=200, **kw):
    """As the page does: review it, then confirm what the review shows."""
    review = preview(p, features, source, **kw)
    confirm = {**body(features, **kw), "fingerprint": review["fingerprint"], "acknowledged_calls": True,
               "unticked": [*kw.get("unticked", []), *(f["key"] for f in review["features"] if f["blocked"])],
               "tables": [t["table"] for t in review["tables"] if t["selected"]],
               "packages": [t["package"] for t in review["packages"] if t["selected"]]}
    r = p.post(f"/api/domains/{source}/features/move", json=confirm)
    assert r.status_code == expect, r.text
    return r.json()


def features_of(p, domain):
    return {f["key"]: f for f in p.get(f"/api/domains/{domain}/features").json()}


def owned(p, domain):
    return p.get(f"/api/domains/{domain}").json()["owned_tables"]


def table(review, name):
    return next(t for t in review["tables"] if t["table"] == name)


def set_used_by(p, domain, key, used_by):
    with projects.use(p.key), session_scope() as s:
        row = next(f for f in s.query(Feature).filter_by(project=p.key, domain=domain, key=key))
        row.used_by = used_by


def started(p, domain, key):
    r = p.post("/api/runs", json={"domain": domain, "operation": key})
    assert r.status_code == 201, r.text
    return wait_for(p, r.json()["id"], {"awaiting_approval", "awaiting_branches", "awaiting_commit", "failed"}, 30)


def test_moving_to_an_existing_domain_keeps_every_field_of_the_feature(proj):
    set_used_by(proj, "loan", "pay-fee", [{"domain": "external", "entry_point": "Batch.fees"}])
    before = features_of(proj, "loan")["pay-fee"]
    result = move(proj, ["pay-fee"])
    assert result["target"] == "billing" and result["moved"] == ["pay-fee"] and not result["new_domain"]
    assert "pay-fee" not in features_of(proj, "loan")
    after = features_of(proj, "billing")["pay-fee"]
    for field in ("name", "description", "entry_points", "tables", "used_by", "run_id"):
        assert after[field] == before[field], field
    assert after["domain"] == "billing" and after["moved_from"] == ["loan"]
    assert "billing" not in after["calls"]  # its own domain now


def test_a_split_creates_the_domain_with_settings_but_no_repositories(proj):
    inputs = {"domain_features": {"paths": ["com/old/loan/"], "notes": "skip batch jobs"},
              "deep_dive": {"paths": ["docs/loan/"], "notes": "see the wiki", "repository": "https://x/y.git",
                            "base_branch": "dev"}}
    with projects.use(proj.key), session_scope() as s:
        loan = s.get(Domain, (proj.key, "loan"))
        loan.context, loan.agent_inputs, loan.default_workflow = "All about loans", inputs, "lean"
    result = move(proj, ["approve"], new_domain={"key": "lending", "name": "Lending", "description": "Split out"})
    assert result["new_domain"] and result["target"] == "lending"
    new = proj.get("/api/domains/lending").json()
    assert (new["name"], new["description"], new["context"], new["default_workflow"]) == \
        ("Lending", "Split out", "All about loans", "lean")
    assert new["agent_inputs"]["deep_dive"] == {"paths": ["docs/loan/"], "notes": "see the wiki"}  # no repository
    # The source's listing paths would list the whole old domain again
    assert new["agent_inputs"]["domain_features"] == {"paths": ["LoanController.approve"], "notes": "skip batch jobs"}
    assert proj.get("/api/domains/loan").json()["agent_inputs"] == inputs  # the source keeps its own


def test_a_new_domain_is_not_reported_as_missing_from_the_bounded_contexts(proj):
    with projects.use(proj.key), session_scope() as s:
        s.merge(discovery_store.Setting(project=proj.key, key=discovery_store.CONTEXTS_SETTING,
                                        value={"run_id": "r", "keys": ["loan", "billing", "reports"]}))
    move(proj, ["approve"], new_domain={"key": "lending", "name": "Lending"})
    assert "lending" not in proj.get("/api/discovery").json()["not_found"]


@pytest.mark.parametrize("key,why", [("loan", "already exists"), ("len-ding", None), ("reports", "already exists")])
def test_a_new_domain_with_an_unusable_key_is_blocked(proj, key, why):
    if key == "len-ding":
        assert proj.post("/api/domains", json={"key": "lending", "name": "Lending"}).status_code == 201
        why = "would share the package"
    review = preview(proj, ["approve"], new_domain={"key": key, "name": "X"})
    assert any(why in b for b in review["blockers"])
    move(proj, ["approve"], new_domain={"key": key, "name": "X"}, expect=409)
    assert "approve" in features_of(proj, "loan")


def test_a_destination_must_be_one_or_the_other(proj):
    bad = {"features": ["approve"], "target": "billing", "new_domain": {"key": "lending", "name": "L"}}
    assert proj.post("/api/domains/loan/features/move/preview", json=bad).status_code == 422
    assert proj.post("/api/domains/loan/features/move/preview", json={"features": ["approve"]}).status_code == 422


def test_a_started_feature_cannot_move_until_it_is_reset(proj):
    started(proj, "loan", "approve")
    review = preview(proj, ["approve", "close"])
    row = next(f for f in review["features"] if f["key"] == "approve")
    assert "can't move" in row["blocked"] and "contracts already provided and pushed code stay" in row["blocked"]
    assert not row["ticked"] and row["migration"]
    # Named in a request without unticking it, the whole move is refused
    r = proj.post("/api/domains/loan/features/move", json={
        **body(["approve", "close"]), "fingerprint": review["fingerprint"], "acknowledged_calls": True, "tables": []})
    assert r.status_code == 409 and "approve" in r.json()["detail"]
    assert set(features_of(proj, "loan")) == {"approve", "pay-fee", "close"}

    assert move(proj, ["approve", "close"])["moved"] == ["close"]  # the rest can go without it
    assert "approve" in features_of(proj, "loan")
    assert proj.post("/api/domains/loan/features/approve/reset").status_code == 200
    assert move(proj, ["approve"])["moved"] == ["approve"]


def test_a_feature_the_target_already_has_is_blocked(proj):
    add(proj, "billing", "approve")
    add(proj, "billing", "payfee")
    review = preview(proj, ["approve", "pay-fee"])
    blocked = {f["key"]: f["blocked"] for f in review["features"]}
    assert "already has a feature approve" in blocked["approve"] and "share the package" in blocked["pay-fee"]
    assert not review["summary"]["features"] and review["blockers"] == ["Tick at least one feature to move"]


def test_unknown_things_are_refused(proj):
    assert proj.post("/api/domains/nope/features/move/preview", json=body(["approve"])).status_code == 404
    assert any("isn't a feature" in b for b in preview(proj, ["nope"])["blockers"])
    assert any("doesn't exist" in b for b in preview(proj, ["approve"], target="nope")["blockers"])
    assert any("already in this domain" in b for b in preview(proj, ["approve"], target="loan")["blockers"])


@pytest.mark.parametrize("domain", ["loan", "billing"])
def test_a_listing_at_work_blocks_it_and_one_at_its_gate_does_not(proj, domain):
    run_id = proj.post(f"/api/domains/{domain}/discover-features").json()["id"]
    wait_for(proj, run_id, {"awaiting_approval"})
    assert not preview(proj, ["approve"])["blockers"]
    with projects.use(proj.key), session_scope() as s:
        s.get(Run, run_id).status = "running"
    assert any("is working" in b for b in preview(proj, ["approve"])["blockers"])


def test_bounded_contexts_at_work_block_it(proj):
    run_id = proj.post("/api/discovery/bounded-contexts").json()["id"]
    wait_for(proj, run_id, {"awaiting_approval"})
    assert not preview(proj, ["approve"])["blockers"]
    with projects.use(proj.key), session_scope() as s:
        s.get(Run, run_id).status = "pending"
    assert any("is working" in b for b in preview(proj, ["approve"])["blockers"])


def test_the_acknowledgement_is_required(proj):
    review = preview(proj, ["approve"])
    r = proj.post("/api/domains/loan/features/move", json={**body(["approve"]), "fingerprint": review["fingerprint"]})
    assert r.status_code == 422 and "calls between" in r.json()["detail"]
    assert "approve" in features_of(proj, "loan")
    assert any("Calls between features aren't recorded" in w for w in review["warnings"])


def test_a_review_out_of_date_is_refused_and_changes_nothing(proj):
    review = preview(proj, ["close"])
    add(proj, "reports", "uses-loan", tables=["LOAN"])  # something the review would now say differently
    r = proj.post("/api/domains/loan/features/move", json={**body(["close"]), "fingerprint": review["fingerprint"],
                                                           "acknowledged_calls": True})
    assert r.status_code == 409 and "review the move again" in r.json()["detail"]
    assert "close" in features_of(proj, "loan") and "LOAN" in owned(proj, "loan")


def test_it_is_refused_while_the_data_folder_changes(proj):
    with maintenance.hold("moving the files"):
        assert proj.post("/api/domains/loan/features/move", json={
            **body(["close"]), "fingerprint": "x", "acknowledged_calls": True}).status_code == 409
    assert "close" in features_of(proj, "loan")


def test_what_the_table_list_offers(proj):
    review = preview(proj, ["pay-fee"])  # SCHEDULE only it uses, RATE shared with approve; LOAN, IDLE not its own
    assert table(review, "SCHEDULE")["selected"] and table(review, "SCHEDULE")["moving_features"] == ["pay-fee"]
    rate = table(review, "RATE")
    assert not rate["selected"] and not rate["locked"] and "need a contract" in rate["note"]
    assert rate["remaining_features"] == ["approve"]
    assert table(review, "IDLE")["group"] == "other" and not table(review, "IDLE")["selected"]
    assert table(review, "LOAN")["group"] == "other"  # no moving feature uses it


def test_tables_owned_elsewhere_or_by_nobody(proj):
    add(proj, "loan", "mixed", tables=["INVOICE", "LEDGER", "AUDIT"])
    assert proj.put("/api/domains/reports", json={"owned_tables": ["AUDIT"]}).status_code == 200
    review = preview(proj, ["mixed"])
    assert table(review, "INVOICE")["locked"] and "Already owned by billing" in table(review, "INVOICE")["note"]
    assert table(review, "AUDIT")["locked"] and "Owned by reports" in table(review, "AUDIT")["note"]
    ledger = table(review, "LEDGER")
    assert not ledger["locked"] and not ledger["selected"] and "No domain owns it" in ledger["note"]
    move(proj, ["mixed"], tables=["LEDGER"])
    assert "LEDGER" in owned(proj, "billing") and owned(proj, "reports") == ["AUDIT"]


def test_a_table_the_target_and_source_both_own_can_leave_the_source(proj):
    assert proj.put("/api/domains/billing", json={"owned_tables": ["INVOICE", "schedule"]}).status_code == 200
    add(proj, "loan", "bill", tables=["SCHEDULE"])
    row = table(preview(proj, ["bill"]), "SCHEDULE")
    assert row["selected"] and not row["locked"] and "removes it from loan" in row["note"]
    move(proj, ["bill"])
    assert "SCHEDULE" not in owned(proj, "loan") and owned(proj, "billing") == ["INVOICE", "schedule"]  # no duplicate


def test_a_table_owned_by_the_source_and_a_third_domain_warns(proj):
    assert proj.put("/api/domains/reports", json={"owned_tables": ["SCHEDULE"]}).status_code == 200
    row = table(preview(proj, ["pay-fee"]), "SCHEDULE")
    assert not row["selected"] and "Also owned by reports" in row["note"]
    assert proj.put("/api/domains/reports", json={"owned_tables": ["RATE"]}).status_code == 200
    rate = table(preview(proj, ["pay-fee"]), "RATE")  # both things to know about it
    assert "Also owned by reports" in rate["note"] and "need a contract" in rate["note"]


def test_a_table_a_started_feature_uses_is_locked(proj):
    started(proj, "loan", "approve")
    review = preview(proj, ["close"])
    row = table(review, "LOAN")  # close moves, approve (already migrating) stays
    assert row["locked"] and not row["selected"] and "approve already migrating" in row["note"]
    r = proj.post("/api/domains/loan/features/move", json={
        **body(["close"]), "fingerprint": review["fingerprint"], "acknowledged_calls": True, "tables": ["LOAN"]})
    assert r.status_code == 409 and "can't move" in r.json()["detail"]
    assert "LOAN" in owned(proj, "loan")


def test_a_table_in_a_run_design_is_locked(proj):
    run = started(proj, "loan", "close")
    design = {"domain": "loan", "owned_tables": ["IDLE"], "read_only_tables": [], "utility_modules": [],
              "other_domain_dependencies": [], "context_ports": [], **{k: [] for k in DOMAIN_DESIGN_LOCKED}}
    with projects.use(proj.key), session_scope() as s:
        s.add(Artifact(run_id=run["id"], stage="domain_design", attempt=1, name=DOMAIN_DESIGN_FILE,
                       path=DOMAIN_DESIGN_FILE, kind="yaml", content=yaml.safe_dump(design)))
    row = table(preview(proj, ["approve"]), "IDLE")
    assert row["locked"] and "design" in row["note"]


def test_unticked_tables_stay_and_ticked_packages_move(proj):
    review = preview(proj, ["pay-fee"], tables=[], packages=["com.old.loan"])
    assert not table(review, "SCHEDULE")["selected"]
    assert any("No table moves" in w for w in review["warnings"])
    assert "SCHEDULE" in review["summary"]["staying_tables"]
    move(proj, ["pay-fee"], tables=[], packages=["com.old.loan"])
    assert "SCHEDULE" in owned(proj, "loan")
    moved = features_of(proj, "billing")["pay-fee"]
    assert moved["tables"] == ["SCHEDULE", "RATE"]  # it still records the access
    assert "loan" in moved["calls"]  # through its owner
    domains = {d: proj.get(f"/api/domains/{d}").json()["owned_packages"] for d in ("loan", "billing")}
    assert domains == {"loan": [], "billing": ["com.old.billing", "com.old.loan"]}


def test_ticked_tables_move_case_insensitively_with_the_owners_spelling(proj):
    assert proj.put("/api/domains/loan", json={"owned_tables": ["Schedule", "RATE"]}).status_code == 200
    move(proj, ["pay-fee"], tables=["SCHEDULE", "rate"])
    assert set(owned(proj, "billing")) == {"INVOICE", "Schedule", "RATE"} and owned(proj, "loan") == []


def reference(review, *parts):
    return next((r for r in review["references"] if all(p in r["id"] for p in parts)), None)


def test_reference_updates(proj):
    add(proj, "loan", "keeps-rate", tables=["SCHEDULE"])  # stays; SCHEDULE moves with pay-fee
    add(proj, "reports", "report-fees", tables=["SCHEDULE"], entry_points=["ReportController.fees"])
    add(proj, "reports", "caller", entry_points=["ReportController.caller"])
    add(proj, "billing", "invoice", entry_points=["InvoiceController.make"])
    set_used_by(proj, "loan", "pay-fee", [{"domain": "billing", "entry_point": "InvoiceController.make"},
                                          {"domain": "reports", "entry_point": "ReportController.caller"},
                                          {"domain": "external", "entry_point": "Batch.fees"}])
    set_used_by(proj, "reports", "report-fees", [{"domain": "loan", "entry_point": "FeeController.pay"},
                                                 {"domain": "loan", "entry_point": "LoanController.close"}])
    set_used_by(proj, "billing", "invoice", [{"domain": "loan", "entry_point": "FeeController.pay"}])

    review = preview(proj, ["pay-fee"], tables=["SCHEDULE"])  # RATE stays with loan
    assert reference(review, "calls:loan/pay-fee", "remove:billing")
    assert reference(review, "calls:loan/pay-fee", "add:loan")
    assert reference(review, "calls:loan/pay-fee", "add:loan")["description"] == "pay-fee: will call loan (RATE stays there)"
    assert reference(review, "used_by:loan/pay-fee", "remove:billing")
    assert reference(review, "calls:loan/keeps-rate", "add:billing")
    assert reference(review, "calls:reports/report-fees", "add:billing")
    assert reference(review, "calls:reports/caller", "add:billing")
    assert reference(review, "used_by:reports/report-fees", "repoint", "FeeController.pay")
    assert reference(review, "used_by:billing/invoice", "remove")
    assert not reference(review, "LoanController.close")  # not a moved entry point
    assert all(r["description"] and r["selected"] for r in review["references"])

    move(proj, ["pay-fee"], tables=["SCHEDULE"])
    moved = features_of(proj, "billing")["pay-fee"]
    assert moved["calls"] == ["loan"]
    assert moved["used_by"] == [{"domain": "reports", "entry_point": "ReportController.caller"},
                                {"domain": "external", "entry_point": "Batch.fees"}]
    assert features_of(proj, "loan")["keeps-rate"]["calls"] == ["billing"]
    report = features_of(proj, "reports")
    assert report["report-fees"]["calls"] == ["billing"] and report["caller"]["calls"] == ["billing"]
    assert report["report-fees"]["used_by"] == [{"domain": "billing", "entry_point": "FeeController.pay"},
                                                {"domain": "loan", "entry_point": "LoanController.close"}]
    assert features_of(proj, "billing")["invoice"]["used_by"] == []


def test_an_unticked_reference_update_is_not_applied(proj):
    add(proj, "reports", "report-fees", tables=["SCHEDULE"])
    skip = reference(preview(proj, ["pay-fee"], tables=["SCHEDULE"]), "calls:reports/report-fees")["id"]
    review = preview(proj, ["pay-fee"], tables=["SCHEDULE"], skipped_references=[skip])
    assert not reference(review, skip)["selected"]
    move(proj, ["pay-fee"], tables=["SCHEDULE"], skipped_references=[skip])
    assert features_of(proj, "reports")["report-fees"]["calls"] == []
    assert "billing" not in features_of(proj, "billing")["pay-fee"]["calls"]  # the others were applied


def test_unticking_a_used_by_update_warns_it_can_only_be_fixed_by_listing(proj):
    set_used_by(proj, "loan", "pay-fee", [{"domain": "billing", "entry_point": "X.y"}])
    skip = reference(preview(proj, ["pay-fee"]), "used_by:loan/pay-fee")["id"]
    assert any("re-listing" in w for w in preview(proj, ["pay-fee"], skipped_references=[skip])["warnings"])


def test_what_to_check_by_hand_is_listed_and_left_alone(proj):
    add(proj, "reports", "caller", calls=["loan"])
    with projects.use(proj.key), session_scope() as s:
        s.get(Domain, (proj.key, "loan")).agent_inputs = {"domain_features": {"paths": ["com/old/loan/"], "notes": ""}}
        s.add(Contract(project=proj.key, key="loan.pay-fee", provider="loan", kind="api", name="Pay", status="approved",
                       consumers=["reports"]))
        s.add(Contract(project=proj.key, key="loan.other", provider="loan", kind="api", name="Other", consumers=[]))
        s.add(Contract(project=proj.key, key="loan.pay-fee-completed", provider="loan", kind="event", name="Paid",
                       consumers=[]))
        s.add(Contract(project=proj.key, key="loan.pay-feeless", provider="loan", kind="api", name="Free", consumers=[]))
    run = started(proj, "reports", "caller")
    with projects.use(proj.key), session_scope() as s:
        s.add(Dependency(project=proj.key, run_id=run["id"], interaction="i", source="reports", target="loan",
                         kind="query", pattern="sync-api", status="planned"))
    checks = preview(proj, ["pay-fee"])["check_by_hand"]
    assert checks["calling_features"] == [{"domain": "reports", "feature": "caller"}]
    assert [d["source"] for d in checks["dependencies"]] == ["reports"]
    assert {c["key"]: c["matches_moved_feature"] for c in checks["contracts"]} == \
        {"loan.other": False, "loan.pay-fee": True, "loan.pay-fee-completed": True, "loan.pay-feeless": False}
    assert checks["listing_paths"] == ["com/old/loan/"]
    move(proj, ["pay-fee"])
    assert features_of(proj, "reports")["caller"]["calls"] == ["loan"]
    with projects.use(proj.key), session_scope() as s:
        assert s.get(Contract, (proj.key, "loan.pay-fee")).provider == "loan"


def test_warnings_when_everything_moves(proj):
    review = preview(proj, ["approve", "pay-fee", "close"])
    assert any("Every feature leaves loan" in w for w in review["warnings"])
    assert any("shared and util" in w for w in review["warnings"])


def test_unticked_features_stay(proj):
    review = preview(proj, ["approve", "close"], unticked=["close"])
    assert review["summary"]["features"] == 1
    assert move(proj, ["approve", "close"], unticked=["close"])["moved"] == ["approve"]
    assert "close" in features_of(proj, "loan")


def test_a_moved_feature_migrates_in_its_new_domain(proj):
    move(proj, ["close"])
    assert proj.post("/api/runs", json={"domain": "loan", "operation": "close"}).status_code == 422  # not there now
    run = started(proj, "billing", "close")
    assert run["branch"].startswith("migration/billing/") and run["status"] != "failed", run.get("error")


def test_a_run_that_lost_the_race_with_a_move_is_refused(proj, monkeypatch):
    """The feature was there when the request looked, and gone once no move could be in progress."""
    real, calls = discovery_store.feature, []

    def moved_between(domain, key):
        calls.append(key)
        return real(domain, key) if len(calls) == 1 else None

    monkeypatch.setattr(discovery_store, "feature", moved_between)
    r = proj.post("/api/runs", json={"domain": "loan", "operation": "close"})
    assert r.status_code == 409 and "isn't in this domain's feature list" in r.json()["detail"]


def test_a_new_domain_needs_its_repositories_before_a_run_starts(client, tmp_path):
    _upstream, _key, p = setup_project(client, tmp_path)
    p.post("/api/domains/loan/features", json={"key": "reject", "name": "Reject"})
    move(p, ["reject"], new_domain={"key": "lending", "name": "Lending"})
    r = p.post("/api/runs", json={"domain": "lending", "operation": "reject"})
    assert r.status_code == 422 and "Set the repository" in r.json()["detail"]


# --- a later discovery can't undo a move ------------------------------------------------------------------------------

def listing(p, domain, content=None):
    run_id = p.post(f"/api/domains/{domain}/discover-features").json()["id"]
    wait_for(p, run_id, {"awaiting_approval"})
    if content is not None:
        write_list(p, run_id, content)
    return run_id


def write_list(p, run_id, content):
    doc = next(a for a in p.get(f"/api/runs/{run_id}/artifacts").json() if a["name"] == "features.yaml")
    assert p.put(f"/api/artifacts/{doc['id']}", json={"content": content}).status_code == 200


def approve(p, run_id):
    return p.post(f"/api/runs/{run_id}/decision", json={"reviewer": "t", "decision": "approve"})


def test_a_waiting_list_of_the_source_cannot_bring_a_moved_feature_back(proj):
    run_id = listing(proj, "loan", "features:\n  - {key: close, name: Close}\n  - {key: fresh, name: Fresh}\n")
    move(proj, ["close"])
    r = approve(proj, run_id)
    assert r.status_code == 422 and "moved to billing" in r.json()["detail"] and "Edit the file" in r.json()["detail"]
    write_list(proj, run_id, "features:\n  - {key: fresh, name: Fresh}\n")
    assert approve(proj, run_id).status_code == 200
    assert "close" not in features_of(proj, "loan") and "fresh" in features_of(proj, "loan")
    assert "close" in features_of(proj, "billing")


def test_a_renamed_key_with_the_same_entry_point_cannot_bring_it_back(proj):
    run_id = listing(proj, "loan", "features:\n  - {key: shut, name: Shut, entry_points: [LoanController.close]}\n")
    move(proj, ["close"])
    r = approve(proj, run_id)
    assert r.status_code == 422 and "shut is close" in r.json()["detail"]


def test_a_waiting_list_of_the_target_keeps_the_moved_in_feature(proj):
    run_id = listing(proj, "billing", "features:\n  - {key: invoice, name: Invoice}\n")
    move(proj, ["close"])
    assert approve(proj, run_id).status_code == 200
    billing = features_of(proj, "billing")
    assert set(billing) == {"invoice", "close"} and billing["close"]["missing_from_latest"]


def test_listing_again_is_told_about_moved_features(proj):
    move(proj, ["close"])
    with projects.use(proj.key):
        brief = discovery_store.domains_brief("loan")
    assert "moved out of `loan`" in brief and "`close` Close, now in `billing`" in brief
    with projects.use(proj.key):
        assert "moved out of" not in discovery_store.domains_brief("billing")


def test_bounded_contexts_cannot_put_moved_tables_back(proj):
    move(proj, ["pay-fee"], tables=["SCHEDULE"])
    context = {"key": "loan", "name": "Loan", "description": "", "tables": ["SCHEDULE", "NEW_ONE"],
               "packages": ["com.old.billing", "com.new"], "entry_points": []}
    with projects.use(proj.key):
        summary = discovery_store._record_contexts("run", [context])
    assert "SCHEDULE" not in owned(proj, "loan") and "NEW_ONE" in owned(proj, "loan")
    assert "SCHEDULE (not loan)" in summary and "com.old.billing (not loan)" in summary
    assert "com.new" in proj.get("/api/domains/loan").json()["owned_packages"]


def test_moved_from_follows_the_feature_around(proj):
    move(proj, ["close"])
    move(proj, ["close"], source="billing", target="reports")
    assert features_of(proj, "reports")["close"]["moved_from"] == ["loan", "billing"]
    for domain in ("loan", "billing"):
        with projects.use(proj.key):
            assert "`close`" in discovery_store.domains_brief(domain)
    move(proj, ["close"], source="reports", target="loan")  # back home
    assert features_of(proj, "loan")["close"]["moved_from"] == ["billing", "reports"]
    run_id = listing(proj, "loan")  # the loan list may have it again
    assert approve(proj, run_id).status_code == 200
