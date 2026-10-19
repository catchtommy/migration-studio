"""The Orchestrator microservice guardrail check: a monolith whose orchestrator module the Orchestrator in monolith
step built, and the new service's repository the step lifts it into."""

import pytest

from app import contracts_store, modulith_scope
from app import orchestrator_service_guardrails as osg
from tests.test_module_guardrails import OWNERSHIP, git, put, repo  # noqa: F401
from tests.test_orchestrator_guardrails import ORCH, ORCHESTRATOR, extracted, orchestrate  # noqa: F401

SERVICE_POM = """<project>
  <parent>
    <groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>4.0.0</version>
  </parent>
  <groupId>com.acme</groupId><artifactId>broker-orchestrator-service</artifactId><version>1.0</version>
  <properties><java.version>25</java.version></properties>
  <dependencies>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-actuator</artifactId></dependency>
  </dependencies>
</project>
"""
API = """openapi: 3.1.0
info: {title: broker orchestrator, version: "1"}
paths:
  /internal/api/v1/broker/orchestrator/brokers:
    post: {operationId: createBroker, responses: {"201": {description: Created}}}
"""
SRC = "src/main/java/com/acme/broker/orchestrator"
DECL = {"feature": "create-broker", "domain": "broker", "orchestrator": "service", "module_commit": "abc",
        "openapi": "orchestrator-openapi.yaml",
        "operations": [{"method": "BrokerOrchestrator#create", "operation_id": "createBroker"}],
        "domain_api": [{"method": "BrokerService#create", "operation_id": "createBroker"}],
        "replaced": [{"file": "CustomerDirectory.java", "reason": "wired straight to its contract adapter"}]}


@pytest.fixture
def lift(extracted, tmp_path):
    """The monolith with its orchestrator module committed, and an empty service repository: (monolith, its commit,
    service, its starting commit)."""
    monolith, _ = extracted
    orchestrate(monolith)
    git(monolith, "add", "-A")
    git(monolith, "commit", "-q", "-m", "orchestrator")
    service = tmp_path / "service"
    service.mkdir()
    git(service, "init", "-q")
    put(service, "README.md", "broker orchestrator service\n")
    git(service, "add", "-A")
    git(service, "commit", "-q", "-m", "empty")
    return monolith, git(monolith, "rev-parse", "HEAD"), service, git(service, "rev-parse", "HEAD")


def build(service):
    """The correct lift: Spring Boot on Java 25, the orchestrator copied with a new package, a REST client of the
    domain service, configuration from the environment."""
    put(service, "pom.xml", SERVICE_POM)
    put(service, f"{SRC}/BrokerOrchestrator.java", ORCHESTRATOR.replace(
        "package com.acme.broker.orchestrator;", "package com.acme.broker.orchestrator;\n"))
    put(service, f"{SRC}/client/BrokerServiceClient.java",
        "package com.acme.broker.orchestrator.client;\npublic class BrokerServiceClient {\n"
        "  // calls POST /internal/api/v1/broker/brokers with RestClient\n}\n")
    put(service, "src/main/resources/application.yml",
        "broker:\n  service:\n    base-url: ${BROKER_SERVICE_URL}\n    timeout: 5s\n")


def run(lift, declaration=DECL, api=API):
    monolith, monolith_sha, service, base = lift
    return osg.check(service, base, monolith, monolith_sha, declaration, ORCH, api, OWNERSHIP, "broker")


def test_a_clean_lift_passes(lift):
    build(lift[2])
    assert run(lift) == []


@pytest.mark.parametrize("path, text, expect", [
    (f"{SRC}/Audit.java", "package x;\nimport org.springframework.jdbc.core.JdbcTemplate;\nclass Audit {}\n",
     "uses JdbcTemplate"),
    (f"{SRC}/BrokerEntity.java", "package x;\n@Entity\nclass BrokerEntity {}\n", "uses @Entity"),
    (f"{SRC}/Saga.java", "package x;\nclass Saga { @Transactional void run() {} }\n", "has no transactions"),
    ("src/main/resources/application.properties", "spring.datasource.url=${DB_URL}\n", "configures a datasource"),
    ("src/main/resources/db/V1__init.sql", "select 1;\n", "holds SQL"),
])
def test_the_service_holds_no_database(lift, path, text, expect):
    build(lift[2])
    put(lift[2], path, text)
    assert any(expect in p for p in run(lift)), run(lift)


def test_no_data_access_library(lift):
    build(lift[2])
    put(lift[2], "pom.xml", SERVICE_POM.replace("</dependencies>", """  <dependency>
      <groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId>
    </dependency>
  </dependencies>"""))
    assert any("is for data access" in p for p in run(lift))


