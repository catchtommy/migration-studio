"""What keeps an agent within its step: the tools' limits, the checks that can't be switched off, and what the
workflow undoes or refuses after the agent finished."""

import subprocess
from pathlib import Path

import pytest

from app import release_guardrails as rg
from app.agents.common import required_checks
from app.agents.defaults import default_config
from app.agents.tools import ToolError, Toolbox
from app.orchestrator.stages import StageDef
from app.tools import git_ops
from tests.conftest import wait_for
from tests.test_agents_domains import ctx  # noqa: F401
from tests.test_delivery import git, setup_project
from tests.test_projects import make_monolith


def _code_step(ctx, code_dir=".", locked=()):
    ctx.config = {**default_config("modular_monolith"), "mode": "llm"}
    ctx.stage = "modular_monolith"
    ctx.stage_def = StageDef("modular_monolith", "Module in monolith", "agent", "", code={"repository": ""})
    ctx.code_dir = code_dir
    ctx.locked_documents = set(locked)
    return Toolbox(ctx, None)


def test_a_code_step_writes_only_in_its_own_repository(ctx):
    ctx, _, _ = ctx
    (ctx.workspace / ".studio/repos/service").mkdir(parents=True)
    tools = _code_step(ctx)
    assert tools.run("write_file", {"path": "src/LoanPort.java", "content": "x"}).startswith("Wrote")
    assert tools.run("write_file", {"path": ".migration/loan/approve/notes.md", "content": "x"}).startswith("Wrote")
    with pytest.raises(ToolError, match="outside the repository this step changes"):
        tools.run("write_file", {"path": ".studio/repos/service/App.java", "content": "x"})  # another repository

    tools = _code_step(ctx, code_dir=".studio/repos/service")
    assert tools.run("write_file", {"path": ".studio/repos/service/App.java", "content": "x"}).startswith("Wrote")
    with pytest.raises(ToolError, match="outside the repository this step changes"):
        tools.run("write_file", {"path": "src/LoanController.java", "content": "x"})  # the monolith


def test_approved_documents_can_be_read_but_not_changed(ctx):
    ctx, _, _ = ctx
    doc = ".migration/loan/approve/domain-design.yaml"
    (ctx.workspace / doc).parent.mkdir(parents=True, exist_ok=True)
    (ctx.workspace / doc).write_text("domain: loan\n")
    tools = _code_step(ctx, locked={doc})
    assert "domain: loan" in tools.run("read_file", {"path": doc})
    for name, args in (("write_file", {"path": doc, "content": "x"}),
                       ("edit_file", {"path": doc, "old_text": "loan", "new_text": "x"})):
        with pytest.raises(ToolError, match="a gate has already approved"):
            tools.run(name, args)


@pytest.mark.parametrize("command", ["git push origin HEAD", "git -C . remote add x y", "git config user.name x",
                                     "git commit --amend -m x", "git checkout -b other", "git rebase main",
                                     "make && git push", "sudo rm -rf /tmp/x"])
def test_commands_cannot_publish_or_rewrite_git(ctx, command):
    ctx, _, _ = ctx
    with pytest.raises(ToolError, match="is not allowed"):
        _code_step(ctx).run("run_command", {"command": command})


def test_commands_commit_locally_and_never_see_the_servers_secrets(ctx, monkeypatch):
    ctx, _, _ = ctx
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("STUDIO_DATABASE_URL", "postgresql://u:p@db/x")
    tools = _code_step(ctx)
    out = tools.run("run_command", {"command": "env"})
    assert "sk-secret" not in out and "postgresql://" not in out and "PATH=" in out
    out = tools.run("run_command", {"command": "git add -A && git -c user.name=m -c user.email=m@x commit -qm step "
                                               "&& git revert --no-edit HEAD && git log --oneline | wc -l"})
    assert out.startswith("exit code 0")


