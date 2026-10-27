import itertools

from tests.conftest import start_run, wait_for

_ids = itertools.count()


def _key(prefix):
    return f"{prefix}{next(_ids)}"


def _two_step(gate_reviews=("as_is_doc",)):
    return [{"kind": "agent", "key": "as_is_doc"},
            {"kind": "gate", "title": "Check docs", "reviews": list(gate_reviews)},
            {"kind": "agent", "key": "deep_dive"}]


def test_standard_workflow_is_seeded(client):
    standard = client.get("/api/workflows/standard").json()
    assert [s["key"] for s in standard["stages"]][:3] == ["as_is_doc", "deep_dive", "gate_analysis"]
    assert standard["stages"][0]["title"] == "AS-IS documentation"  # agent titles come from the library


def test_custom_workflow_runs_its_own_steps_and_gates(client):
    key = _key("wf")
    r = client.post("/api/workflows", json={"key": key, "name": "Docs only", "steps": _two_step()})
    assert r.status_code == 201, r.text
    gate = r.json()["steps"][1]
    assert gate["key"] == "gate_check_docs"  # generated from the title

    run = start_run(client, {"domain": "broker", "operation": _key("op"), "workflow": key}).json()
    assert [s["key"] for s in run["workflow_stages"]] == ["as_is_doc", "gate_check_docs", "deep_dive"]
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert run["pending_gate"] == {"gate": "gate_check_docs", "reviews": ["as_is_doc"], "checklist": []}

    client.post(f"/api/runs/{run['id']}/decision", json={"reviewer": "t", "decision": "reject", "comments": "More"})
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert [s["status"] for s in run["stages"] if s["stage"] == "as_is_doc"] == ["completed", "completed"]

    client.post(f"/api/runs/{run['id']}/decision", json={"reviewer": "t", "decision": "approve"})
    run = wait_for(client, run["id"], {"completed"})
    assert [s["stage"] for s in run["stages"]] == [
        "as_is_doc", "gate_check_docs", "as_is_doc", "gate_check_docs", "deep_dive"]


def test_running_runs_keep_the_workflow_they_started_with(client):
    key = _key("wf")
    client.post("/api/workflows", json={"key": key, "name": "Snapshot", "steps": _two_step()})
    run = start_run(client, {"domain": "broker", "operation": _key("op"), "workflow": key}).json()
    wait_for(client, run["id"], {"awaiting_approval"})

    # Remove the gate and the second agent, then delete the workflow altogether.
    r = client.put(f"/api/workflows/{key}", json={"steps": [{"kind": "agent", "key": "as_is_doc"}]})
    assert r.status_code == 200
    # While a run uses the workflow it can't be deleted, and the refusal says why.
    r = client.delete(f"/api/workflows/{key}")
    assert r.status_code == 409 and "1 run(s) still use it" in r.json()["detail"]

    client.post(f"/api/runs/{run['id']}/decision", json={"reviewer": "t", "decision": "approve"})
    done = wait_for(client, run["id"], {"completed"})
    assert [s["stage"] for s in done["stages"]] == ["as_is_doc", "gate_check_docs", "deep_dive"]


def test_invalid_workflows_are_refused(client):
    def create(steps):
        return client.post("/api/workflows", json={"key": _key("bad"), "name": "Bad", "steps": steps})

    assert "at least one agent" in create([]).json()["detail"]
    assert "unknown agent" in create([{"kind": "agent", "key": "nope"}]).json()["detail"]
    assert "already in this workflow" in create([{"kind": "agent", "key": "as_is_doc"}] * 2).json()["detail"]
    r = create([{"kind": "gate", "title": "Too early", "reviews": ["as_is_doc"]}, {"kind": "agent", "key": "as_is_doc"}])
    assert "before it" in r.json()["detail"]
    r = create([{"kind": "agent", "key": "as_is_doc"}, {"kind": "gate", "title": "Empty", "reviews": []}])
    assert "at least one" in r.json()["detail"]
    assert client.post("/api/workflows", json={"key": "standard", "name": "Dup", "copy_from": "standard"}).status_code == 422


