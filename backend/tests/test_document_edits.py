"""Documents a step wrote stay its documents when it runs again: shown and editable with its latest attempt, and
given to later steps, whether the re-run changed them or not."""

import itertools

from app.agents import stub
from app.workspace import effective_documents
from tests.conftest import start_run, wait_for

_ids = itertools.count()


def _artifacts(client, run_id, stage, attempt):
    return {a["name"]: a for a in client.get(f"/api/runs/{run_id}/artifacts").json()
            if a["stage"] == stage and a["attempt"] == attempt}


def test_a_re_run_keeps_the_documents_it_did_not_change_and_they_stay_editable(client, monkeypatch):
    original = stub.StubAgent.run
    calls = {"n": 0}

    def run(self, ctx):
        if self.stage == "deep_dive" and ctx.operation.startswith("edit"):
            calls["n"] += 1
            if calls["n"] == 1:  # the first time it also writes a document of its own (not a required one)
                (ctx.output_dir / "rules-notes.md").write_text("# Notes on the rules\n")
        return original(self, ctx)

    monkeypatch.setattr(stub.StubAgent, "run", run)
    run_id = start_run(client, {"domain": "customer", "operation": f"edit{next(_ids)}"}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    assert "rules-notes.md" in _artifacts(client, run_id, "deep_dive", 1)

    # Re-run from the deep dive, updating its documents: this time it doesn't touch rules-notes.md.
    assert client.post(f"/api/runs/{run_id}/restart", json={"stage": "deep_dive"}).status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    latest = _artifacts(client, run_id, "deep_dive", 2)
    assert "rules-notes.md" in latest  # still the step's document, with its latest attempt
    path = latest["rules-notes.md"]["path"]
    assert effective_documents(run_id)[path] == "# Notes on the rules\n"  # and later steps still get it

    r = client.put(f"/api/artifacts/{latest['rules-notes.md']['id']}", json={"content": "# Edited\n"})
    assert r.status_code == 200, r.text
    assert effective_documents(run_id)[path] == "# Edited\n"  # the edit is what the run uses
