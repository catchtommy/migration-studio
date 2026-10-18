"""The Module in monolith guardrail check, on a small throw-away Maven monolith."""

import subprocess
from pathlib import Path

import pytest

from app import module_guardrails as g

ROOT_POM = """<project>
  <groupId>com.acme</groupId><artifactId>monolith</artifactId><version>1.0</version>
  <modules>
    <module>web</module>
  </modules>
</project>
"""
WEB_POM = """<project>
  <parent><groupId>com.acme</groupId><artifactId>monolith</artifactId><version>1.0</version></parent>
  <artifactId>web</artifactId>
  <dependencies>
    <dependency>
      <groupId>com.ibatis</groupId>
      <artifactId>ibatis-sqlmap</artifactId>
      <version>2.3.4.726</version>
    </dependency>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <version>4.12</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
"""
SERVICE = """package com.acme.broker;

public class BrokerService {
    public String create(String name) {
        return dao.insert(name);
    }
}
"""
CONTROLLER = """package com.acme.web;

import com.acme.broker.BrokerService;

public class BrokerController {
    @Autowired
    private BrokerService brokerService;

    public String create(String name) {
        return brokerService.create(name);
    }
}
"""
SQLMAP = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE sqlMap PUBLIC "-//ibatis.apache.org//DTD SQL Map 2.0//EN" "http://ibatis.apache.org/dtd/sql-map-2.dtd">
<sqlMap namespace="broker">
  <insert id="insertBroker" parameterClass="string">
    INSERT INTO BROKER (NAME) VALUES (#value#)
  </insert>
  <select id="selectBroker" resultClass="string">
    SELECT NAME FROM BROKER WHERE ID = #value#
  </select>
</sqlMap>
"""
LEGACY_AFTER = SQLMAP.replace("""  <insert id="insertBroker" parameterClass="string">
    INSERT INTO BROKER (NAME) VALUES (#value#)
  </insert>
""", "")
MODULE_SQLMAP = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE sqlMap PUBLIC "-//ibatis.apache.org//DTD SQL Map 2.0//EN" "http://ibatis.apache.org/dtd/sql-map-2.dtd">
<sqlMap namespace="brokerProfile">
  <insert id="insertBroker" parameterClass="string">
      INSERT INTO BROKER (NAME)   VALUES (#value#)
  </insert>
</sqlMap>
"""
MODULE_POM = """<project>
  <parent><groupId>com.acme</groupId><artifactId>monolith</artifactId><version>1.0</version></parent>
  <artifactId>broker-profile-domain</artifactId>
  <dependencies>
    <dependency>
      <groupId>com.ibatis</groupId>
      <artifactId>ibatis-sqlmap</artifactId>
    </dependency>
  </dependencies>
</project>
"""
TEST = """package com.acme.broker;

import org.junit.Test;

public class BrokerServiceTest {
    @Test
    public void creates() {
        assertEquals("1", new BrokerService().create("x"));
    }
}
"""
CONTEXT = """<beans>
  <bean id="brokerService" class="com.acme.broker.BrokerService"/>
</beans>
"""
MOD = "broker-domain/broker-profile/domain"
CONTROLLER_PATH = "web/src/main/java/com/acme/web/BrokerController.java"
SQLMAP_PATH = "web/src/main/resources/sqlmaps/Broker.xml"
CONTEXT_PATH = "web/src/main/resources/applicationContext.xml"
OWNERSHIP = {"broker": {"tables": ["BROKER"], "packages": ["com.acme.broker"]},
             "customer": {"tables": ["CUSTOMER"], "packages": ["com.acme.customer"]}}
DECLARATION = {"feature": "create-broker", "domain": "broker", "scope": "domain-only",
               "modules": [{"path": MOD, "kind": "domain", "artifact": "com.acme:broker-profile-domain"}],
               "outside_changes": [CONTROLLER_PATH, SQLMAP_PATH, CONTEXT_PATH]}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def move(root: Path, source: str, target: str) -> None:
    (root / target).parent.mkdir(parents=True, exist_ok=True)
    (root / source).rename(root / target)


def put(root: Path, path: str, text: str) -> None:
    f = root / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "monolith"
    root.mkdir()
    git(root, "init", "-q")
    put(root, "pom.xml", ROOT_POM)
    put(root, "web/pom.xml", WEB_POM)
    put(root, "web/src/main/java/com/acme/broker/BrokerService.java", SERVICE)
    put(root, CONTROLLER_PATH, CONTROLLER)
    put(root, SQLMAP_PATH, SQLMAP)
    put(root, CONTEXT_PATH, CONTEXT)
    put(root, "web/src/main/webapp/broker.jsp", "<html>broker</html>\n")
    put(root, "web/src/test/java/com/acme/broker/BrokerServiceTest.java", TEST)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root, git(root, "rev-parse", "HEAD")


def extract(root: Path) -> None:
    """The correct extraction: code and test moved, SQL copied, callers re-pointed, modules wired in."""
    move(root, "web/src/main/java/com/acme/broker/BrokerService.java",
         f"{MOD}/src/main/java/com/acme/broker/domain/BrokerService.java")
    f = root / MOD / "src/main/java/com/acme/broker/domain/BrokerService.java"
    f.write_text(f.read_text().replace("package com.acme.broker;", "package com.acme.broker.domain;"))
    put(root, f"{MOD}/pom.xml", MODULE_POM)
    put(root, f"{MOD}/src/main/resources/BrokerProfile.xml", MODULE_SQLMAP)
    put(root, SQLMAP_PATH, LEGACY_AFTER)
    put(root, CONTROLLER_PATH, CONTROLLER.replace("com.acme.broker.BrokerService", "com.acme.broker.domain.BrokerService"))
    put(root, CONTEXT_PATH, CONTEXT.replace("com.acme.broker.BrokerService", "com.acme.broker.domain.BrokerService"))
    put(root, "pom.xml", ROOT_POM.replace("<module>web</module>", f"<module>web</module>\n    <module>{MOD}</module>"))
    put(root, "web/pom.xml", WEB_POM.replace("  </dependencies>", """    <dependency>
      <groupId>com.acme</groupId>
      <artifactId>broker-profile-domain</artifactId>
      <version>${project.version}</version>
    </dependency>
  </dependencies>"""))
    test = "web/src/test/java/com/acme/broker/BrokerServiceTest.java"
    move(root, test, f"{MOD}/src/test/java/com/acme/broker/domain/BrokerServiceTest.java")
    t = root / MOD / "src/test/java/com/acme/broker/domain/BrokerServiceTest.java"
    t.write_text(t.read_text().replace("package com.acme.broker;", "package com.acme.broker.domain;"))


def run(root: Path, base: str, declaration=DECLARATION, design=None, gods=None) -> list[str]:
    return g.check(root, base, declaration, design, OWNERSHIP, "broker", gods)


def test_a_clean_extraction_passes(repo):
    root, base = repo
    extract(root)
    assert run(root, base) == []


def test_nothing_changed_is_fine(repo):
    root, base = repo
    assert run(root, base) == []


def test_ui_files_may_not_change(repo):
    root, base = repo
    extract(root)
    for path in ("web/src/main/webapp/broker.jsp", "web/src/main/webapp/css/site.css", "web/src/main/webapp/app.js"):
        put(root, path, "x\n")
    out = "\n".join(run(root, base))
    assert out.count("UI file changed") == 3 and "broker.jsp" in out and "site.css" in out


def test_ddl_is_refused_anywhere(repo):
    root, base = repo
    extract(root)
    f = root / MOD / "src/main/resources/BrokerProfile.xml"
    f.write_text(f.read_text().replace("</sqlMap>", '<statement id="x">ALTER TABLE BROKER ADD COLUMN age int</statement></sqlMap>'))
    put(root, "scripts/V2.sql", "create table T (id int);\n")
    out = "\n".join(run(root, base))
    assert "DDL in" in out and "New SQL script scripts/V2.sql" in out


def test_controller_may_only_be_re_pointed(repo):
    root, base = repo
    extract(root)
    c = root / CONTROLLER_PATH
    c.write_text(c.read_text().replace("brokerService.create(name)", "brokerService.create(name.trim())"))
    out = run(root, base)
    assert any("Edit outside the modules" in p and "BrokerController.java" in p and "return" in p for p in out)


def test_changed_sql_is_refused(repo):
    root, base = repo
    extract(root)
    f = root / MOD / "src/main/resources/BrokerProfile.xml"
    f.write_text(f.read_text().replace("(NAME)   VALUES", "(NAME, AGE) VALUES"))
    assert any("SQL in a module is new or differs" in p for p in run(root, base))


def test_a_statement_removed_without_being_copied_is_refused(repo):
    root, base = repo
    extract(root)
    (root / MOD / "src/main/resources/BrokerProfile.xml").write_text(
        MODULE_SQLMAP.replace("""  <insert id="insertBroker" parameterClass="string">
      INSERT INTO BROKER (NAME)   VALUES (#value#)
  </insert>
""", ""))
    assert any("removed from" in p and "without being copied" in p for p in run(root, base))


def test_a_shared_statement_may_be_copied_and_stay_in_the_legacy_map(repo):
    root, base = repo
    extract(root)
    put(root, SQLMAP_PATH, SQLMAP)  # the legacy map keeps it: another feature still uses it
    out = run(root, base)
    assert out == [] or all("outside_changes" in p for p in out)  # only the declaration no longer matches
    declaration = {**DECLARATION, "outside_changes": [CONTROLLER_PATH, CONTEXT_PATH]}
    assert run(root, base, declaration) == []


def test_new_modules_may_not_depend_on_the_monolith_or_unknown_libraries(repo):
    root, base = repo
    extract(root)
    pom = root / MOD / "pom.xml"
    pom.write_text(MODULE_POM.replace("</dependencies>", """    <dependency><groupId>com.acme</groupId><artifactId>monolith-core</artifactId></dependency>
    <dependency><groupId>org.newlib</groupId><artifactId>shiny</artifactId></dependency>
    <dependency><groupId>com.ibatis</groupId><artifactId>ibatis-sqlmap</artifactId><version>9.9</version></dependency>
  </dependencies>"""))
    out = "\n".join(run(root, base))
    assert "com.acme:monolith-core" in out and "module of the monolith itself" in out
    assert "org.newlib:shiny" in out and "not used by the monolith" in out
    assert "declares version 9.9" in out


def test_a_listed_utility_module_is_allowed(repo):
    root, base = repo
    extract(root)
    pom = root / MOD / "pom.xml"
    pom.write_text(MODULE_POM.replace("</dependencies>", """    <dependency><groupId>com.acme</groupId><artifactId>common-utils</artifactId></dependency>
  </dependencies>"""))
    assert any("common-utils" in p for p in run(root, base))
    assert run(root, base, design={"utility_modules": ["com.acme:common-utils"]}) == []


def test_build_files_cannot_change_versions_or_plugins(repo):
    root, base = repo
    extract(root)
    put(root, "web/pom.xml", (root / "web/pom.xml").read_text().replace("4.12", "4.13"))
    out = "\n".join(run(root, base))
    assert "web/pom.xml" in out and "lines removed or changed" in out
    pom = root / MOD / "pom.xml"
    pom.write_text(MODULE_POM.replace("<dependencies>", "<build><plugins><plugin><artifactId>x</artifactId></plugin></plugins></build><dependencies>"))
    pom.write_text(pom.read_text().replace("</project>", "<properties><maven.compiler.source>11</maven.compiler.source></properties></project>"))
    out = "\n".join(run(root, base))
    assert "declares a plugin" in out and "sets the Java level" in out


def test_existing_tests_cannot_be_weakened_ignored_or_deleted(repo):
    root, base = repo
    extract(root)
    t = root / MOD / "src/test/java/com/acme/broker/domain/BrokerServiceTest.java"
    t.write_text(t.read_text().replace('assertEquals("1"', 'assertEquals("2"').replace("@Test", "@Ignore\n    @Test"))
    out = "\n".join(run(root, base))
    assert "Moved test changed beyond package/imports" in out and "tests must not be ignored" in out


def test_a_deleted_existing_test_is_refused(repo):
    root, base = repo
    extract(root)
    (root / MOD / "src/test/java/com/acme/broker/domain/BrokerServiceTest.java").unlink()
    assert any("Existing test deleted" in p for p in run(root, base))


def test_domain_only_features_have_no_orchestrator_or_bridge(repo):
    root, base = repo
    extract(root)
    declaration = {**DECLARATION, "modules": [*DECLARATION["modules"],
                                              {"path": "broker-domain/broker-profile/orchestrator",
                                               "kind": "orchestrator", "artifact": "com.acme:broker-profile-orch"}],
                   "bridge_dir": "web/src/main/java/com/acme/legacybridge"}
    out = "\n".join(run(root, base, declaration))
    assert "so there is no orchestrator" in out and "bridge_dir is set" in out


def test_outside_changes_must_match_what_changed(repo):
    root, base = repo
    extract(root)
    declaration = {**DECLARATION, "outside_changes": [CONTROLLER_PATH, "web/src/main/java/Other.java"]}
    out = "\n".join(run(root, base, declaration))
    assert f"{SQLMAP_PATH} changed outside the modules but is not in outside_changes" in out
    assert "outside_changes lists web/src/main/java/Other.java, which did not change" in out


def test_god_modules_and_other_domains_are_kept_out_of_the_modules(repo):
    root, base = repo
    extract(root)
    put(root, f"{MOD}/src/main/java/com/acme/broker/domain/Helper.java",
        "package com.acme.broker.domain;\nimport com.acme.common.MasterService;\nimport com.acme.customer.CustomerDao;\n"
        "class Helper { String q = \"select * from CUSTOMER\"; }\n")
    out = "\n".join(run(root, base, gods=["com.acme.common.MasterService"]))
    assert "uses the God module com.acme.common.MasterService" in out
    assert "Boundary:" in out and "customer" in out


def test_too_many_outside_files_means_stop_and_ask(repo):
    root, base = repo
    extract(root)
    names = []
    for i in range(12):
        p = f"web/src/main/java/com/acme/web/C{i}.java"
        put(root, p, "package com.acme.web;\n")
        names.append(p)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "more")
    base2 = git(root, "rev-parse", "HEAD")
    for p in names:
        put(root, p, "package com.acme.web;\nimport com.acme.broker.domain.BrokerService;\n")
    declaration = {**DECLARATION, "outside_changes": names}
    out = run(root, base2, declaration)
    assert any("Stop and ask the user" in p for p in out)


def test_uncommitted_and_committed_steps_both_count(repo):
    root, base = repo
    extract(root)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "step 1")
    put(root, "web/src/main/webapp/new.css", "a{}\n")  # an uncommitted change after the commit
    assert any("UI file changed: web/src/main/webapp/new.css" in p for p in run(root, base))


