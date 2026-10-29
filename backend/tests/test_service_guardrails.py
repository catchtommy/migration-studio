"""The microservice step's guardrail check, on a small monolith with its module and a separate service repository."""

from pathlib import Path

import pytest

from app import contracts_store, service_guardrails as sg
from tests.test_module_guardrails import BRIDGE, MOD, OWNERSHIP, extract, git, put, repo  # noqa: F401  (repo is a fixture)

SERVICE_POM = """<project>
  <groupId>com.acme.services</groupId><artifactId>broker-service</artifactId><version>1.0</version>
  <properties><maven.compiler.source>1.8</maven.compiler.source><maven.compiler.target>1.8</maven.compiler.target></properties>
  <dependencies>
    <dependency><groupId>com.ibatis</groupId><artifactId>ibatis-sqlmap</artifactId><version>2.3.4.726</version></dependency>
    <dependency><groupId>org.eclipse.jetty</groupId><artifactId>jetty-server</artifactId><version>9.4.44</version></dependency>
    <dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.12</version><scope>test</scope></dependency>
  </dependencies>
</project>
"""
OPENAPI = """openapi: 3.1.0
paths:
  /brokers:
    post: {operationId: createBroker}
  /health:
    get: {operationId: health}
"""
MODULE_DECL = {"modules": [{"path": MOD, "kind": "domain", "artifact": "com.acme:broker-profile-domain"}],
               "events": ["BrokerCreated"]}
SERVICE_DECL = {"operations": [{"method": "BrokerService#create", "operation_id": "createBroker"}],
                "replaced": [], "open_decisions": [], "events": ["BrokerCreated"]}
DESIGN = {"context_ports": []}
MAIN = "src/main/java/com/acme/svc"


def lift(monolith: Path, service: Path) -> None:
    """The correct lift: code and tests copied (package lines changed), SQL copied, a REST layer added."""
    git(service, "init", "-q") if not (service / ".git").exists() else None
    m = monolith / MOD
    for name in ("BrokerService", "BrokerServiceImpl"):
        code = (m / f"src/main/java/com/acme/broker/domain/{name}.java").read_text()
        put(service, f"{MAIN}/domain/{name}.java", code.replace("package com.acme.broker.domain;", "package com.acme.svc.domain;"))
    test = (m / "src/test/java/com/acme/broker/domain/BrokerServiceTest.java").read_text()
    put(service, "src/test/java/com/acme/svc/domain/BrokerServiceTest.java",
        test.replace("package com.acme.broker.domain;", "package com.acme.svc.domain;"))
    put(service, "src/main/resources/BrokerProfile.xml", (m / "src/main/resources/BrokerProfile.xml").read_text())
    put(service, "pom.xml", SERVICE_POM)
    put(service, f"{MAIN}/rest/BrokerResource.java",
        "package com.acme.svc.rest;\npublic class BrokerResource { public String create(String n) { return n; } }\n")


@pytest.fixture
def lifted(repo, tmp_path):  # noqa: F811
    monolith, _base = repo
    extract(monolith)
    git(monolith, "add", "-A")
    git(monolith, "commit", "-q", "-m", "module")
    monolith_base = git(monolith, "rev-parse", "HEAD")
    service = tmp_path / "service"
    service.mkdir()
    git(service, "init", "-q")
    lift(monolith, service)
    return monolith, monolith_base, service


def run(lifted, service_decl=SERVICE_DECL, design=DESIGN, openapi=OPENAPI, gods=None):
    monolith, monolith_base, service = lifted
    return sg.check(service, "4b825dc642cb6eb9a060e54bf8d69288fbee4904", monolith, monolith_base, MODULE_DECL,
                    service_decl, design, openapi, OWNERSHIP, "broker", gods)


def test_a_clean_lift_passes(lifted):
    assert run(lifted) == []


def test_the_monolith_must_stay_untouched(lifted):
    monolith, *_ = lifted
    put(monolith, "web/src/main/java/com/acme/web/New.java", "class New {}\n")
    assert any("The monolith repository changed" in p for p in run(lifted))


