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

public interface BrokerService {
    String create(String name);
}
"""
IMPL = """package com.acme.broker;

public class BrokerServiceImpl implements BrokerService {
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
        assertEquals("1", new BrokerServiceImpl().create("x"));
    }
}
"""
CONTEXT = """<beans>
  <bean id="brokerService" class="com.acme.broker.BrokerServiceImpl"/>
</beans>
"""
# The router takes over the legacy bean's id; the legacy bean gets a new one; the module's copy is a bean of its own.
CONTEXT_AFTER = """<beans>
  <bean id="brokerServiceLegacy" class="com.acme.broker.BrokerServiceImpl"/>
  <bean id="brokerDomainService" class="com.acme.broker.domain.BrokerServiceImpl"/>
  <bean id="brokerService" class="com.acme.broker.legacybridge.BrokerServiceRouter" primary="true">
    <property name="legacy" ref="brokerServiceLegacy"/>
    <property name="domain" ref="brokerDomainService"/>
  </bean>
</beans>
"""
MOD = "broker-domain/broker-profile/domain"
BRIDGE = "web/src/main/java/com/acme/broker/legacybridge"
ROUTER = f"{BRIDGE}/BrokerServiceRouter.java"
PROPERTY = "migration.broker.create-broker.route"
ROUTER_JAVA = """package com.acme.broker.legacybridge;

import com.acme.broker.BrokerService;

public class BrokerServiceRouter implements BrokerService {
    @Value("${migration.broker.create-broker.route:legacy}")
    private String route;
    private BrokerService legacy;
    private com.acme.broker.domain.BrokerService domain;

    public String create(String name) {
        return "monolith".equals(route) ? domain.create(name) : legacy.create(name);
    }
}
"""
CONTROLLER_PATH = "web/src/main/java/com/acme/web/BrokerController.java"
SERVICE_PATH = "web/src/main/java/com/acme/broker/BrokerService.java"
IMPL_PATH = "web/src/main/java/com/acme/broker/BrokerServiceImpl.java"
TEST_PATH = "web/src/test/java/com/acme/broker/BrokerServiceTest.java"
SQLMAP_PATH = "web/src/main/resources/sqlmaps/Broker.xml"
CONTEXT_PATH = "web/src/main/resources/applicationContext.xml"
PROPERTIES_PATH = "web/src/main/resources/application.properties"
OWNERSHIP = {"broker": {"tables": ["BROKER"], "packages": ["com.acme.broker"]},
             "customer": {"tables": ["CUSTOMER"], "packages": ["com.acme.customer"]}}
ROUTING = {"property": PROPERTY, "default": "legacy", "router": ROUTER,
           "routes": [{"bean": "brokerService", "legacy_bean": "brokerServiceLegacy",
                       "interface": "com.acme.broker.BrokerService", "module": "com.acme.broker.domain.BrokerService"}]}
DECLARATION = {"feature": "create-broker", "domain": "broker", "scope": "domain-only", "bridge_dir": BRIDGE,
               "modules": [{"path": MOD, "kind": "domain", "artifact": "com.acme:broker-profile-domain"}],
               "routing": ROUTING, "outside_changes": [CONTEXT_PATH, PROPERTIES_PATH, "pom.xml", "web/pom.xml"]}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def put(root: Path, path: str, text: str) -> None:
    f = root / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


def copy(root: Path, source: str, target: str) -> None:
    put(root, target, (root / source).read_text().replace("package com.acme.broker;", "package com.acme.broker.domain;"))


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "monolith"
    root.mkdir()
    git(root, "init", "-q")
    put(root, "pom.xml", ROOT_POM)
    put(root, "web/pom.xml", WEB_POM)
    put(root, SERVICE_PATH, SERVICE)
    put(root, IMPL_PATH, IMPL)
    put(root, CONTROLLER_PATH, CONTROLLER)
    put(root, SQLMAP_PATH, SQLMAP)
    put(root, CONTEXT_PATH, CONTEXT)
    put(root, PROPERTIES_PATH, "app.name=web\n")
    put(root, "web/src/main/webapp/broker.jsp", "<html>broker</html>\n")
    put(root, TEST_PATH, TEST)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root, git(root, "rev-parse", "HEAD")


def extract(root: Path) -> None:
    """The correct extraction, a parallel run: the legacy code (service, controller, SQL map, test) unchanged; the
    module a copy of it with its own SQL map; the router in the bridge behind the legacy bean's id; the module wired in."""
    pkg = f"{MOD}/src/main/java/com/acme/broker/domain"
    copy(root, SERVICE_PATH, f"{pkg}/BrokerService.java")
    copy(root, IMPL_PATH, f"{pkg}/BrokerServiceImpl.java")
    copy(root, TEST_PATH, f"{MOD}/src/test/java/com/acme/broker/domain/BrokerServiceTest.java")
    put(root, f"{MOD}/pom.xml", MODULE_POM)
    put(root, f"{MOD}/src/main/resources/BrokerProfile.xml", MODULE_SQLMAP)
    put(root, ROUTER, ROUTER_JAVA)
    put(root, CONTEXT_PATH, CONTEXT_AFTER)
    put(root, PROPERTIES_PATH, f"app.name=web\n{PROPERTY}=legacy\n")
    put(root, "pom.xml", ROOT_POM.replace("<module>web</module>", f"<module>web</module>\n    <module>{MOD}</module>"))
    put(root, "web/pom.xml", WEB_POM.replace("  </dependencies>", """    <dependency>
      <groupId>com.acme</groupId>
      <artifactId>broker-profile-domain</artifactId>
      <version>${project.version}</version>
    </dependency>
  </dependencies>"""))


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