def test_an_agent_gets_the_check_tools_of_its_checks_and_no_others(ctx):
    from app.agents.common import tools_of

    ctx, _, _ = ctx
    ctx.stage = "modular_monolith"
    ctx.config = {**default_config("modular_monolith"), "tools": ["read_file", "edit_file"]}  # none chosen
    assert tools_of(ctx) == ["read_file", "edit_file", "check_module_guardrails"]
    ctx.config["tools"] = ["read_file", "check_service_guardrails", "check_boundaries"]  # not its checks: dropped
    assert tools_of(ctx) == ["read_file", "check_module_guardrails"]
    ctx.stage, ctx.config = "modernise_and_test", default_config("modernise_and_test")
    assert {"check_boundaries", "check_test_guardrails", "check_release_guardrails"} <= set(tools_of(ctx))
    ctx.stage, ctx.config = "domain_design", default_config("domain_design")  # a design step: no check tools
    assert not [t for t in tools_of(ctx) if t.startswith("check_")]


def test_a_built_in_agent_always_passes_its_own_guardrails(ctx):
    ctx, _, _ = ctx
    ctx.stage, ctx.config = "modular_monolith", {**default_config("modular_monolith"), "checks": []}  # none stored
    assert "module_guardrails" in required_checks(ctx)
    ctx.stage, ctx.config = "my_custom_agent", {"checks": ["boundary"]}  # a custom agent: what it is configured with
    assert required_checks(ctx) == {"boundary"}


def _repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "base")
    return git(path, "rev-parse", "HEAD")


BOOT_POM = """<project>
  <parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>4.0.0</version></parent>
  <artifactId>customer-service</artifactId>
  <properties><java.version>25</java.version></properties>
  <dependencies>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId></dependency>
  </dependencies>
</project>
"""


def test_modernisation_keeps_the_schema_and_reaches_the_target_stack(tmp_path):
    service = tmp_path / "service"
    base = _repo(service, {"pom.xml": "<project><artifactId>customer-service</artifactId></project>\n"})
    (service / "pom.xml").write_text(BOOT_POM)
    (service / "src/main/resources").mkdir(parents=True)
    (service / "src/main/resources/application.yml").write_text("spring:\n  jpa:\n    hibernate:\n      ddl-auto: none\n")
    assert rg.modernise(service, base, None, None) == []

    for path, text, expect in [
        ("src/main/resources/application.yml", "spring:\n  jpa:\n    hibernate:\n      ddl-auto: update\n", "ddl-auto: update"),
        ("src/main/resources/application.properties", "spring.jpa.generate-ddl=true\n", "generate-ddl"),
        ("src/main/resources/schema.sql", "select 1;\n", "New SQL script"),
        ("src/main/java/Fix.java", 'class Fix { String s = "ALTER TABLE CUSTOMER ADD COLUMN X"; }\n', "DDL in"),
        ("src/main/resources/application.properties", "customer.db.password=hunter2secret\n", "Possible secret"),
    ]:
        (service / path).parent.mkdir(parents=True, exist_ok=True)
        (service / path).write_text(text)
        assert any(expect in p for p in rg.modernise(service, base, None, None)), (path, text)
        (service / path).unlink() if path != "src/main/resources/application.yml" else \
            (service / path).write_text("spring:\n  jpa:\n    hibernate:\n      ddl-auto: validate\n")
    (service / "pom.xml").write_text(BOOT_POM.replace("4.0.0", "3.4.1").replace("<java.version>25", "<java.version>17")
                                     .replace("</dependencies>", "  <dependency><groupId>org.flywaydb</groupId>"
                                              "<artifactId>flyway-core</artifactId></dependency>\n  </dependencies>"))
    out = "\n".join(rg.modernise(service, base, None, None))
    assert "Spring Boot 4" in out and "Java level is 25" in out and "flyway-core manages the schema" in out


MONOLITH_POM = """<project>
  <artifactId>web</artifactId>
  <dependencies>
    <dependency><groupId>org.springframework</groupId><artifactId>spring-web</artifactId><version>3.2.18</version></dependency>
  </dependencies>
</project>
"""


