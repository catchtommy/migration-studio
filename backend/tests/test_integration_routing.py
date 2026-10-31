"""Monolith integration's toggle: the feature's toggle (from Module in monolith) gains the service value, which calls
the orchestrator service when the feature has an orchestrator and the domain service when it has none; the as-is
code (legacy) stays the default."""

import subprocess
from pathlib import Path

import pytest

from app import contracts_store as cs
from app import release_guardrails as rg

ORCH_API = """openapi: 3.1.0
info: {title: o, version: "1"}
paths:
  /internal/api/v1/loan/orchestrator/loans:
    post: {operationId: createLoan, responses: {"201": {description: Created}}}
"""
DOMAIN_API = ORCH_API.replace("/orchestrator", "")
ROUTER = "web/src/main/java/com/acme/loan/legacybridge/LoanServiceRouter.java"
PROPERTY = "migration.loan.create-loan.route"


def routing(target="orchestrator-service", api="orchestrator-openapi.yaml", default="legacy", ops=("createLoan",),
            prop=PROPERTY):
    return "\n".join([
        "feature: create-loan", "domain: loan", f"target: {target}", f"api: {api}",
        "toggle:", f"  property: {prop}", f"  default: {default}", f"router: {ROUTER}", "operations:",
        *(f"  - {{operation_id: {o}, monolith: 'LoanOrchestrator#create', client: 'LoanClient#create'}}" for o in ops),
    ]) + "\n"


def documents(tmp_path, orchestrator: bool, service: bool = True) -> Path:
    d = tmp_path / "docs"
    d.mkdir()
    (d / "openapi.yaml").write_text(DOMAIN_API)
    (d / cs.MODULE_EXTRACTION_FILE).write_text(  # the domain module the orchestrator is built on
        "feature: create-loan\ndomain: loan\nscope: cross-domain\n"
        "bridge_dir: web/src/main/java/com/acme/loan/legacybridge\n"
        "modules: [{path: loan/domain, kind: domain, artifact: 'a:d'}]\n"
        f"routing: {{property: {PROPERTY}, default: legacy, router: {ROUTER}, routes: [{{bean: loanService, "
        "legacy_bean: loanServiceLegacy, interface: com.acme.loan.LoanService, "
        "module: com.acme.loan.domain.LoanService}]}\n")
    if orchestrator:
        (d / cs.ORCHESTRATOR_EXTRACTION_FILE).write_text(
            "feature: create-loan\ndomain: loan\norchestrator: module\nmodule: {path: loan/orchestrator, artifact: 'a:o'}\n"
            "operations: [{method: 'LoanOrchestrator#create', replaces: 'LoanService#create'}]\n")
        if service:
            (d / "orchestrator-openapi.yaml").write_text(ORCH_API)
            (d / cs.ORCHESTRATOR_SERVICE_FILE).write_text(
                "feature: create-loan\ndomain: loan\norchestrator: service\nmodule_commit: abc\n"
                "openapi: orchestrator-openapi.yaml\noperations: [{method: 'LoanOrchestrator#create', operation_id: createLoan}]\n")
    else:
        (d / cs.ORCHESTRATOR_EXTRACTION_FILE).write_text(
            "feature: create-loan\ndomain: loan\norchestrator: none\nreason: domain-only\n")
    return d


def problems(d: Path, text: str) -> str:
    (d / cs.INTEGRATION_ROUTING_FILE).write_text(text)
    return "\n".join(cs.validate_outputs(d))


def test_with_an_orchestrator_the_monolith_calls_the_orchestrator_service(tmp_path):
    d = documents(tmp_path, orchestrator=True)
    assert problems(d, routing()) == ""
    out = problems(d, routing(target="domain-service", api="openapi.yaml"))
    assert "must call the orchestrator-service" in out and "only through it" in out


def test_without_an_orchestrator_the_monolith_calls_the_domain_service(tmp_path):
    d = documents(tmp_path, orchestrator=False)
    assert problems(d, routing(target="domain-service", api="openapi.yaml")) == ""
    assert "must call the domain-service (the feature has no orchestrator)" in problems(d, routing())