def test_the_legacy_code_stays_as_it_is(repo):
    root, base = repo
    extract(root)
    put(root, CONTROLLER_PATH, CONTROLLER.replace("com.acme.broker.BrokerService", "com.acme.broker.domain.BrokerService"))
    put(root, IMPL_PATH, IMPL.replace("dao.insert(name)", "dao.insert(name.trim())"))
    out = "\n".join(run(root, base, {**DECLARATION, "outside_changes": [*DECLARATION["outside_changes"],
                                                                        CONTROLLER_PATH, IMPL_PATH]}))
    assert "Legacy file changed: " + CONTROLLER_PATH in out and "Legacy file changed: " + IMPL_PATH in out


def test_legacy_files_are_never_deleted_moved_or_joined_by_new_ones(repo):
    root, base = repo
    extract(root)
    (root / IMPL_PATH).unlink()
    (root / SERVICE_PATH).rename(root / "web/src/main/java/com/acme/broker/BrokerApi.java")
    put(root, "web/src/main/java/com/acme/web/Helper.java", "package com.acme.web;\nclass Helper {}\n")
    out = "\n".join(run(root, base))
    assert f"Legacy file moved: {IMPL_PATH} → {MOD}/" in out  # git pairs it with its copy in the module
    assert f"Legacy file moved: {SERVICE_PATH} → web/src/main/java/com/acme/broker/BrokerApi.java" in out
    assert "New file outside the module and the bridge: web/src/main/java/com/acme/web/Helper.java" in out


def test_spring_wiring_may_change_only_for_this_steps_beans(repo):
    root, base = repo
    extract(root)
    put(root, CONTEXT_PATH, CONTEXT_AFTER.replace("</beans>", '  <bean id="auditService" class="com.acme.Audit"/>\n</beans>'))
    assert any("Legacy file changed: " + CONTEXT_PATH in p and "auditService" in p for p in run(root, base))


def test_changed_sql_is_refused(repo):
    root, base = repo
    extract(root)
    f = root / MOD / "src/main/resources/BrokerProfile.xml"
    f.write_text(f.read_text().replace("(NAME)   VALUES", "(NAME, AGE) VALUES"))
    assert any("SQL in a module is new or differs" in p for p in run(root, base))


def test_the_legacy_sql_maps_stay_as_they_are(repo):
    root, base = repo
    extract(root)
    put(root, SQLMAP_PATH, SQLMAP.replace("""  <select id="selectBroker" resultClass="string">
    SELECT NAME FROM BROKER WHERE ID = #value#
  </select>
""", ""))
    declaration = {**DECLARATION, "outside_changes": [*DECLARATION["outside_changes"], SQLMAP_PATH]}
    assert any("Legacy SQL map changed: " + SQLMAP_PATH in p for p in run(root, base, declaration))


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
    put(root, TEST_PATH, TEST.replace('assertEquals("1"', 'assertEquals("2"'))
    t = root / MOD / "src/test/java/com/acme/broker/domain/BrokerServiceTest.java"
    t.write_text(t.read_text().replace("@Test", "@Ignore\n    @Test"))
    out = "\n".join(run(root, base, {**DECLARATION, "outside_changes": [*DECLARATION["outside_changes"], TEST_PATH]}))
    assert "Legacy test changed: " + TEST_PATH in out and "tests must not be ignored" in out