def test_custom_agent_in_a_copied_workflow(client):
    agent = _key("security_review")
    r = client.post("/api/agents", json={"key": agent, "title": "Security review", "description": "OWASP pass"})
    assert r.status_code == 201 and r.json()["builtin"] is False and r.json()["outputs"] == [f"{agent}.md"]
    assert client.post(f"/api/agents/{agent}/reset").status_code == 409  # no default for custom agents

    wf = _key("wf")
    copied = client.post("/api/workflows", json={"key": wf, "name": "With security", "copy_from": "standard"}).json()
    steps = copied["steps"][:2] + [{"kind": "agent", "key": agent},
                                   {"kind": "gate", "title": "Approve security", "reviews": [agent, "deep_dive"]}]
    assert client.put(f"/api/workflows/{wf}", json={"steps": steps}).status_code == 200

    run = start_run(client, {"domain": "payments", "operation": _key("op"), "workflow": wf}).json()
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert run["pending_gate"]["reviews"] == [agent, "deep_dive"]
    artifacts = client.get(f"/api/runs/{run['id']}/artifacts").json()
    assert {a["stage"] for a in artifacts} == {"as_is_doc", "deep_dive", agent}

    r = client.delete(f"/api/agents/{agent}")
    assert r.status_code == 409 and "With security" in r.json()["detail"]
    client.put(f"/api/workflows/{wf}", json={"steps": copied["steps"][:2]})
    assert client.delete(f"/api/agents/{agent}").status_code == 409  # the unfinished run still uses it
    client.post(f"/api/runs/{run['id']}/decision", json={"reviewer": "t", "decision": "approve"})
    wait_for(client, run["id"], {"completed"})
    assert client.delete(f"/api/agents/{agent}").status_code == 204


def test_domain_default_workflow_is_used_and_cleared_on_delete(client):
    wf = _key("wf")
    client.post("/api/workflows", json={"key": wf, "name": "Short", "steps": [{"kind": "agent", "key": "as_is_doc"}]})
    assert client.put("/api/domains/reports", json={"default_workflow": "missing"}).status_code == 422
    assert client.put("/api/domains/reports", json={"default_workflow": wf}).json()["default_workflow"] == wf

    run = start_run(client, {"domain": "reports", "operation": _key("op")}).json()
    assert run["workflow"] == wf
    wait_for(client, run["id"], {"completed"})

    assert client.delete(f"/api/workflows/{wf}").status_code == 409  # the run still uses it
    assert client.post(f"/api/domains/reports/features/{run['operation']}/reset").status_code == 200
    assert client.delete(f"/api/workflows/{wf}").status_code == 204
    assert client.get("/api/domains/reports").json()["default_workflow"] is None
    assert start_run(client, {"domain": "reports", "operation": _key("op")}).json()["workflow"] == "standard"



def _doc(run, stage):
    from tests.conftest import document
    return document(run, stage).read_text()


def test_steps_receive_only_the_outputs_they_use(client):
    key = _key("wf")
    steps = [{"kind": "agent", "key": "as_is_doc"},
             {"kind": "agent", "key": "boundary_design", "uses": []},
             {"kind": "agent", "key": "deep_dive", "uses": ["as_is_doc"], "include_content": True},
             {"kind": "agent", "key": "modernise"}]  # default: every earlier step
    r = client.post("/api/workflows", json={"key": key, "name": "Uses", "steps": steps})
    assert r.status_code == 201, r.text
    assert r.json()["stages"][2]["uses"] == ["as_is_doc"] and r.json()["stages"][3]["uses"] is None

    run = start_run(client, {"domain": "broker", "operation": _key("op"), "workflow": key}).json()
    run = wait_for(client, run["id"], {"completed"})
    assert "Inputs from earlier steps" not in _doc(run, "boundary_design")
    deep = _doc(run, "deep_dive")
    assert "AS-IS documentation: `" in deep and "as_is_doc.md` (contents included)" in deep
    assert "boundary_design" not in deep
    modern = _doc(run, "modernise")
    assert all(f"{s}.md`" in modern for s in ("as_is_doc", "boundary_design", "deep_dive"))
    assert "contents included" not in modern


def test_a_step_can_only_use_earlier_outputs(client):
    steps = [{"kind": "agent", "key": "as_is_doc", "uses": ["deep_dive"]}, {"kind": "agent", "key": "deep_dive"}]
    r = client.post("/api/workflows", json={"key": _key("bad"), "name": "Bad", "steps": steps})
    assert r.status_code == 422 and "before it" in r.json()["detail"]


