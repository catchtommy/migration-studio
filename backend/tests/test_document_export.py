"""Downloading the documents the agents generated: one run's, or a whole domain's, as a zip."""

import io
import itertools
import zipfile

from tests.conftest import start_run, wait_for

_ids = itertools.count()


def _zip(response) -> zipfile.ZipFile:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    return zipfile.ZipFile(io.BytesIO(response.content))


def test_a_runs_documents_download_as_one_zip_a_folder_per_agent(client):
    operation = f"dl{next(_ids)}"
    run = start_run(client, {"domain": "loan", "operation": operation}).json()
    wait_for(client, run["id"], {"awaiting_approval"})  # AS-IS and deep dive have run

    response = client.get(f"/api/runs/{run['id']}/documents.zip")
    assert f'loan-{operation}-documents.zip' in response.headers["content-disposition"]
    names = _zip(response).namelist()
    assert "01-as_is_doc/as_is_doc.md" in names
    assert "02-deep_dive/deep_dive.md" in names and "02-deep_dive/cross-domain.yaml" in names
    # The contents are the documents as the run has them, reviewers' edits included.
    artifact = next(a for a in client.get(f"/api/runs/{run['id']}/artifacts").json() if a["stage"] == "as_is_doc")
    assert _zip(client.get(f"/api/runs/{run['id']}/documents.zip")).read("01-as_is_doc/as_is_doc.md").decode() == \
        client.get(f"/api/artifacts/{artifact['id']}").json()["content"]


def test_a_domains_documents_download_with_a_folder_per_feature(client):
    operation = f"dl{next(_ids)}"
    run = start_run(client, {"domain": "broker", "operation": operation}).json()
    wait_for(client, run["id"], {"awaiting_approval"})

    names = _zip(client.get("/api/domains/broker/documents.zip")).namelist()
    assert f"{operation}/01-as_is_doc/as_is_doc.md" in names
    assert all(not n.startswith((".", "/")) and ".." not in n for n in names)


def test_unknown_runs_and_domains_have_nothing_to_download(client):
    assert client.get("/api/runs/nope/documents.zip").status_code == 404
    assert client.get("/api/domains/nope/documents.zip").status_code == 404