def test_the_monolith_integration_only_adds_the_switch(tmp_path):
    monolith = tmp_path / "monolith"
    base = _repo(monolith, {"pom.xml": MONOLITH_POM, "web/index.jsp": "<html/>\n",
                            "customer-domain/view/domain/CustomerService.java": "class CustomerService {}\n",
                            "web/src/main/resources/Customer.xml": '<sqlMap namespace="c"><select id="s">SELECT NAME '
                                                                   "FROM CUSTOMER</select></sqlMap>\n"})
    (monolith / "pom.xml").write_text(MONOLITH_POM.replace("</dependencies>", "  <dependency><groupId>com.azure"
                                      "</groupId><artifactId>azure-messaging-servicebus</artifactId><version>7.17.0"
                                      "</version></dependency>\n  </dependencies>"))
    (monolith / "web/src/main/java").mkdir(parents=True)
    (monolith / "web/src/main/java/CustomerServiceClient.java").write_text("class CustomerServiceClient {}\n")
    modules = ["customer-domain/view/domain"]
    assert rg.integration(monolith, base, modules) == []

    (monolith / "web/index.jsp").write_text("<html>changed</html>\n")
    (monolith / "customer-domain/view/domain/CustomerService.java").write_text("class CustomerService { int flag; }\n")
    (monolith / "web/src/main/resources/Customer.xml").write_text('<sqlMap namespace="c"><select id="s">SELECT NAME, '
                                                                   "X FROM CUSTOMER</select></sqlMap>\n")
    (monolith / "db/outbox.sql").parent.mkdir()
    (monolith / "db/outbox.sql").write_text("CREATE TABLE OUTBOX (ID NUMBER);\n")
    (monolith / "pom.xml").write_text(MONOLITH_POM.replace("3.2.18", "5.3.0").replace("</dependencies>",
                                      "  <dependency><groupId>com.google.guava</groupId><artifactId>guava</artifactId>"
                                      "</dependency>\n  </dependencies>"))
    out = "\n".join(rg.integration(monolith, base, modules))
    for expect in ("UI file changed: web/index.jsp", "in the module customer-domain/view/domain", "SQL changed in",
                   "New SQL script db/outbox.sql", "spring-web was removed or changed", "guava is not an HTTP client"):
        assert expect in out, expect


