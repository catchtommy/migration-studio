"""The Orchestrator in monolith guardrail check, on the throw-away monolith of the module guardrail tests, after the
Module in monolith step has extracted the domain module."""

import pytest

from app import contracts_store
from app import orchestrator_guardrails as og
from tests.test_module_guardrails import (CONTEXT, CONTEXT_PATH, CONTROLLER, CONTROLLER_PATH, DECLARATION, MOD,
                                          OWNERSHIP, ROOT_POM, WEB_POM, extract, git, put, repo)  # noqa: F401

ORCH = "broker-domain/broker-profile/orchestrator"
ORCH_POM = """<project>
  <parent><groupId>com.acme</groupId><artifactId>monolith</artifactId><version>1.0</version></parent>
  <artifactId>broker-profile-orchestrator</artifactId>
  <dependencies>
    <dependency>
      <groupId>com.acme</groupId>
      <artifactId>broker-profile-domain</artifactId>
    </dependency>
  </dependencies>
</project>
"""
ORCHESTRATOR = """package com.acme.broker.orchestrator;

import com.acme.broker.domain.BrokerService;

/** Sequences the domain API and the calls to other domains; no database access of its own. */
public class BrokerOrchestrator {
    private BrokerService brokerService;
    private CustomerDirectory customers;

    public String create(String name) {
        customers.check(name);
        return brokerService.create(name);
    }
}
"""
ORCH_CLASS = f"{ORCH}/src/main/java/com/acme/broker/orchestrator/BrokerOrchestrator.java"
DECL = {"feature": "create-broker", "domain": "broker", "orchestrator": "module",
        "module": {"path": ORCH, "artifact": "com.acme:broker-profile-orchestrator"},
        "operations": [{"method": "BrokerOrchestrator#create", "replaces": "BrokerService#create"}],
        "outside_changes": [CONTROLLER_PATH, CONTEXT_PATH]}


@pytest.fixture
def extracted(repo):
    """The monolith as the Module in monolith step left it: the step's starting commit."""
    root, _ = repo
    extract(root)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "module")
    return root, git(root, "rev-parse", "HEAD")


def orchestrate(root):
    """The correct orchestrator: its own module on the domain module, controllers re-pointed by type only."""
    put(root, f"{ORCH}/pom.xml", ORCH_POM)
    put(root, ORCH_CLASS, ORCHESTRATOR)
    put(root, f"{ORCH}/src/main/java/com/acme/broker/orchestrator/CustomerDirectory.java",
        "package com.acme.broker.orchestrator;\npublic interface CustomerDirectory { void check(String name); }\n")
    controller = CONTROLLER.replace("com.acme.broker.BrokerService", "com.acme.broker.orchestrator.BrokerOrchestrator")
    put(root, CONTROLLER_PATH, controller.replace("private BrokerService brokerService;",
                                                  "private BrokerOrchestrator brokerService;"))
    context = (root / CONTEXT_PATH).read_text()
    put(root, CONTEXT_PATH, context.replace(
        "</beans>", '  <bean id="brokerOrchestrator" class="com.acme.broker.orchestrator.BrokerOrchestrator"/>\n</beans>'))
    pom = (root / "pom.xml").read_text()
    put(root, "pom.xml", pom.replace(f"<module>{MOD}</module>", f"<module>{MOD}</module>\n    <module>{ORCH}</module>"))
    web = (root / "web/pom.xml").read_text()
    put(root, "web/pom.xml", web.replace("  </dependencies>", """    <dependency>
      <groupId>com.acme</groupId>
      <artifactId>broker-profile-orchestrator</artifactId>
      <version>${project.version}</version>
    </dependency>
  </dependencies>"""))


def run(root, base, declaration=DECL):
    return og.check(root, base, declaration, DECLARATION, None, OWNERSHIP, "broker")


def test_a_clean_orchestrator_passes(extracted):
    root, base = extracted
    orchestrate(root)
    assert run(root, base) == []


@pytest.mark.parametrize("line, what", [
    ("import org.springframework.jdbc.core.JdbcTemplate;", "JdbcTemplate"),
    ("import com.ibatis.sqlmap.client.SqlMapClient;", "com.ibatis"),
    ("private BrokerDao brokerDao;", "BrokerDao"),
    ("import javax.sql.DataSource;", "javax.sql"),
])
def test_the_orchestrator_holds_no_database_access(extracted, line, what):
    root, base = extracted
    orchestrate(root)
    put(root, ORCH_CLASS, ORCHESTRATOR.replace("public class BrokerOrchestrator {", f"{line}\npublic class BrokerOrchestrator {{"))
    out = "\n".join(run(root, base))
    assert f"uses {what}" in out and "no database access" in out