def test_java_25_on_spring_boot(lift):
    build(lift[2])
    put(lift[2], "pom.xml", SERVICE_POM.replace("<java.version>25", "<java.version>8"))
    assert any("Java level must be 25" in p for p in run(lift))
    put(lift[2], "pom.xml", SERVICE_POM.replace("spring-boot-starter-parent", "acme-parent"))
    assert any("Spring Boot application" in p for p in run(lift))
    put(lift[2], "pom.xml", SERVICE_POM.replace("<version>4.0.0", "<version>3.3.5"))
    assert any("Spring Boot 3.3.5; the service uses Spring Boot 4" in p for p in run(lift))


def test_the_orchestrator_is_copied_unchanged_unless_replaced(lift):
    build(lift[2])
    put(lift[2], f"{SRC}/BrokerOrchestrator.java", ORCHESTRATOR.replace("customers.check(name);\n", ""))
    assert any("BrokerOrchestrator.java (main) differs" in p for p in run(lift))
    (lift[2] / SRC / "BrokerOrchestrator.java").unlink()
    assert any("BrokerOrchestrator.java (main) of the orchestrator module was not copied" in p for p in run(lift))
    replaced = {**DECL, "replaced": [*DECL["replaced"], {"file": "BrokerOrchestrator.java", "reason": "r"}]}
    assert run(lift, replaced) == []


def test_exactly_the_declared_operations(lift):
    build(lift[2])
    extra = API + "    get: {operationId: listBrokers, responses: {\"200\": {description: ok}}}\n"
    assert any("listBrokers is in the service's API but not" in p for p in run(lift, api=extra))


def test_the_monolith_is_untouched_and_secrets_stay_out(lift):
    build(lift[2])
    put(lift[0], "web/src/main/webapp/broker.jsp", "<html>changed</html>\n")
    put(lift[2], "src/main/resources/application.properties", "broker.service.token=abcd1234secret\n")
    out = "\n".join(run(lift))
    assert "The monolith repository changed" in out and "Possible secret" in out


def test_a_feature_without_an_orchestrator_service_changes_no_code(lift):
    none = {"feature": "create-broker", "domain": "broker", "orchestrator": "none", "reason": "no orchestrator"}
    assert run(lift, none) == []
    build(lift[2])
    assert any("there is no orchestrator service" in p for p in run(lift, none))


def test_the_declaration_is_checked_against_the_other_documents(tmp_path):
    with pytest.raises(contracts_store.InvalidDocument, match="reason"):
        contracts_store.parse_orchestrator_service("feature: f\ndomain: d\norchestrator: none\n")
    with pytest.raises(contracts_store.InvalidDocument, match="module_commit"):
        contracts_store.parse_orchestrator_service("feature: f\ndomain: d\norchestrator: service\n")

    (tmp_path / "orchestrator-extraction.yaml").write_text(
        "feature: f\ndomain: broker\norchestrator: module\nmodule: {path: o, artifact: 'com.acme:o'}\n"
        "operations: [{method: 'BrokerOrchestrator#create', replaces: 'BrokerService#create'},"
        " {method: 'BrokerOrchestrator#update', replaces: 'BrokerService#update'}]\n")
    (tmp_path / "openapi.yaml").write_text(API.replace("/orchestrator", ""))
    (tmp_path / "orchestrator-service-extraction.yaml").write_text(
        "feature: f\ndomain: broker\norchestrator: service\nmodule_commit: abc\nopenapi: orchestrator-openapi.yaml\n"
        "operations: [{method: 'BrokerOrchestrator#create', operation_id: createBroker}]\n"
        "domain_api: [{method: 'BrokerService#remove', operation_id: removeBroker}]\n")
    out = "\n".join(contracts_store.validate_outputs(tmp_path))
    assert "BrokerOrchestrator#update of the orchestrator module is not an operation of the service" in out
    assert "orchestrator-openapi.yaml does not exist" in out and "removeBroker" in out


def test_the_step_is_skipped_when_there_is_no_orchestrator(tmp_path):
    assert modulith_scope.skip_reason("orchestrator_service", tmp_path) is None  # nothing decided: run
    (tmp_path / "orchestrator-extraction.yaml").write_text(
        "feature: f\ndomain: broker\norchestrator: none\nreason: Controllers call the domain directly\n")
    reason = modulith_scope.skip_reason("orchestrator_service", tmp_path)
    assert reason.startswith(modulith_scope.NO_ORCHESTRATOR) and "Controllers call the domain directly" in reason
    note = modulith_scope.skip_note("orchestrator_service", "Orchestrator microservice", reason)
    assert "skipped" in note and "no orchestrator microservice to build" in note
    assert modulith_scope.skip_reason("microservice_scaffold", tmp_path) is None  # the domain service is always built
