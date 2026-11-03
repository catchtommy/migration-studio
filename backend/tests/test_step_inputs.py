"""Each step attempt keeps what it was given: the earlier documents it used, with the attempt that produced each, and
the full prompt its agent got. Only users with runs.inspect (admins by default) see them."""

import itertools

from app.db.session import session_scope
from tests.conftest import approve, start_run, wait_for
from tests.test_auth import make_user, signed_in

_ids = itertools.count()


def _attempts(run, key):
    return [s for s in run["stages"] if s["stage"] == key]


def _to_design_gate(client):
    run_id = start_run(client, {"domain": "loan", "operation": f"insp{next(_ids)}", "workflow": "lean"}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    approve(client, run_id)
    return run_id, wait_for(client, run_id, {"awaiting_approval"})


def test_a_step_records_the_document_versions_it_used_and_its_full_prompt(client):
    domain = client.get("/api/domains/loan").json()
    guidance = {**(domain.get("agent_inputs") or {}),
                "modulith_design": {"paths": [], "notes": "Keep the KYC call synchronous."}}
    client.put("/api/domains/loan", json={"agent_inputs": guidance})
    try:
        run_id, run = _to_design_gate(client)
    finally:
        client.put("/api/domains/loan", json={"agent_inputs": domain.get("agent_inputs") or {}})
    design = _attempts(run, "modulith_design")[-1]
    used = {i["path"].rsplit("/", 1)[-1]: i for i in design["inputs"]}
    assert {"as-is.md", "functional-spec.md", "test-scenarios.md", "cross-domain.yaml",
            "domain-dependencies.yaml"} <= set(used)
    assert used["as-is.md"]["stage"] == "analysis" and used["as-is.md"]["attempt"] == 1
    assert used["cross-domain.yaml"]["stage_title"] == "Domain dependencies" and used["as-is.md"]["artifact_id"]
    assert not used["as-is.md"]["newer_attempt"] and not used["as-is.md"]["changed"]
    prompt = client.get(f"/api/runs/{run_id}/stages/{design['id']}/prompt").json()
    assert "You design how ONE feature moves into its own domain" in prompt["system"]
    assert "## Guardrails" in prompt["system"]  # its built-in guardrails, as sent
    assert "Keep the KYC call synchronous." in prompt["brief"]  # the domain's agent inputs
    assert "read_file" in prompt["tools"] and prompt["mode"] == "stub"


def test_after_a_change_the_new_attempt_records_the_changed_versions(client):
    run_id, _ = _to_design_gate(client)
    r = client.post(f"/api/runs/{run_id}/change", json={"stage": "analysis", "notes": "Add the refund flow",
                                                         "follow": ["dependency_analysis", "modulith_design"]})
    assert r.status_code == 200, r.text
    wait_for(client, run_id, {"awaiting_approval"})
    approve(client, run_id)
    run = wait_for(client, run_id, {"awaiting_approval"})
    used = {i["path"].rsplit("/", 1)[-1]: i for i in _attempts(run, "modulith_design")[-1]["inputs"]}
    assert used["as-is.md"]["attempt"] == 2 and used["as-is.md"]["changed"]  # the amended Analysis


def test_a_document_from_an_older_attempt_than_the_steps_latest_is_flagged(client, monkeypatch):
    from app import workspace

    real = workspace.document_versions

    def older(run_id, before_stage=None):  # as if Analysis's newer attempt weren't in effect (rolled back, say)
        out = real(run_id, before_stage)
        return {p: ({**v, "attempt": 1} if v["stage"] == "analysis" else v) for p, v in out.items()}

    run_id, _ = _to_design_gate(client)
    client.post(f"/api/runs/{run_id}/change", json={"stage": "analysis", "notes": "x", "follow": ["modulith_design"]})
    wait_for(client, run_id, {"awaiting_approval"})
    monkeypatch.setattr(workspace, "document_versions", older)
    approve(client, run_id)
    run = wait_for(client, run_id, {"awaiting_approval"})
    used = {i["path"].rsplit("/", 1)[-1]: i for i in _attempts(run, "modulith_design")[-1]["inputs"]}
    assert used["as-is.md"]["newer_attempt"] == 2


def test_only_users_allowed_to_inspect_see_inputs_and_prompts(client):
    run_id, run = _to_design_gate(client)
    design = _attempts(run, "modulith_design")[-1]
    gate = _attempts(run, "gate_analysis")[-1]
    assert client.get(f"/api/runs/{run_id}/stages/{gate['id']}/prompt").status_code == 404  # a gate: no prompt
    for role in ("developer", "reviewer"):
        other = signed_in(make_user(client, [role])["username"])
        seen = other.get(f"/api/runs/{run_id}").json()
        assert all(s["inputs"] is None for s in seen["stages"]), role
        assert other.get(f"/api/runs/{run_id}/stages/{design['id']}/prompt").status_code == 403, role
    from app.db.models import Role
    key = f"inspector{next(_ids)}"
    with session_scope() as s:  # an admin gives the permission to a role of their own
        s.add(Role(key=key, name="Inspector", description="", permissions=["runs.inspect"]))
    inspector = signed_in(make_user(client, [key])["username"])
    seen = {s["id"]: s for s in inspector.get(f"/api/runs/{run_id}").json()["stages"]}
    assert seen[design["id"]]["inputs"]
    assert inspector.get(f"/api/runs/{run_id}/stages/{design['id']}/prompt").status_code == 200