# ---- wired into the agent -----------------------------------------------------------------------------------------

def _ctx(root: Path, base: str | None, tools=("check_module_guardrails",)):
    from app.agents.base_agent import AgentContext

    ctx = AgentContext(run_id="r", domain="broker", operation="create-broker", stage="modular_monolith", attempt=1,
                       workspace=root, feedback=None, settings={}, log=lambda _m: None,
                       config={"tools": list(tools), "outputs": [], "checks": ["module_guardrails"]})
    ctx.ownership = OWNERSHIP
    ctx.base_sha = base
    return ctx


def _declare(ctx, text: str) -> None:
    (ctx.output_dir / "module-extraction.yaml").write_text(text, encoding="utf-8")


DECLARATION_YAML = f"""feature: create-broker
domain: broker
scope: domain-only
modules:
  - {{path: {MOD}, kind: domain, artifact: "com.acme:broker-profile-domain"}}
outside_changes: [{CONTROLLER_PATH}, {SQLMAP_PATH}, {CONTEXT_PATH}]
"""


def test_the_tool_remembers_the_starting_commit_and_checks_against_it(repo):
    from app.agents.tools import Toolbox

    root, base = repo
    ctx = _ctx(root, None)
    box = Toolbox(ctx, None)
    assert ctx.base_sha == base
    assert "Write module-extraction.yaml" in box.run("check_module_guardrails", {})  # needs the declaration first
    _declare(ctx, DECLARATION_YAML)
    extract(root)
    assert box.run("check_module_guardrails", {}).startswith("Module guardrails: PASS")
    put(root, "web/src/main/webapp/broker.jsp", "<html>changed</html>\n")
    result = box.run("check_module_guardrails", {})
    assert result.startswith("Module guardrails: FAIL") and "UI file changed" in result