def test_a_deleted_existing_test_is_refused(repo):
    root, base = repo
    extract(root)
    (root / TEST_PATH).unlink()
    assert any("Legacy test moved: " + TEST_PATH in p for p in run(root, base))  # beside its copy in the module
    (root / MOD / "src/test/java/com/acme/broker/domain/BrokerServiceTest.java").unlink()
    assert any("Legacy test deleted: " + TEST_PATH in p for p in run(root, base))


def test_domain_only_features_have_no_orchestrator_or_bridge(repo):
    root, base = repo
    extract(root)
    declaration = {**DECLARATION, "modules": [*DECLARATION["modules"],
                                              {"path": "broker-domain/broker-profile/orchestrator",
                                               "kind": "orchestrator", "artifact": "com.acme:broker-profile-orch"}],
                   "bridge_dir": "web/src/main/java/com/acme/legacybridge"}
    out = "\n".join(run(root, base, declaration))
    assert "so there is no orchestrator" in out and "bridge_dir is set" not in out


def test_outside_changes_must_match_what_changed(repo):
    root, base = repo
    extract(root)
    declaration = {**DECLARATION, "outside_changes": [PROPERTIES_PATH, "pom.xml", "web/pom.xml",
                                                      "web/src/main/java/Other.java"]}
    out = "\n".join(run(root, base, declaration))
    assert f"{CONTEXT_PATH} changed outside the modules but is not in outside_changes" in out
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
bridge_dir: {BRIDGE}
modules:
  - {{path: {MOD}, kind: domain, artifact: "com.acme:broker-profile-domain"}}
routing:
  property: {PROPERTY}
  default: legacy
  router: {ROUTER}
  routes:
    - {{bean: brokerService, legacy_bean: brokerServiceLegacy, interface: com.acme.broker.BrokerService,
       module: com.acme.broker.domain.BrokerService}}
outside_changes: [{CONTEXT_PATH}, {PROPERTIES_PATH}, pom.xml, web/pom.xml]
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

    for scope, orchestrator in (("domain-only", "none"), ("cross-domain", "module")):
        ctx = _ctx(tmp_path, None)
        ctx.config["outputs"] = ["module-extraction.yaml", "orchestrator-extraction.yaml"]
        (ctx.output_dir / "domain-dependencies.yaml").write_text(
            "decision: {scope: %s, reason: r}\ndependencies: %s\n" % (
                scope, "[{domain: customer, relation: calls, touchpoints: [{kind: service, ref: a.B#c}]}]"
                if scope == "cross-domain" else "[]"))
        written = {a.name: (tmp_path / a.path).read_text() for a in stub._sample_cross_domain_files(ctx)}
        parsed = contracts_store.parse_module_extraction(written["module-extraction.yaml"])
        assert [m["kind"] for m in parsed["modules"]] == ["domain"] and parsed["scope"] == scope  # no orchestrator here
        parsed = contracts_store.parse_orchestrator_extraction(written["orchestrator-extraction.yaml"])
        assert parsed["orchestrator"] == orchestrator


def test_modules_may_not_read_the_session_or_user_directly(repo):
    root, base = repo
    extract(root)
    put(root, f"{MOD}/src/main/java/com/acme/broker/domain/Audit.java",
        "package com.acme.broker.domain;\nimport javax.servlet.http.HttpSession;\n"
        "class Audit { HttpSession s; ThreadLocal<String> user; }\n")
    out = "\n".join(run(root, base))
    assert "Audit.java uses" in out and "context port" in out