def test_a_step_that_moves_its_branch_fails_and_changes_elsewhere_are_undone(client, tmp_path, monkeypatch):
    """Run through the workflow: a code step that switches branches fails (its work is undone); a code step that also
    changes another repository has those changes undone."""
    from app.agents import stub

    upstream, key, proj = setup_project(client, tmp_path)  # the first agent step changes the monolith
    service = tmp_path / "loan-service"
    make_monolith(service)
    wf = proj.get("/api/workflows/standard").json()
    agents = [st["key"] for st in wf["steps"] if st["kind"] == "agent"]
    steps = [{**st, "code": {"repository": str(service)}} if st.get("key") == agents[1] else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    original, mode = stub.StubAgent.run, {"bad": "branch"}

    def run(self, ctx):
        out = original(self, ctx)
        if self.stage == agents[0] and mode["bad"] == "branch":
            subprocess.run(["git", "checkout", "-q", "-b", "elsewhere"], cwd=ctx.workspace, check=True)
        if self.stage == agents[1]:  # also changes the monolith, outside its own repository
            (ctx.workspace / "SNEAKY.md").write_text("x\n")
        return out

    monkeypatch.setattr(stub.StubAgent, "run", run)
    run_id = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()["id"]
    run = wait_for(proj, run_id, {"awaiting_branches"}, timeout=30)
    proj.post(f"/api/runs/{run_id}/branches", json={"feature_branch": run["branch"]})
    failed = wait_for(proj, run_id, {"failed"}, timeout=30)
    assert "not the run's branch" in failed["error"]
    ws = Path(failed["workspace"])
    assert git(ws, "symbolic-ref", "--short", "HEAD") == run["branch"]  # put back

    mode["bad"] = None
    assert proj.post(f"/api/runs/{run_id}/retry").status_code == 200
    while True:
        run = wait_for(proj, run_id, {"awaiting_branches", "awaiting_commit", "awaiting_approval", "awaiting_publish"},
                       timeout=30)
        if run["current_stage"] == agents[1] and run["status"] == "awaiting_commit":
            break
        if run["status"] == "awaiting_branches":
            proj.post(f"/api/runs/{run_id}/branches", json={"feature_branch": run["branch"]})
        elif run["status"] == "awaiting_commit":
            assert proj.post(f"/api/runs/{run_id}/finish").status_code == 204
        elif run["status"] == "awaiting_publish":
            assert proj.post(f"/api/runs/{run_id}/docs/skip").status_code == 204
        else:
            assert proj.post(f"/api/runs/{run_id}/decision", json={"decision": "approve"}).status_code == 200
    assert not (ws / "SNEAKY.md").exists()
    from app.db.models import AuditEvent
    from app.db.session import session_scope
    from sqlalchemy import select
    with session_scope() as s:
        log = " ".join(e.message for e in s.scalars(select(AuditEvent).where(AuditEvent.run_id == run_id)))
    assert "its changes elsewhere were undone: SNEAKY.md" in log


def test_rerunning_an_approved_step_with_an_updated_agent_updates_its_documents(client, monkeypatch):
    """Approved documents are read-only to the steps after their gate, not to their own step: re-running the workflow
    from that step (after changing its agent) rewrites them, the gate asks again, and later steps get the new version
    (and may not change it in turn)."""
    from app.agents import stub
    from tests.conftest import start_run
    from tests.test_workflow import decide

    original, seen = stub.StubAgent.run, {}
    version = {"text": "first version"}

    def run(self, ctx):
        out = original(self, ctx)
        if self.stage == "as_is_doc":  # the agent's instructions decide what it writes
            (ctx.output_dir / "as-is.md").write_text(f"# AS-IS\n{version['text']}\n")
        if self.stage == "deep_dive":
            seen["locked"] = sorted(ctx.locked_documents)
            seen["read"] = (ctx.output_dir / "as-is.md").read_text()
        if self.stage == "domain_dependencies":  # after the analysis gate
            seen["after_gate"] = sorted(ctx.locked_documents)
            (ctx.output_dir / "as-is.md").write_text("# AS-IS\nquietly rewritten\n")  # through a command, say
        return out

    monkeypatch.setattr(stub.StubAgent, "run", run)
    r = start_run(client, {"domain": "customer", "operation": "rerun-approved"})  # its own feature: no clash
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})  # AS-IS and deep dive done, the analysis gate waits
    decide(client, run_id, decision="approve")
    wait_for(client, run_id, {"awaiting_approval", "awaiting_publish"})

    version["text"] = "second version, from the updated agent"
    assert client.post(f"/api/runs/{run_id}/restart", json={"stage": "as_is_doc"}).status_code == 200
    run = wait_for(client, run_id, {"awaiting_approval"})
    assert run["pending_gate"]["gate"] == "gate_analysis"  # approved again, with the new document

    docs = [a for a in client.get(f"/api/runs/{run_id}/artifacts").json() if a["name"] == "as-is.md"]
    latest = client.get(f"/api/artifacts/{max(docs, key=lambda a: a['attempt'])['id']}").json()["content"]
    assert "second version" in latest  # the step rewrote its own approved document
    assert "second version" in seen["read"]  # the deep dive, re-run after it, read the new version
    assert not any(p.endswith("as-is.md") for p in seen["locked"])  # nothing locked yet: its gate comes after both

    decide(client, run_id, decision="approve")  # approved again; the steps after the gate run
    run = wait_for(client, run_id, {"awaiting_approval", "awaiting_publish"})
    if run["status"] == "awaiting_publish":
        assert client.post(f"/api/runs/{run_id}/docs/skip").status_code == 204
        wait_for(client, run_id, {"awaiting_approval"})
    assert any(p.endswith("as-is.md") for p in seen["after_gate"])  # read-only after its gate
    docs = [a for a in client.get(f"/api/runs/{run_id}/artifacts").json() if a["name"] == "as-is.md"]
    assert {a["stage"] for a in docs} == {"as_is_doc"}  # the later step's rewrite was not kept
    latest = client.get(f"/api/artifacts/{max(docs, key=lambda a: a['attempt'])['id']}").json()["content"]
    assert "second version" in latest