def test_comments_mentioning_the_dao_are_fine(extracted):
    root, base = extracted
    orchestrate(root)
    put(root, ORCH_CLASS, ORCHESTRATOR.replace("public class", "// The BrokerDao stays in the domain module.\npublic class"))
    assert run(root, base) == []


def test_the_orchestrator_has_no_transaction_of_its_own(extracted):
    root, base = extracted
    orchestrate(root)
    put(root, ORCH_CLASS, ORCHESTRATOR.replace("    public String create", "    @Transactional\n    public String create"))
    assert any("no transaction of its own" in p for p in run(root, base))


def test_the_orchestrator_holds_no_sql_map(extracted):
    root, base = extracted
    orchestrate(root)
    put(root, f"{ORCH}/src/main/resources/Broker.xml",
        '<sqlMap namespace="b"><select id="s">SELECT NAME FROM BROKER</select></sqlMap>\n')
    assert any("is an SQL map" in p for p in run(root, base))


def test_the_domain_module_does_not_change(extracted):
    root, base = extracted
    orchestrate(root)
    service = root / MOD / "src/main/java/com/acme/broker/domain/BrokerService.java"
    service.write_text(service.read_text().replace("return dao.insert(name);", "return dao.insert(name.trim());"))
    assert any("The domain module changed" in p for p in run(root, base))


def test_the_orchestrator_depends_on_the_domain_module(extracted):
    root, base = extracted
    orchestrate(root)
    put(root, f"{ORCH}/pom.xml", ORCH_POM.replace("""    <dependency>
      <groupId>com.acme</groupId>
      <artifactId>broker-profile-domain</artifactId>
    </dependency>
""", ""))
    assert any("does not depend on the domain module" in p for p in run(root, base))


def test_controllers_change_only_by_type_and_import(extracted):
    root, base = extracted
    orchestrate(root)
    controller = (root / CONTROLLER_PATH).read_text()
    put(root, CONTROLLER_PATH, controller.replace("return brokerService.create(name);",
                                                  "return brokerService.create(name.trim());"))
    assert any("Edit outside the modules" in p and "BrokerController" in p for p in run(root, base))


def test_a_feature_without_an_orchestrator_changes_no_code(extracted):
    root, base = extracted
    none = {"feature": "create-broker", "domain": "broker", "orchestrator": "none", "reason": "domain-only"}
    assert run(root, base, none) == []
    orchestrate(root)
    assert any("there is no orchestrator" in p for p in run(root, base, none))


def test_the_declaration_is_checked_against_the_other_documents(tmp_path):
    with pytest.raises(contracts_store.InvalidDocument, match="reason"):
        contracts_store.parse_orchestrator_extraction("feature: f\ndomain: d\norchestrator: none\n")
    with pytest.raises(contracts_store.InvalidDocument, match="defaults to local"):
        contracts_store.parse_orchestrator_extraction(
            "feature: f\ndomain: d\norchestrator: module\nmodule: {path: o, artifact: 'a:b'}\n"
            "operations: [{method: A#b, replaces: C#b}]\nports: [{port: P, contract: c.k, property: p, default: contract}]\n")

    (tmp_path / "domain-dependencies.yaml").write_text("decision: {scope: domain-only, reason: r}\ndependencies: []\n")
    (tmp_path / "module-extraction.yaml").write_text(
        f"feature: f\ndomain: broker\nscope: domain-only\nmodules: [{{path: {MOD}, kind: domain, artifact: 'com.acme:d'}}]\n")
    (tmp_path / "orchestrator-extraction.yaml").write_text(
        f"feature: f\ndomain: broker\norchestrator: module\nmodule: {{path: {MOD}/orchestrator, artifact: 'com.acme:o'}}\n"
        "operations: [{method: A#b, replaces: C#b}]\n")
    out = "\n".join(contracts_store.validate_outputs(tmp_path))
    assert "domain-only, so it has no orchestrator" in out and "overlaps the domain module" in out