def test_the_bridge_package_holds_the_router_and_adapters_only(repo):
    root, base = repo
    extract(root)
    put(root, f"{MOD}/src/main/java/com/acme/broker/domain/CurrentUser.java",
        "package com.acme.broker.domain;\npublic interface CurrentUser { String name(); }\n")
    put(root, f"{BRIDGE}/SessionCurrentUser.java",
        "package com.acme.broker.legacybridge;\nimport javax.servlet.http.HttpSession;\n"
        "class SessionCurrentUser implements com.acme.broker.domain.CurrentUser { HttpSession s; }\n")
    assert run(root, base) == []
    put(root, f"{BRIDGE}/BrokerRules.java", "package com.acme.broker.legacybridge;\nclass BrokerRules { }\n")
    assert any("BrokerRules.java in the bridge package is not the router" in p for p in run(root, base))


MAPPER = """package com.acme.broker.legacybridge;

/** Converts between the legacy types and the module's. */
final class BrokerMapper {
    static String toModule(String legacy) { return legacy; }
}
"""


def test_the_bridge_may_hold_mappers_the_router_uses(repo):
    root, base = repo
    extract(root)
    put(root, f"{BRIDGE}/BrokerMapper.java", MAPPER)
    put(root, ROUTER, ROUTER_JAVA.replace("domain.create(name)", "domain.create(BrokerMapper.toModule(name))"))
    assert run(root, base) == []


def test_a_mapper_nothing_uses_or_with_database_access_is_refused(repo):
    root, base = repo
    extract(root)
    put(root, f"{BRIDGE}/BrokerMapper.java", MAPPER)
    assert any("BrokerMapper.java in the bridge package is not the router" in p for p in run(root, base))
    put(root, ROUTER, ROUTER_JAVA.replace("domain.create(name)", "domain.create(BrokerMapper.toModule(name))"))
    put(root, f"{BRIDGE}/BrokerMapper.java", MAPPER.replace("final class", "import com.acme.broker.BrokerDao;\nfinal class"))
    assert any("BrokerMapper.java uses BrokerDao" in p for p in run(root, base))


# The toggle branch: the caller asks the router; the as-is code may move or be rewritten while a test pins behaviour.
BRANCHED = CONTROLLER.replace("""    public String create(String name) {
        return brokerService.create(name);
    }
""", """    @Autowired
    private com.acme.broker.legacybridge.BrokerServiceRouter brokerServiceRouter;

    public String create(String name) {
        return brokerServiceRouter.routed() ? brokerServiceRouter.create(name) : createLegacy(name);
    }

    private String createLegacy(String name) {
        String created = brokerService.create(name);
        return created;
    }
""")
BRANCH = {"file": CONTROLLER_PATH, "reason": "calls BrokerServiceImpl directly"}
CONTROLLER_TEST_PATH = "web/src/test/java/com/acme/web/BrokerControllerTest.java"
CONTROLLER_TEST = """package com.acme.web;

public class BrokerControllerTest {
    @Test public void createsAtLegacy() { assertEquals("1", new BrokerController().create("x")); }
    @Test public void createsAtMonolith() { assertEquals("1", routed(new BrokerController()).create("x")); }
}
"""


def test_a_listed_caller_may_be_refactored_for_its_toggle_branch_when_a_test_pins_it(repo):
    root, base = repo
    extract(root)
    put(root, CONTROLLER_PATH, BRANCHED)  # the as-is line moved into a method of its own and rewritten
    assert any("Legacy file changed: " + CONTROLLER_PATH in p for p in run(root, base))  # not listed
    declaration = {**DECLARATION, "toggle_branches": [BRANCH]}
    assert any("no test in this change covers BrokerController" in p for p in run(root, base, declaration))
    put(root, CONTROLLER_TEST_PATH, CONTROLLER_TEST)  # a new test beside the legacy code needs no listing
    assert run(root, base, declaration) == []


@pytest.mark.parametrize("controller, problem", [
    (BRANCHED.replace("brokerServiceRouter", "brokerDomain").replace("BrokerServiceRouter", "BrokerFacade"),
     "does not ask the router BrokerServiceRouter"),
    (BRANCHED.replace("        String created", "        BrokerDao.check(name);\n        String created"),
     "the change adds BrokerDao"),
])
def test_a_toggle_branch_asks_the_router_and_adds_no_database_access(repo, controller, problem):
    root, base = repo
    extract(root)
    put(root, CONTROLLER_PATH, controller)
    put(root, CONTROLLER_TEST_PATH, CONTROLLER_TEST)
    assert any(problem in p for p in run(root, base, {**DECLARATION, "toggle_branches": [BRANCH]}))