def test_commands_keep_what_builds_need_behind_a_proxy_and_for_testcontainers(ctx, monkeypatch):
    ctx, _, _ = ctx
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.setenv("TESTCONTAINERS_RYUK_DISABLED", "true")
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Djavax.net.ssl.trustStore=/etc/corp.jks")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_notforagents")
    out = _code_step(ctx).run("run_command", {"command": "env"})
    assert "DOCKER_HOST=" in out and "TESTCONTAINERS_RYUK_DISABLED=" in out and "trustStore" in out
    assert "ghp_notforagents" not in out


def test_the_spring_boot_version_may_come_from_a_property(tmp_path):
    service = tmp_path / "service"
    base = _repo(service, {"pom.xml": "<project><artifactId>customer-service</artifactId></project>\n"})
    bom = """<project>
  <artifactId>customer-service</artifactId>
  <properties><java.version>25</java.version><spring-boot.version>4.0.1</spring-boot.version></properties>
  <dependencyManagement><dependencies><dependency>
    <groupId>org.springframework.boot</groupId><artifactId>spring-boot-dependencies</artifactId>
    <version>${spring-boot.version}</version><type>pom</type><scope>import</scope>
  </dependency></dependencies></dependencyManagement>
</project>
"""
    (service / "pom.xml").write_text(bom)
    assert rg.modernise(service, base, None, None) == []
    (service / "pom.xml").write_text(bom.replace(">4.0.1<", ">3.5.0<"))
    assert any("Spring Boot 4 (its parent or BOM, found 3.5.0)" in p for p in rg.modernise(service, base, None, None))


def test_steps_changing_the_monolith_run_focused_tests_not_the_whole_legacy_suite(ctx):
    """The monolith's modules are large: Module in monolith and Orchestrator in monolith run the tests of the classes
    they change (or a module they created), never a legacy module's whole suite."""
    from app.agents.common import brief

    ctx, _, _ = ctx
    (ctx.workspace / "loan-web").mkdir()
    (ctx.workspace / "loan-web" / "pom.xml").write_text("<project/>")
    git_ops.git(ctx.workspace, "add", "-A")
    git_ops.git(ctx.workspace, "commit", "-qm", "legacy module")
    tools = _code_step(ctx)  # Module in monolith, starting now
    (ctx.workspace / "loan-domain").mkdir()  # the module this step creates
    (ctx.workspace / "loan-domain" / "pom.xml").write_text("<project/>")

    refused = ["mvn test", "mvn -q -o verify", "./mvnw clean install", "mvn -pl loan-web test",
               "mvn.cmd -pl loan-web,loan-domain package", "gradle build", "./gradlew :loan-web:test",
               "cd loan-web && mvn test"]
    for command in refused:
        with pytest.raises(ToolError, match="whole test suite of the legacy code"):
            tools.run("run_command", {"command": command})
    allowed = ["mvn -q -o -pl loan-web test -Dtest=LoanServiceTest,LoanRouterTest -Dsurefire.failIfNoSpecifiedTests=false",
               "mvn -q -o install -DskipTests", "mvn -Dmaven.test.skip=true package", "mvn -q compile",
               "mvn -q test-compile", "mvn -pl loan-domain test", "gradle :loan-web:test --tests '*LoanService*'",
               "gradle build -x test", "git status"]
    for command in allowed:
        assert tools._unfocused_tests(command) is None, command
    assert "never the whole legacy suite" in brief(ctx) and "-Dtest=" in brief(ctx)

    ctx.stage = "microservice_scaffold"  # a service of its own: its whole (copied) suite is the point
    assert tools._unfocused_tests("mvn test") is None
