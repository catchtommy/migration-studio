"""GitHub Copilot sign-in from Settings (against a stand-in for the Copilot runtime), and the data folder's remaining
outcomes: choosing the folder in use, and a copy that fails half way."""

import pytest

from app import data_folder
from app.api import copilot_api
from app.config import settings
from tests.test_data_folder import chooseable  # noqa: F401 - the fixture


class FakeCopilot:
    def __init__(self, fail: bool = False):
        self.fail, self.calls = fail, []

    def _call(self, name, *args):
        self.calls.append((name, *args))
        if self.fail:
            raise RuntimeError("the runtime isn't reachable")
        return {"flow": "f1", "step": name}

    def status(self):
        return {"signed_in": not self.fail, "login": "octocat"}

    def models(self):
        return [{"id": "gpt-x", "name": "GPT X"}]

    def login_begin(self, kind):
        return self._call("begin", kind)

    def login_advance(self, flow_id, value):
        return self._call("advance", flow_id, value)

    def login_cancel(self, flow_id):
        self._call("cancel", flow_id)

    def login_with_token(self, token, host):
        self._call("token", token, host)

    def logout(self):
        self._call("logout")


@pytest.fixture
def copilot(monkeypatch):
    fake = FakeCopilot()
    monkeypatch.setattr(copilot_api, "get_service", lambda: fake)
    return fake


def test_the_copilot_account_status_and_models_are_shown(client, copilot):
    assert client.get("/api/copilot/status").json() == {"signed_in": True, "login": "octocat"}
    assert client.get("/api/copilot/models").json() == [{"id": "gpt-x", "name": "GPT X"}]


def test_signing_in_and_out_of_copilot(client, copilot):
    assert client.post("/api/copilot/login", json={"kind": "entra"}).json()["step"] == "begin"
    assert client.post("/api/copilot/login/f1/advance", json={"input": "corp.ghe.com"}).json()["step"] == "advance"
    assert client.delete("/api/copilot/login/f1").status_code == 204
    assert client.post("/api/copilot/token", json={"token": "  ghp_x  ", "host": " github.com "}).status_code == 200
    assert client.post("/api/copilot/logout").status_code == 200
    assert copilot.calls == [("begin", "entra"), ("advance", "f1", "corp.ghe.com"), ("cancel", "f1"),
                             ("token", "ghp_x", "github.com"), ("logout",)]  # tokens and hosts trimmed


def test_copilot_failures_are_reported_as_such(client, monkeypatch):
    monkeypatch.setattr(copilot_api, "get_service", lambda: FakeCopilot(fail=True))
    for method, path, body in (("post", "/api/copilot/login", {"kind": "github"}),
                               ("post", "/api/copilot/login/f1/advance", {}), ("delete", "/api/copilot/login/f1", None),
                               ("post", "/api/copilot/token", {"token": "t"}), ("post", "/api/copilot/logout", None)):
        r = getattr(client, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 502 and r.json()["detail"].startswith("GitHub Copilot:"), path


def test_choosing_the_folder_in_use_changes_nothing(client, chooseable):  # noqa: F811
    before = data_folder.status()
    assert data_folder.change(str(settings.data_dir), copy_data=True) == before


def test_a_copy_that_fails_leaves_the_new_folder_empty_and_the_data_folder_unchanged(client, tmp_path, chooseable,  # noqa: F811
                                                                                    monkeypatch):
    import shutil

    new = tmp_path / "new"
    new.mkdir()
    current = settings.data_dir

    def half_copy(src, dst, **_kw):
        (dst / "partial.txt").write_text("half")
        (dst / "half-dir").mkdir()
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", half_copy)
    with pytest.raises(data_folder.DataFolderError) as failed:
        data_folder.change(str(new), copy_data=True)
    assert "unchanged" in str(failed.value) and "disk full" in str(failed.value)
    assert list(new.iterdir()) == [] and settings.data_dir == current