def test_copied_code_must_equal_the_modules(lifted):
    _, _, service = lifted
    f = service / MAIN / "domain/BrokerServiceImpl.java"
    f.write_text(f.read_text().replace("dao.insert(name)", "dao.insert(name.trim())"))
    assert any("BrokerServiceImpl.java (main) differs from the module's" in p for p in run(lifted))
    # declared as replaced: allowed
    assert not any("differs" in p for p in run(lifted, {**SERVICE_DECL, "replaced": [
        {"file": "BrokerServiceImpl.java", "reason": "uses the trimmed name"}]}))


def test_a_file_that_was_not_copied_is_reported(lifted):
    _, _, service = lifted
    (service / "src/test/java/com/acme/svc/domain/BrokerServiceTest.java").unlink()
    assert any("BrokerServiceTest.java (test) of the module was not copied" in p for p in run(lifted))


def test_sql_must_be_the_modules(lifted):
    _, _, service = lifted
    f = service / "src/main/resources/BrokerProfile.xml"
    f.write_text(f.read_text().replace("(NAME)   VALUES", "(NAME, AGE) VALUES"))
    assert any("is not a statement of the module" in p for p in run(lifted))


def test_libraries_versions_and_java_level(lifted):
    _, _, service = lifted
    pom = service / "pom.xml"
    pom.write_text(SERVICE_POM.replace("</dependencies>", """
    <dependency><groupId>org.newlib</groupId><artifactId>shiny</artifactId><version>1</version></dependency>
    <dependency><groupId>com.acme</groupId><artifactId>broker-profile-domain</artifactId></dependency>
    <dependency><groupId>com.azure</groupId><artifactId>azure-messaging-servicebus</artifactId></dependency>
  </dependencies>""").replace("2.3.4.726", "9.9").replace("<maven.compiler.source>1.8", "<maven.compiler.source>17"))
    out = "\n".join(run(lifted))
    assert "org.newlib:shiny" in out and "Ask the user" in out
    assert "com.acme:broker-profile-domain is a module of the monolith" in out
    assert "azure-messaging-servicebus" not in out  # infrastructure is allowed
    assert "declares version 9.9" in out and "must stay Java 8" in out


def test_no_secrets_ddl_or_ui(lifted):
    _, _, service = lifted
    put(service, "src/main/resources/app.properties", "servicebus.password=hunter22\nservicebus.token=${TOKEN}\n")
    put(service, "src/main/resources/V1.sql", "create table OUTBOX (id int);\n")
    put(service, "src/main/webapp/index.html", "<html/>\n")
    out = "\n".join(run(lifted))
    assert "Possible secret in src/main/resources/app.properties" in out and "${TOKEN}" not in out
    assert "New SQL script" in out and "UI file in the service" in out


def test_exactly_the_approved_operations(lifted):
    extra = {**SERVICE_DECL, "operations": [*SERVICE_DECL["operations"],
                                            {"method": "BrokerService#create", "operation_id": "deleteEverything"}]}
    out = "\n".join(run(lifted, extra))
    assert "deleteEverything is in microservice-extraction.yaml but not in openapi.yaml" in out
    missing = "\n".join(run(lifted, SERVICE_DECL, openapi=OPENAPI.replace("/health:", "/brokers/{id}:\n    get: {operationId: getBroker}\n  /health:")))
    assert "getBroker is in openapi.yaml but not in microservice-extraction.yaml" in missing
    assert any("has no operationId" in p for p in run(lifted, openapi=OPENAPI.replace("post: {operationId: createBroker}", "post: {}")))
    bad = {**SERVICE_DECL, "operations": [{"method": "BrokerService#vanish", "operation_id": "createBroker"}]}
    assert any("BrokerService#vanish is not a method of the module" in p for p in run(lifted, bad))


def test_only_designed_events(lifted):
    out = run(lifted, {**SERVICE_DECL, "events": ["BrokerCreated", "BrokerExploded"]})
    assert any("BrokerExploded" in p for p in out)