def test_a_workflow_a_run_uses_cannot_be_deleted_and_says_which_runs(client):
    wf = _key("wf")
    client.post("/api/workflows", json={"key": wf, "name": "In use", "steps": [{"kind": "agent", "key": "as_is_doc"}]})
    assert client.get(f"/api/workflows/{wf}/usage").json()["blocked"] is None  # nothing uses it yet
    op = _key("op")
    run = start_run(client, {"domain": "broker", "operation": op, "workflow": wf}).json()
    wait_for(client, run["id"], {"completed"})

    usage = client.get(f"/api/workflows/{wf}/usage").json()
    assert [r["id"] for r in usage["runs"]] == [run["id"]] and usage["steps"] == 1
    assert f"broker/{op} (completed)" in usage["blocked"] and "reset their features" in usage["blocked"]
    r = client.delete(f"/api/workflows/{wf}")
    assert r.status_code == 409 and "In use workflow can't be deleted" in r.json()["detail"]

    assert client.post(f"/api/domains/broker/features/{op}/reset").status_code == 200  # the run is deleted
    assert client.get(f"/api/workflows/{wf}/usage").json() == {
        "name": "In use", "steps": 1, "runs": [], "copies": [], "defaults": [], "blocked": None}
    assert client.delete(f"/api/workflows/{wf}").status_code == 204


def test_an_agent_in_a_workflow_cannot_be_deleted_until_its_step_is_removed(client):
    agent = _key("helper")
    assert client.post("/api/agents", json={"key": agent, "title": "Helper", "description": ""}).status_code == 201
    wf = _key("wf")
    client.post("/api/workflows", json={"key": wf, "name": "Has helper", "steps": [{"kind": "agent", "key": agent}]})

    usage = client.get(f"/api/agents/{agent}/usage").json()
    assert usage["workflows"] == [{"name": "Has helper", "library": "default"}]  # the project's own workflow
    assert "Has helper (project default)" in usage["blocked"] and "Remove its step" in usage["blocked"]
    r = client.delete(f"/api/agents/{agent}")
    assert r.status_code == 409 and "Helper agent can't be deleted" in r.json()["detail"]

    assert client.delete(f"/api/workflows/{wf}").status_code == 204  # no run used it
    assert client.get(f"/api/agents/{agent}/usage").json()["blocked"] is None
    assert client.delete(f"/api/agents/{agent}").status_code == 204
    assert client.get(f"/api/agents/{agent}/usage").status_code == 404


def test_workflows_can_be_browsed_by_project_or_all_together(client):
    from tests.test_projects import as_project, new_project

    other = new_project(client)
    proj = as_project(client, other)
    own = _key("wf")
    r = proj.post("/api/workflows", json={"key": own, "name": "Theirs", "steps": [{"kind": "agent", "key": "as_is_doc"}]})
    assert r.status_code == 201, r.text

    here = {w["key"]: w for w in client.get("/api/workflows/browse", params={"of": "default"}).json()}
    assert "standard" in here and own not in here and here["standard"]["owner"] is None  # shared, used here too
    theirs = {w["key"]: w for w in client.get("/api/workflows/browse", params={"of": other}).json()}
    assert theirs[own]["owner"] == other and theirs[own]["library"] == "project" and "standard" in theirs
    everything = client.get("/api/workflows/browse", params={"of": "all"}).json()
    assert {(w["key"], w["owner"]) for w in everything} >= {("standard", None), (own, other)}
    assert client.get("/api/workflows/browse", params={"of": "nope"}).status_code == 404
    from tests.test_auth import make_user, signed_in
    outsider = signed_in(make_user(client, ["viewer"])["username"])  # a member of the default project only
    assert outsider.get("/api/workflows/browse", params={"of": other}).status_code == 404
    assert own not in {w["key"] for w in outsider.get("/api/workflows/browse", params={"of": "all"}).json()}


def test_a_project_default_migration_workflow_is_used_unless_the_domain_has_one(client):
    from app.db.models import Run
    from app.db.session import session_scope

    try:
        assert client.put("/api/projects/default/settings", json={"default_workflow": "list_features"}).status_code == 422
        assert client.put("/api/projects/default/settings", json={"default_workflow": "nope"}).status_code == 422
        r = client.put("/api/projects/default/settings", json={"default_workflow": "lean"})
        assert r.status_code == 200 and r.json()["default_workflow"] == "lean"
        run = start_run(client, {"domain": "auditing", "operation": _key("op")}).json()
        assert run["workflow"] == "lean"  # the project's default

        own = _key("wf")
        client.post("/api/workflows", json={"key": own, "name": "Domain's", "steps": [{"kind": "agent", "key": "as_is_doc"}]})
        assert client.put("/api/domains/auditing", json={"default_workflow": own}).status_code == 200
        assert start_run(client, {"domain": "auditing", "operation": _key("op")}).json()["workflow"] == own  # domain wins
        client.put("/api/domains/auditing", json={"default_workflow": ""})

        # Deleting the project's default workflow puts the default back to the Standard migration.
        r = client.put("/api/projects/default/settings", json={"default_workflow": own})
        assert r.status_code == 200
        assert client.delete(f"/api/workflows/{own}").status_code == 409  # a run used it
        with session_scope() as s:
            used = [(r.domain, r.operation) for r in s.query(Run).filter(Run.workflow == own)]
        for domain, operation in used:  # the runs that use it go first
            client.post(f"/api/domains/{domain}/features/{operation}/reset")
        assert client.delete(f"/api/workflows/{own}").status_code == 204
        assert client.get("/api/projects/default/settings").json()["default_workflow"] == ""
        assert start_run(client, {"domain": "auditing", "operation": _key("op")}).json()["workflow"] == "standard"
    finally:
        client.put("/api/projects/default/settings", json={"default_workflow": ""})


