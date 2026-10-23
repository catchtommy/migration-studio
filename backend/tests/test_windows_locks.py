"""Windows won't delete or move a folder while another program holds a file in it (WinError 145: directory not
empty; 32: in use). The studio waits a few seconds for it, and replacing a workspace doesn't depend on it at all."""

import os
import shutil
from pathlib import Path

import pytest

from app import workspace
from app.tools import git_ops


def _held(code: int) -> OSError:
    e = OSError(code, "The directory is not empty" if code == 145 else "in use")
    e.winerror = code  # as Windows raises it
    return e


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(git_ops.time, "sleep", lambda s: None)


def test_a_folder_held_for_a_moment_is_deleted_once_it_is_released(tmp_path, monkeypatch):
    folder = tmp_path / "ws"
    (folder / "src").mkdir(parents=True)
    (folder / "src" / "App.java").write_text("class App {}")
    real, calls = shutil.rmtree, {"n": 0}

    def rmtree(path, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:  # an antivirus scan holds a file for the first two tries
            raise _held(145)
        return real(path, **kw)

    monkeypatch.setattr(git_ops.shutil, "rmtree", rmtree)
    git_ops.remove_tree(folder)
    assert not folder.exists() and calls["n"] == 3


def test_a_folder_that_stays_held_gives_an_error_that_says_what_to_do(tmp_path, monkeypatch):
    folder = tmp_path / "ws"
    folder.mkdir()
    monkeypatch.setattr(git_ops.shutil, "rmtree", lambda path, **kw: (_ for _ in ()).throw(_held(145)))
    with pytest.raises(git_ops.PathInUse) as e:
        git_ops.remove_tree(folder)
    assert str(folder) in str(e.value) and "program still has a file in it open" in str(e.value)
    assert "exclude the studio's data folder" in str(e.value)


def test_other_errors_are_not_retried(tmp_path, monkeypatch):
    calls = {"n": 0}

    def rmtree(path, **kw):
        calls["n"] += 1
        raise FileExistsError("something else")

    (tmp_path / "ws").mkdir()
    monkeypatch.setattr(git_ops.shutil, "rmtree", rmtree)
    with pytest.raises(FileExistsError):
        git_ops.remove_tree(tmp_path / "ws")
    assert calls["n"] == 1


def test_a_rename_waits_for_a_held_folder(tmp_path, monkeypatch):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.mkdir()
    real, calls = os.rename, {"n": 0}

    def rename(a, b):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _held(32)
        return real(a, b)

    monkeypatch.setattr(git_ops.os, "rename", rename)
    git_ops.rename_with_retry(src, dst)
    assert dst.is_dir() and not src.exists()


def test_replacing_a_workspace_goes_on_when_the_old_one_cant_be_deleted_yet(tmp_path, monkeypatch):
    ws, new = tmp_path / "run1", tmp_path / "run1.monolith"
    (ws / ".migration").mkdir(parents=True)
    (ws / "old.txt").write_text("the documents-only workspace")
    new.mkdir()
    (new / "App.java").write_text("the monolith's checkout")
    real = git_ops.remove_tree
    monkeypatch.setattr(git_ops, "remove_tree", lambda p: (_ for _ in ()).throw(
        git_ops.PathInUse(145, "held")) if workspace.LEFTOVER in Path(p).name else real(p))
    logged = []

    workspace._swap_in(new, ws, logged.append)
    assert (ws / "App.java").read_text() == "the monolith's checkout"  # the step can go on
    left = [p for p in tmp_path.iterdir() if workspace.LEFTOVER in p.name]
    assert len(left) == 1 and (left[0] / "old.txt").exists()
    assert logged and "to delete later" in logged[0]

    monkeypatch.setattr(git_ops, "remove_tree", real)  # released later: the next start deletes it
    assert workspace.cleanup_leftovers(tmp_path) == 1
    assert not left[0].exists() and ws.exists()