def test_the_agent_cannot_finish_while_a_guardrail_fails(repo):
    from app.agents import common

    root, base = repo
    ctx = _ctx(root, base)
    _declare(ctx, DECLARATION_YAML)
    extract(root)
    assert common.verify(ctx) == []
    put(root, "web/src/main/webapp/site.css", "a{}\n")
    problems = common.verify(ctx)
    assert any(p.startswith("Guardrail: UI file changed") for p in problems)


def test_without_a_git_checkout_the_check_says_so(tmp_path):
    ctx = _ctx(tmp_path, None)
    assert "starting commit is unknown" in g.for_context(ctx)[0]


def test_the_stub_declaration_is_valid_for_both_scopes(tmp_path):
    from app import contracts_store
    from app.agents import stub

    for scope, expect in (("domain-only", 1), ("cross-domain", 2)):
        ctx = _ctx(tmp_path, None)
        ctx.config["outputs"] = ["module-extraction.yaml"]
        (ctx.output_dir / "domain-dependencies.yaml").write_text(
            "decision: {scope: %s, reason: r}\ndependencies: %s\n" % (
                scope, "[{domain: customer, relation: calls, touchpoints: [{kind: service, ref: a.B#c}]}]"
                if scope == "cross-domain" else "[]"))
        [out] = [a for a in stub._sample_cross_domain_files(ctx) if a.name == "module-extraction.yaml"]
        parsed = contracts_store.parse_module_extraction((tmp_path / out.path).read_text())
        assert len(parsed["modules"]) == expect and parsed["scope"] == scope
