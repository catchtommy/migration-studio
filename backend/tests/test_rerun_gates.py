"""A re-run that brings a run up to date goes through every gate between the steps it re-runs: each asks again, and
while one waits, nothing offers a re-run from a step after it (which would drop that approval)."""

import itertools

from tests.conftest import start_run, wait_for

_ids = itertools.count()


def _approve(client, run_id):
    r = client.post(f"/api/runs/{run_id}/decision", json={"reviewer": "t", "decision": "approve"})
    assert r.status_code == 200, r.text


def test_a_re_run_across_a_gate_stops_at_the_gate_and_offers_nothing_beyond_it(client):
    run_id = start_run(client, {"domain": "customer", "operation": f"gates{next(_ids)}"}).json()["id"]
    assert wait_for(client, run_id, {"awaiting_approval"})["pending_gate"]["gate"] == "gate_analysis"
    _approve(client, run_id)
    assert wait_for(client, run_id, {"awaiting_approval"})["pending_gate"]["gate"] == "gate_design"

    changed = {}
    for key in ("deep_dive", "domain_design"):  # one before the analysis gate, one after it
        agent = client.get(f"/api/agents/{key}").json()
        changed[key] = agent
        r = client.put(f"/api/agents/{key}", json={**agent, "guardrails": agent["guardrails"] + "\n- x"})
        assert r.status_code == 200
    try:
        update = client.get(f"/api/runs/{run_id}").json()["workflow_update"]
        assert update["rerun"]["from"] == "deep_dive"
        assert client.post(f"/api/runs/{run_id}/update-workflow", json={"rerun": True}).status_code == 200
        run = wait_for(client, run_id, {"awaiting_approval"})
        assert run["pending_gate"]["gate"] == "gate_analysis"  # stopped at the gate in the middle

        # Domain design (after the gate) changed too, but it runs again once the gate is approved: no re-run offer
        # that would replace the approval waiting now.
        assert run["workflow_update"] is None
        assert client.post(f"/api/runs/{run_id}/update-workflow", json={"rerun": True}).status_code == 422

        _approve(client, run_id)
        run = wait_for(client, run_id, {"awaiting_approval"})
        assert run["pending_gate"]["gate"] == "gate_design"
        assert [e["status"] for e in run["stages"] if e["stage"] == "domain_design"] == ["completed", "completed"]
        assert run["workflow_update"] is None  # every changed step ran again with its current agent
    finally:
        for key, agent in changed.items():
            client.put(f"/api/agents/{key}", json=agent)
