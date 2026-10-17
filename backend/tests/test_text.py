"""Legacy code that isn't UTF-8 reaches agents as its real characters, never as U+FFFD."""

import subprocess

from app.agents.base_agent import AgentContext
from app.agents.tools import Toolbox
from app.text import decode, decode_with


def test_decoding_falls_back_from_utf8_to_windows_1252():
    assert decode("Café → ok".encode()) == "Café → ok"
    assert decode("﻿with a byte order mark".encode()) == "with a byte order mark"
    assert decode_with("Café © 2004 “quoted”".encode("cp1252")) == ("Café © 2004 “quoted”", "cp1252")
    assert "�" not in decode(bytes(range(256)))  # any bytes at all decode to real characters


def test_agents_read_search_and_edit_legacy_files_in_their_own_encoding(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    legacy = tmp_path / "Legacy.java"
    legacy.write_bytes("// Café © 2004 – “legacy”\nclass Legacy {}\n".encode("cp1252"))
    ctx = AgentContext(run_id="r", domain="d", operation="o", stage="s", attempt=1, workspace=tmp_path, feedback=None,
                       settings={}, log=lambda m: None,
                       config={"tools": ["read_file", "search_code", "edit_file", "write_file"]})
    box = Toolbox(ctx, None)
    read = box.run("read_file", {"path": "Legacy.java"})
    assert "Café © 2004 – “legacy”" in read and "�" not in read
    found = box.run("search_code", {"pattern": "Caf"})
    assert "Café ©" in found and "�" not in found
    box.run("edit_file", {"path": "Legacy.java", "old_text": "class Legacy {}", "new_text": "class Legacy { int é; }"})
    assert legacy.read_bytes().decode("cp1252") == "// Café © 2004 – “legacy”\nclass Legacy { int é; }\n"  # still cp1252
    box.run("write_file", {"path": "New.md", "content": "Café → doc\n"})
    assert (tmp_path / "New.md").read_bytes() == "Café → doc\n".encode()  # new files: UTF-8