def test_every_context_port_needs_an_adapter(lifted):
    _, _, service = lifted
    design = {"context_ports": ["CurrentUser"]}
    assert any("Context port CurrentUser has no adapter" in p for p in run(lifted, design=design))
    put(service, f"{MAIN}/rest/HeaderCurrentUser.java",
        "package com.acme.svc.rest;\npublic class HeaderCurrentUser implements CurrentUser { }\n")
    assert run(lifted, design=design) == []


def test_god_modules_and_other_domains_stay_out(lifted):
    _, _, service = lifted
    put(service, f"{MAIN}/rest/Bad.java",
        "package com.acme.svc.rest;\nimport com.acme.common.MasterService;\nimport com.acme.customer.CustomerDao;\nclass Bad {}\n")
    out = "\n".join(run(lifted, gods=["com.acme.common.MasterService"]))
    assert "God module com.acme.common.MasterService" in out and "belongs to another domain" in out


def test_missing_declaration_is_reported(lifted):
    assert any("Write microservice-extraction.yaml" in p for p in run(lifted, service_decl=None))


def test_microservice_declaration_is_validated():
    ok = """feature: f
domain: broker
module_commit: abc1234
operations: [{method: BrokerService#create, operation_id: createBroker}]
replaced: []
open_decisions: [{topic: transactions, detail: OrderService#place}]
events: [BrokerCreated]
"""
    assert contracts_store.parse_microservice_extraction(ok)["module_commit"] == "abc1234"
    for old, new in (("module_commit: abc1234\n", ""), ("BrokerService#create", "create"), ("replaced: []\n", ""),
                     ("operations: [{method: BrokerService#create, operation_id: createBroker}]", "operations: []"),
                     ("open_decisions: [{topic: transactions, detail: OrderService#place}]", "open_decisions: [{topic: x}]")):
        with pytest.raises(contracts_store.InvalidDocument):
            contracts_store.parse_microservice_extraction(ok.replace(old, new))


def test_the_agent_tool_and_finish_check_use_the_service_repository(repo, tmp_path):  # noqa: F811
    from app.agents import common
    from app.agents.base_agent import AgentContext
    from app.agents.tools import Toolbox

    monolith, _ = repo
    extract(monolith)
    git(monolith, "add", "-A")
    git(monolith, "commit", "-q", "-m", "module")
    service = monolith / ".studio" / "repos" / "svc"
    service.mkdir(parents=True)
    git(service, "init", "-q")
    ctx = AgentContext(run_id="r", domain="broker", operation="create-broker", stage="microservice_scaffold", attempt=1,
                       workspace=monolith, feedback=None, settings={}, log=lambda _m: None,
                       config={"tools": ["check_service_guardrails"], "outputs": [], "checks": ["service_guardrails"]},
                       code_dir=".studio/repos/svc")
    ctx.ownership = OWNERSHIP
    box = Toolbox(ctx, None)
    assert ctx.base_sha and ctx.monolith_sha  # the empty service repository and the monolith's commit
    out = ctx.output_dir
    (out / "module-extraction.yaml").write_text(
        f"feature: create-broker\ndomain: broker\nscope: domain-only\nmodules:\n"
        f"  - {{path: {MOD}, kind: domain, artifact: \"com.acme:broker-profile-domain\"}}\nevents: [BrokerCreated]\n"
        f"bridge_dir: {BRIDGE}\nrouting: {{property: p, default: legacy, router: {BRIDGE}/R.java, routes: "
        "[{bean: a, legacy_bean: b, interface: I, module: J}]}\n")
    (out / "openapi.yaml").write_text(OPENAPI)
    (out / "microservice-extraction.yaml").write_text(
        "feature: create-broker\ndomain: broker\nmodule_commit: abc\noperations: "
        "[{method: BrokerService#create, operation_id: createBroker}]\nreplaced: []\nopen_decisions: []\n"
        "events: [BrokerCreated]\n")
    lift(monolith, service)
    assert box.run("check_service_guardrails", {}).startswith("Service guardrails: PASS")
    assert common.verify(ctx) == []
    put(service, "src/main/webapp/site.css", "a{}\n")
    assert any(p.startswith("Guardrail: UI file in the service") for p in common.verify(ctx))
    ctx.code_dir = "."
    assert "own repository" in sg.for_context(ctx)[0]