def test_tests_may_be_added_or_extended_but_not_weakened(repo):
    root, base = repo
    extract(root)
    put(root, TEST_PATH, TEST.replace("    @Test\n", "    @Test\n    public void more() { }\n    @Test\n"))
    assert run(root, base) == []
    put(root, TEST_PATH, TEST.replace('assertEquals("1", ', "assertNotNull("))
    assert any(f"Legacy test changed: {TEST_PATH}" in p for p in run(root, base))


def test_toggle_branches_are_for_changed_legacy_callers_not_daos(repo):
    root, base = repo
    dao = "web/src/main/java/com/acme/broker/BrokerDaoImpl.java"
    put(root, dao, "package com.acme.broker;\npublic class BrokerDaoImpl extends SqlMapClientDaoSupport { }\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "dao")
    base = git(root, "rev-parse", "HEAD")
    extract(root)
    put(root, dao, "package com.acme.broker;\npublic class BrokerDaoImpl extends SqlMapClientDaoSupport { int x; }\n")
    put(root, IMPL_PATH, IMPL.replace("    public String create", "    private BrokerServiceRouter router;\n    public String create"))
    put(root, "web/src/test/java/com/acme/broker/BrokerServiceImplTest.java", "class T { BrokerServiceImpl s; }\n")
    branches = [BRANCH, {"file": dao, "reason": "r"}, {"file": IMPL_PATH, "reason": "news up its collaborator"}]
    out = "\n".join(run(root, base, {**DECLARATION, "toggle_branches": branches}))
    assert f"toggle_branches lists {CONTROLLER_PATH}, which did not change" in out
    assert f"toggle_branches lists {dao}, a DAO" in out
    assert IMPL_PATH not in out  # a service with no bean seam may get a branch too


def test_toggle_branches_in_the_declaration():
    from app import contracts_store as cs

    text = DECLARATION_YAML.replace("outside_changes:", f"toggle_branches: [{{file: {CONTROLLER_PATH}, reason: r}}]\n"
                                    "outside_changes:")
    assert cs.parse_module_extraction(text)["toggle_branches"] == [{"file": CONTROLLER_PATH, "reason": "r"}]
    older = text.replace("{file: ", "{controller: ")  # the older key still reads
    assert cs.parse_module_extraction(older)["toggle_branches"] == [{"file": CONTROLLER_PATH, "reason": "r"}]
    with pytest.raises(cs.InvalidDocument, match="must be a Java file"):
        cs.parse_module_extraction(text.replace(f"file: {CONTROLLER_PATH}", "file: web/broker.jsp"))
    with pytest.raises(cs.InvalidDocument, match="reason"):
        cs.parse_module_extraction(text.replace("reason: r}", "reason: ''}"))


def test_the_module_and_bridge_folders_are_the_steps_own(repo):
    root, base = repo
    extract(root)
    existing = "web/src/main/java/com/acme/broker"
    modules = [{"path": existing, "kind": "domain", "artifact": "com.acme:broker-profile-domain"}]
    out = "\n".join(run(root, base, {**DECLARATION, "modules": modules}))
    assert f"The module folder {existing} already existed" in out
    out = "\n".join(run(root, base, {**DECLARATION, "bridge_dir": "web/src/main/java/com/acme"}))
    assert "The bridge package web/src/main/java/com/acme already existed" in out
    earlier = {"modules": {existing}, "bridges": set()}  # this domain's module, from an earlier feature
    assert "already existed" not in "\n".join(g.check(root, base, {**DECLARATION, "modules": modules}, None, OWNERSHIP,
                                                       "broker", None, earlier=earlier))


def test_the_toggle_defaults_to_the_legacy_code_and_the_router_exists(repo):
    root, base = repo
    extract(root)
    put(root, PROPERTIES_PATH, f"app.name=web\n{PROPERTY}=monolith\n")
    assert any(f"sets {PROPERTY} to monolith. It defaults to legacy" in p for p in run(root, base))
    put(root, PROPERTIES_PATH, f"app.name=web\n{PROPERTY}=legacy\n")
    (root / ROUTER).unlink()
    out = "\n".join(run(root, base))
    assert f"The router {ROUTER}" in out and "doesn't exist" in out


LEGACY_MAP = """<sqlMap namespace="loan">
  <typeAlias alias="loan" type="com.acme.loan.Loan"/>
  <resultMap id="loanResult" class="loan">
    <result property="id" column="LOAN_ID"/>
    <result property="customer" column="CUSTOMER_ID" select="loan.selectCustomer"/>
  </resultMap>
  <sql id="loanColumns">LOAN_ID, CUSTOMER_ID, AMOUNT</sql>
  <select id="selectLoan" parameterClass="long" resultMap="loanResult">
    SELECT <include refid="loanColumns"/> FROM LOAN WHERE LOAN_ID = #value#
    <dynamic><isNotNull property="status"> AND STATUS = #status#</isNotNull></dynamic>
  </select>
  <insert id="insertLoan" parameterClass="com.acme.loan.Loan">INSERT INTO LOAN (LOAN_ID) VALUES (#id#)</insert>
</sqlMap>"""
# The copy in the module, beside the original in the same iBatis configuration: every name made unique, the classes
# in the module's package, the SQL as it is.
COPIED_MAP = """<sqlMap namespace="loanDomain">
  <typeAlias alias="domainLoan" type="com.acme.loan.domain.Loan"/>
  <resultMap id="domainLoanResult" class="domainLoan">
    <result property="id" column="LOAN_ID"/>
    <result property="customer" column="CUSTOMER_ID" select="loanDomain.selectCustomer"/>
  </resultMap>
  <sql id="domainLoanColumns">LOAN_ID, CUSTOMER_ID, AMOUNT</sql>
  <select id="domainSelectLoan" parameterClass="long" resultMap="domainLoanResult">
    SELECT <include refid="domainLoanColumns"/> FROM LOAN WHERE LOAN_ID = #value#
    <dynamic><isNotNull property="status"> AND STATUS = #status#</isNotNull></dynamic>
  </select>
  <insert id="domainInsertLoan" parameterClass="com.acme.loan.domain.Loan">INSERT INTO LOAN (LOAN_ID) VALUES (#id#)</insert>
</sqlMap>"""


def test_a_copied_sql_map_may_rename_what_must_be_unique_but_not_change_its_sql():
    from app.module_guardrails import statements

    assert sorted(statements(COPIED_MAP)) == sorted(statements(LEGACY_MAP))  # names only: the same statements
    for changed in (COPIED_MAP.replace("AMOUNT", "AMOUNT, RATE"),  # the SQL
                    COPIED_MAP.replace('column="LOAN_ID"', 'column="ID"'),  # a mapping
                    COPIED_MAP.replace(" AND STATUS = #status#", ""),  # a dynamic condition
                    COPIED_MAP.replace('parameterClass="long"', 'parameterClass="string"'),  # a built-in type
                    COPIED_MAP.replace("domain.Loan\">INSERT", "domain.LoanDto\">INSERT")):  # another class
        assert sorted(statements(changed)) != sorted(statements(LEGACY_MAP)), changed
    # The legacy maps themselves stay exactly as they are: renaming one of their ids is a change.
    assert statements(LEGACY_MAP.replace('id="selectLoan"', 'id="legacySelectLoan"'), strict=True) != \
        statements(LEGACY_MAP, strict=True)


def test_the_module_check_accepts_a_copy_with_unique_names(repo):
    root, base = repo
    extract(root)
    f = root / MOD / "src/main/resources/BrokerProfile.xml"
    f.write_text(f.read_text().replace('id="insertBroker"', 'id="profileInsertBroker"'))  # unique beside the original
    assert not [p for p in run(root, base) if "SQL in a module" in p]


def test_renaming_an_id_in_a_legacy_sql_map_is_refused(repo):
    root, base = repo
    extract(root)
    put(root, SQLMAP_PATH, SQLMAP.replace('id="selectBroker"', 'id="legacySelectBroker"'))
    declaration = {**DECLARATION, "outside_changes": [*DECLARATION["outside_changes"], SQLMAP_PATH]}
    assert any("Legacy SQL map changed: " + SQLMAP_PATH in p for p in run(root, base, declaration))
