"""The test steps' guardrails: the acceptance suite stays black-box and in its own folder, modernisation leaves it
alone, and the service tests add tests only."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import test_guardrails as tg

SERVICE_POM = """<project>
  <groupId>com.acme</groupId><artifactId>customer-service</artifactId><version>1.0</version>
  <dependencies>
    <dependency><groupId>org.springframework</groupId><artifactId>spring-web</artifactId><version>3.2.18</version></dependency>
  </dependencies>
</project>
"""
SUITE_POM = """<project>
  <groupId>com.acme</groupId><artifactId>customer-acceptance-tests</artifactId><version>1.0</version>
  <dependencies>
    <dependency><groupId>io.rest-assured</groupId><artifactId>rest-assured</artifactId><version>5.4.0</version><scope>test</scope></dependency>
  </dependencies>
</project>
"""
SUITE_TEST = """package com.acme.acceptance;

import io.restassured.RestAssured;
import org.junit.jupiter.api.Test;

class ViewCustomerTest {
    @Test
    void s1_returns_the_customer() { RestAssured.get("/internal/api/v1/customer/1").then().statusCode(200); }
}
"""
PACKAGES = ["com.acme.customer"]


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def put(root: Path, path: str, text: str) -> None:
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text)


@pytest.fixture
def service(tmp_path):
    root = tmp_path / "customer-service"
    root.mkdir()
    git(root, "init", "-q")
    put(root, "pom.xml", SERVICE_POM)
    put(root, "src/main/java/com/acme/customer/CustomerService.java", "package com.acme.customer;\nclass CustomerService {}\n")
    put(root, "src/test/java/com/acme/customer/CustomerServiceTest.java", "package com.acme.customer;\nclass CustomerServiceTest {}\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "lifted service")
    return root, git(root, "rev-parse", "HEAD")


def suite(root: Path) -> None:
    put(root, "acceptance-tests/pom.xml", SUITE_POM)
    put(root, "acceptance-tests/src/test/java/com/acme/acceptance/ViewCustomerTest.java", SUITE_TEST)


def test_a_black_box_suite_in_its_own_folder_passes(service):
    root, base = service
    suite(root)
    assert tg.acceptance(root, base, None, None, PACKAGES) == []


@pytest.mark.parametrize("change, expect", [
    (lambda r: put(r, "src/main/java/com/acme/customer/CustomerService.java", "package com.acme.customer;\nclass X {}\n"),
     "is outside acceptance-tests/"),
    (lambda r: put(r, "acceptance-tests/src/test/java/com/acme/acceptance/ViewCustomerTest.java",
                   SUITE_TEST.replace("import org.junit", "import com.acme.customer.CustomerService;\nimport org.junit")),
     "imports com.acme.customer, the service's code"),
    (lambda r: put(r, "acceptance-tests/pom.xml", SUITE_POM.replace("<dependencies>", "<parent><groupId>com.acme</groupId>"
                   "<artifactId>customer-service</artifactId><version>1.0</version><relativePath>../pom.xml</relativePath>"
                   "</parent>\n  <dependencies>")),
     "inherits from the service's POM"),
    (lambda r: put(r, "acceptance-tests/pom.xml", SUITE_POM.replace("</dependencies>", "  <dependency><groupId>com.acme"
                   "</groupId><artifactId>customer-service</artifactId><version>1.0</version></dependency>\n  </dependencies>")),
     "depends on the service (customer-service)"),
    (lambda r: put(r, "acceptance-tests/src/test/java/com/acme/acceptance/ViewCustomerTest.java",
                   SUITE_TEST.replace("    @Test", "    @Disabled\n    @Test")),
     "must not be disabled"),
])
def test_the_acceptance_suite_only_adds_black_box_tests(service, change, expect):
    root, base = service
    suite(root)
    change(root)
    assert any(expect in p for p in tg.acceptance(root, base, None, None, PACKAGES))


def test_the_acceptance_suite_needs_its_own_build_and_leaves_the_monolith_alone(service, tmp_path):
    root, base = service
    put(root, "acceptance-tests/src/test/java/A.java", "class A {}\n")
    assert any("its own pom.xml" in p for p in tg.acceptance(root, base, None, None, PACKAGES))
    monolith = tmp_path / "monolith"
    monolith.mkdir()
    git(monolith, "init", "-q")
    put(monolith, "App.java", "class App {}\n")
    git(monolith, "add", "-A")
    git(monolith, "commit", "-q", "-m", "m")
    sha = git(monolith, "rev-parse", "HEAD")
    put(monolith, "App.java", "class App { int x; }\n")
    assert any("The monolith repository changed: App.java" in p for p in tg.acceptance(root, base, monolith, sha, PACKAGES))


def test_modernisation_leaves_the_acceptance_suite_unchanged(service):
    root, base = service
    suite(root)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "suite")
    base = git(root, "rev-parse", "HEAD")
    put(root, "src/main/java/com/acme/customer/CustomerService.java", "package com.acme.customer;\nrecord CustomerService() {}\n")
    assert tg.suite_unchanged(root, base) == []  # the service may change freely
    put(root, "acceptance-tests/src/test/java/com/acme/acceptance/ViewCustomerTest.java", SUITE_TEST.replace("200", "201"))
    out = tg.suite_unchanged(root, base)
    assert len(out) == 1 and "ViewCustomerTest.java changed" in out[0]


def test_service_tests_add_tests_and_test_scoped_dependencies_only(service):
    root, base = service
    put(root, "src/test/java/com/acme/customer/CustomerControllerTest.java", "package com.acme.customer;\nclass T {}\n")
    put(root, "src/test/resources/application-test.yml", "spring:\n  jpa:\n    hibernate:\n      ddl-auto: validate\n")
    put(root, "pom.xml", SERVICE_POM.replace("</dependencies>", "  <dependency><groupId>org.testcontainers</groupId>"
                                             "<artifactId>oracle-xe</artifactId><version>1.20.1</version><scope>test</scope>"
                                             "</dependency>\n  </dependencies>"))
    assert tg.service_tests(root, base, None, None) == []

    put(root, "pom.xml", SERVICE_POM.replace("</dependencies>", "  <dependency><groupId>com.google.guava</groupId>"
                                             "<artifactId>guava</artifactId><version>33.0</version></dependency>\n  </dependencies>"))
    assert any("guava is not <scope>test</scope>" in p for p in tg.service_tests(root, base, None, None))
    put(root, "pom.xml", SERVICE_POM.replace("3.2.18", "6.1.0"))
    assert any("spring-web was removed or changed" in p for p in tg.service_tests(root, base, None, None))
    put(root, "pom.xml", SERVICE_POM.replace("<version>1.0</version>", "<version>1.1</version>", 1))
    assert any("more than dependencies changed" in p for p in tg.service_tests(root, base, None, None))
    put(root, "pom.xml", SERVICE_POM)
    put(root, "src/main/java/com/acme/customer/CustomerService.java", "package com.acme.customer;\nclass Fixed {}\n")
    put(root, "acceptance-tests/pom.xml", SUITE_POM)
    out = "\n".join(tg.service_tests(root, base, None, None))
    assert "CustomerService.java is production code" in out and "part of the acceptance suite" in out


def test_the_checks_run_in_the_service_repository_only(service):
    root, base = service
    ctx = SimpleNamespace(config={"checks": ["acceptance_test_guardrails"]}, code_dir=".", base_sha=base,
                          workspace=root, ownership={}, domain="customer", monolith_sha=None)
    assert "never in the monolith" in tg.for_context(ctx)[0]
    assert tg.for_context(SimpleNamespace(config={"checks": ["boundary"]})) == []  # no test check configured


def test_the_standard_workflow_gives_the_test_steps_their_repository(client):
    from app import workflow_store
    from app.db.models import SHARED, Setting, WorkflowDef
    from app.db.session import session_scope
    from tests.test_modulith_design import _steps_of_shape

    original = client.get("/api/workflows/standard").json()["steps"]
    service = {"repository": "https://git.example.com/acme/{domain}-service.git", "base_branch": "main"}
    previous = [{**st, "code": service} if st["key"] in ("microservice_scaffold", "modernise") else st
                for st in _steps_of_shape(workflow_store._STANDARD_V6)]
    with session_scope() as s:
        s.get(WorkflowDef, (SHARED, "standard")).steps = previous
        s.query(Setting).filter(Setting.key == "_standard_workflow_v7").delete()
    workflow_store.seed()
    steps = {st["key"]: st for st in client.get("/api/workflows/standard").json()["steps"]}
    keys = list(steps)
    assert keys[keys.index("modernise") + 1] == "service_tests"
    assert steps["independent_tests"]["code"] == service and steps["service_tests"]["code"] == service
    assert "service_tests" in steps["gate_release"]["reviews"]
    with session_scope() as s:
        s.get(WorkflowDef, (SHARED, "standard")).steps = original