def test_a_gate_checklist_must_be_ticked_before_approving(client):
    key = _key("wf")
    steps = _two_step()
    steps[1]["checklist"] = ["  Read the AS-IS document ", "Checked the SQL", "Checked the SQL", ""]
    r = client.post("/api/workflows", json={"key": key, "name": "Checklist", "steps": steps})
    assert r.status_code == 201, r.text
    assert r.json()["steps"][1]["checklist"] == ["Read the AS-IS document", "Checked the SQL"]  # trimmed, unique

    run = start_run(client, {"domain": "broker", "operation": _key("op"), "workflow": key}).json()
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert [(c["item"], c["done"]) for c in run["pending_gate"]["checklist"]] == [
        ("Read the AS-IS document", False), ("Checked the SQL", False)]
    r = client.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve"})
    assert r.status_code == 422 and "Read the AS-IS document; Checked the SQL" in r.json()["detail"]

    assert client.post(f"/api/runs/{run['id']}/checklist", json={"item": "Not on it"}).status_code == 422
    state = client.post(f"/api/runs/{run['id']}/checklist", json={"item": "Checked the SQL"}).json()
    assert [c["done"] for c in state] == [False, True] and state[1]["by"]
    assert client.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve"}).status_code == 422

    # Rejecting doesn't need the checklist; the gate reached again starts with nothing ticked.
    client.post(f"/api/runs/{run['id']}/decision", json={"decision": "reject", "comments": "More"})
    run = wait_for(client, run["id"], {"awaiting_approval"})
    assert [c["done"] for c in run["pending_gate"]["checklist"]] == [False, False]
    rejected = [s for s in run["stages"] if s["stage"] == "gate_check_docs"][0]
    assert [t["item"] for t in rejected["checklist"]] == ["Checked the SQL"]  # what that attempt had ticked

    # Items added to the workflow while the run waits apply to it; ones removed no longer hold it back.
    steps[1]["checklist"] = ["Checked the SQL", "Asked the DBA"]
    steps[1]["key"] = "gate_check_docs"
    assert client.put(f"/api/workflows/{key}", json={"steps": steps}).status_code == 200
    run = client.get(f"/api/runs/{run['id']}").json()
    assert [c["item"] for c in run["pending_gate"]["checklist"]] == ["Checked the SQL", "Asked the DBA"]
    assert not (run["workflow_update"] or {}).get("rerun")  # a checklist is no reason to re-run agents
    for item in ("Checked the SQL", "Asked the DBA"):
        client.post(f"/api/runs/{run['id']}/checklist", json={"item": item})
    client.post(f"/api/runs/{run['id']}/checklist", json={"item": "Asked the DBA", "done": False})
    assert client.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve"}).status_code == 422
    client.post(f"/api/runs/{run['id']}/checklist", json={"item": "Asked the DBA"})
    assert client.post(f"/api/runs/{run['id']}/decision", json={"decision": "approve"}).status_code == 200
    done = wait_for(client, run["id"], {"completed"})
    approved = [s for s in done["stages"] if s["stage"] == "gate_check_docs"][-1]
    assert approved["status"] == "approved" and [t["item"] for t in approved["checklist"]] == [
        "Checked the SQL", "Asked the DBA"]  # in the checklist's order


def test_a_checklist_must_be_a_list_of_texts(client):
    steps = _two_step()
    steps[1]["checklist"] = ["x" * 301]
    r = client.post("/api/workflows", json={"key": _key("bad"), "name": "Bad", "steps": steps})
    assert r.status_code == 422 and "longer than 300" in r.json()["detail"]