def test_an_orchestrator_without_its_service_stops_the_step(tmp_path):
    d = documents(tmp_path, orchestrator=True, service=False)
    out = problems(d, routing(target="domain-service", api="openapi.yaml"))
    assert "would drop the orchestration" in out and "Orchestrator microservice step comes first" in out


def test_every_operation_of_the_target_is_routed_and_the_old_flow_is_the_default(tmp_path):
    d = documents(tmp_path, orchestrator=True)
    out = problems(d, routing(ops=("createLoan", "deleteLoan")))
    assert "operation deleteLoan is not in orchestrator-openapi.yaml" in out
    assert "operation createLoan of orchestrator-openapi.yaml is not routed" in problems(d, routing(ops=("other",)))
    with pytest.raises(cs.InvalidDocument, match="defaults to legacy"):
        cs.parse_integration_routing(routing(default="monolith"))


def test_the_service_value_goes_on_the_features_own_toggle(tmp_path):
    d = documents(tmp_path, orchestrator=True)
    assert "use the feature's toggle" in problems(d, routing(prop="migration.loan.other.route"))


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _monolith(tmp_path):
    root = tmp_path / "monolith"
    (root / "web/src/main/resources").mkdir(parents=True)
    _git(root.parent, "init", "-q", str(root))
    (root / "web/src/main/resources/application.properties").write_text("app.name=web\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _switch(root, url, prop_value="legacy"):
    (root / ROUTER).parent.mkdir(parents=True, exist_ok=True)
    (root / ROUTER).write_text('class LoanServiceRouter { @Value("${migration.loan.create-loan.route:legacy}") String route; }\n')
    (root / "web/src/main/java/com/acme/loan/legacybridge/LoanClient.java").write_text(f'class LoanClient {{ String path = "{url}"; }}\n')
    (root / "web/src/main/resources/application.properties").write_text(
        f"app.name=web\nmigration.loan.create-loan.route={prop_value}\n")


def test_the_code_reads_the_toggle_and_calls_only_the_target(tmp_path):
    root, base = _monolith(tmp_path)
    orch = cs.parse_integration_routing(routing())
    _switch(root, "/internal/api/v1/loan/orchestrator/loans")
    assert rg.integration(root, base, [], orch, "loan") == []

    _switch(root, "/internal/api/v1/loan/loans")  # straight to the domain service
    assert any("calls the loan domain service directly" in p for p in rg.integration(root, base, [], orch, "loan"))

    domain = cs.parse_integration_routing(routing(target="domain-service", api="openapi.yaml"))
    _switch(root, "/internal/api/v1/loan/loans")
    assert rg.integration(root, base, [], domain, "loan") == []
    _switch(root, "/internal/api/v1/loan/orchestrator/loans")
    assert any("the feature has no orchestrator" in p for p in rg.integration(root, base, [], domain, "loan"))

    _switch(root, "/internal/api/v1/loan/orchestrator/loans", prop_value="service")
    assert any("defaults to legacy" in p for p in rg.integration(root, base, [], orch, "loan"))
    (root / ROUTER).unlink()
    (root / "web/src/main/resources/application.properties").write_text("app.name=web\n")
    out = "\n".join(rg.integration(root, base, [], orch, "loan"))
    assert "The router" in out and "The toggle migration.loan.create-loan.route is not read" in out


def test_a_stub_run_routes_a_feature_with_an_orchestrator_to_the_orchestrator_service(client):
    from tests.conftest import start_run, wait_for

    r = start_run(client, {"domain": "customer", "operation": "routing-stub"})
    run_id = r.json()["id"]
    for _ in range(8):
        run = wait_for(client, run_id, {"awaiting_approval", "completed"}, timeout=30)
        if run["status"] == "completed":
            break
        client.post(f"/api/runs/{run_id}/decision", json={"reviewer": "t", "decision": "approve"})
    docs = {a["name"]: a for a in client.get(f"/api/runs/{run_id}/artifacts").json()}
    text = client.get(f"/api/artifacts/{docs[cs.INTEGRATION_ROUTING_FILE]['id']}").json()["content"]
    declared = cs.parse_integration_routing(text)
    assert declared["target"] == "orchestrator-service" and declared["api"] == "orchestrator-openapi.yaml"
    assert declared["toggle"]["default"] == "legacy"
