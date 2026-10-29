"""One domain module and one orchestrator module per domain, which every feature of the domain extends."""

from pathlib import Path

from app import domain_modules
from app.db.models import Artifact, Run
from app.db.session import session_scope
from tests.test_projects import new_project


def module_extraction(feature: str, path: str = "loan-domain", bridge: str = "web/src/main/java/com/acme/loan/bridge") -> str:
    return f"""feature: {feature}
domain: loan
scope: domain-only
modules:
  - path: {path}
    kind: domain
    artifact: com.acme:loan-domain
bridge_dir: {bridge}
routing:
  property: migration.loan.{feature}.route
  default: legacy
  router: {bridge}/LoanServiceRouter.java
  routes:
    - bean: loanService
      legacy_bean: loanServiceLegacy
      interface: com.acme.loan.LoanService
      module: com.acme.loan.domain.LoanService
outside_changes: []
events: [LoanCreated]
"""


def earlier_run(project: str, feature: str, text: str) -> str:
    with session_scope() as s:
        run = Run(project=project, domain="loan", operation=feature, status="completed")
        s.add(run)
        s.flush()
        s.add(Artifact(run_id=run.id, stage="modular_monolith", attempt=1, name="module-extraction.yaml",
                       path=f".migration/loan/{feature}/module-extraction.yaml", kind="yaml", content=text))
        return run.id


def checkout(tmp_path: Path, router_reads: str = "migration.loan.approve.route") -> Path:
    ws = tmp_path / "ws"
    java = ws / "loan-domain/src/main/java/com/acme/loan/domain"
    java.mkdir(parents=True)
    (java / "LoanService.java").write_text("package com.acme.loan.domain;\npublic interface LoanService {}\n")
    (java / "LoanDao.java").write_text("package com.acme.loan.domain;\npublic class LoanDao {}\n")
    maps = ws / "loan-domain/src/main/resources"
    maps.mkdir(parents=True)
    (maps / "LoanDomain.xml").write_text('<sqlMap><select id="getLoan">SELECT 1</select>'
                                         '<insert id="insertLoan">INSERT</insert></sqlMap>')
    bridge = ws / "web/src/main/java/com/acme/loan/bridge"
    bridge.mkdir(parents=True)
    (bridge / "LoanServiceRouter.java").write_text(f'class LoanServiceRouter {{ String p = "{router_reads}"; }}')
    return ws


def test_the_first_feature_creates_the_modules_where_the_settings_say(client, tmp_path):
    project = new_project(client)
    mods = domain_modules.existing(project, "loan", None, tmp_path)
    assert mods["domain"].path == "loan-domain" and mods["orchestrator"].path == "loan-orchestrator"
    brief = domain_modules.brief(project, "loan", None, tmp_path)
    assert "Domain module: none yet. The first feature creates it at `loan-domain/`" in brief
    assert domain_modules.problems(project, "loan", None, tmp_path, "domain", "loan-domain") == []
    wrong = domain_modules.problems(project, "loan", None, tmp_path, "domain", "loan-domain/approve/domain")
    assert wrong and "goes at loan-domain" in wrong[0]

    other = new_project(client, domain_module_dir_pattern="modules/{domain}")
    assert domain_modules.existing(other, "loan", None, tmp_path)["domain"].path == "modules/loan"


def test_a_later_feature_is_told_what_the_domain_module_holds_and_extends_it(client, tmp_path):
    project = new_project(client)
    earlier_run(project, "approve", module_extraction("approve"))
    ws = checkout(tmp_path)
    brief = domain_modules.brief(project, "loan", "this-run", ws)
    assert "`loan-domain/` (com.acme:loan-domain), built by approve" in brief
    assert "LoanDao" in brief and "LoanService" in brief  # classes to reuse
    assert "LoanDomain.xml:getLoan" in brief and "LoanDomain.xml:insertLoan" in brief  # statements to reuse
    assert "toggle `migration.loan.approve.route` (approve)" in brief

    assert domain_modules.problems(project, "loan", "this-run", ws, "domain", "loan-domain") == []
    second = domain_modules.problems(project, "loan", "this-run", ws, "domain", "loan-domain-2")
    assert second and "already has its domain module at loan-domain" in second[0]


def test_an_earlier_features_module_missing_from_the_checkout_stops_the_step(client, tmp_path):
    project = new_project(client)
    earlier_run(project, "approve", module_extraction("approve"))
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "NOT in this checkout" in domain_modules.brief(project, "loan", "this-run", empty)
    problems = domain_modules.problems(project, "loan", "this-run", empty, "domain", "loan-domain")
    assert any("commit and push it first" in p for p in problems)


def test_earlier_features_keep_their_toggle_in_the_router(client, tmp_path):
    project = new_project(client)
    earlier_run(project, "approve", module_extraction("approve"))
    ws = checkout(tmp_path, router_reads="migration.loan.reject.route")  # approve's toggle was dropped
    problems = domain_modules.problems(project, "loan", "this-run", ws, "domain", "loan-domain")
    assert any("no longer reads migration.loan.approve.route" in p for p in problems)


def test_module_folder_settings_are_relative_folders_naming_the_domain(client):
    key = new_project(client)
    for bad in ("../x", "/abs", "fixed-name", "{feature}-domain"):
        r = client.put(f"/api/projects/{key}/settings", json={"domain_module_dir_pattern": bad})
        assert r.status_code == 422, bad
    assert client.put(f"/api/projects/{key}/settings",
                      json={"orchestrator_module_dir_pattern": "modules/{domain}-orchestrator"}).status_code == 200


def _repo(tmp_path: Path) -> tuple[Path, str]:
    import subprocess

    ws = tmp_path / "mono"
    ws.mkdir()
    run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=ws, check=True,  # noqa: E731
                                    capture_output=True, text=True).stdout.strip()
    run("init", "-q")
    (ws / "README").write_text("monolith")
    run("add", "-A")
    run("commit", "-qm", "start")
    return ws, run("rev-parse", "HEAD")


def _java(ws: Path, package: str, name: str, imports: str = "") -> None:
    folder = ws / "loan-domain/src/main/java" / package.replace(".", "/")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.java").write_text(f"package {package};\n{imports}\npublic class {name} {{}}\n")


def _features(client, *keys) -> str:
    from tests.test_projects import as_project

    project = new_project(client)
    proj = as_project(client, project)
    proj.post("/api/domains", json={"key": "loan", "name": "Loan", "description": ""})
    for k in keys:
        proj.post("/api/domains/loan/features", json={"key": k, "name": k})
    return project


DECL = {"base_package": "com.acme.loan.domain", "uses_features": []}


def test_each_features_code_goes_in_its_own_package_and_shared_code_in_shared(client, tmp_path):
    project = _features(client, "create-loan", "view-loan", "close-loan")
    ws, base = _repo(tmp_path)
    _java(ws, "com.acme.loan.domain.createloan", "CreateLoanService")
    _java(ws, "com.acme.loan.domain.createloan.dto", "CreateLoanRequest")  # sub-packages are fine
    _java(ws, "com.acme.loan.domain.shared", "Loan")
    _java(ws, "com.acme.loan.domain.closeloan", "LoanCloser")  # another feature's package, not migrated yet: fine
    check = lambda decl: domain_modules.package_problems(project, "loan", "create-loan", None, ws, base,  # noqa: E731
                                                          "domain", "loan-domain", decl)
    assert check(DECL) == []
    assert "Add base_package" in check({})[0]

    _java(ws, "com.acme.loan.domain", "Misplaced")  # directly in the base package
    _java(ws, "com.acme.loan.domain.repayloan", "Unknown")  # no such feature
    problems = check(DECL)
    assert any("Misplaced.java is in package com.acme.loan.domain:" in p for p in problems)
    assert any("repayloan, which is no feature of the domain" in p for p in problems)


def test_a_feature_using_another_features_package_lists_it(client, tmp_path):
    project = _features(client, "create-loan", "view-loan")
    ws, base = _repo(tmp_path)
    _java(ws, "com.acme.loan.domain.viewloan", "LoanReader")
    _java(ws, "com.acme.loan.domain.createloan", "CreateLoanService",
          "import com.acme.loan.domain.viewloan.LoanReader;\nimport com.acme.loan.domain.shared.Loan;")
    check = lambda decl: domain_modules.package_problems(project, "loan", "create-loan", None, ws, base,  # noqa: E731
                                                          "domain", "loan-domain", decl)
    assert any("uses view-loan: list it in uses_features" in p for p in check(DECL))
    assert check({**DECL, "uses_features": ["view-loan"]}) == []


def test_a_later_feature_keeps_the_modules_base_package_and_sees_the_packages(client, tmp_path):
    project = _features(client, "approve", "reject")
    earlier_run(project, "approve", module_extraction("approve").replace(
        "bridge_dir:", "base_package: com.acme.loan.domain\nuses_features: [reject]\nbridge_dir:"))
    ws, base = _repo(tmp_path)
    problems = domain_modules.package_problems(project, "loan", "reject", "this-run", ws, base, "domain", "loan-domain",
                                               {"base_package": "com.acme.loans", "uses_features": []})
    assert any("the module's is com.acme.loan.domain" in p for p in problems)
    brief = domain_modules.brief(project, "loan", "this-run", ws)
    assert "approve → approve, reject → reject; shared → shared" in brief
    assert "Base package: `com.acme.loan.domain`" in brief and "approve → reject" in brief


def test_stateless_helpers_go_in_util_and_using_util_or_shared_is_no_feature_interaction(client, tmp_path):
    project = _features(client, "create-loan", "util")
    ws, base = _repo(tmp_path)
    _java(ws, "com.acme.loan.domain.util", "LoanNumberFormatter")
    _java(ws, "com.acme.loan.domain.util.dates", "LoanDates")  # sub-packages too
    _java(ws, "com.acme.loan.domain.shared", "Loan")
    _java(ws, "com.acme.loan.domain.createloan", "CreateLoanService",
          "import com.acme.loan.domain.util.LoanNumberFormatter;\nimport com.acme.loan.domain.shared.Loan;")
    problems = domain_modules.package_problems(project, "loan", "create-loan", None, ws, base, "domain", "loan-domain",
                                               DECL)
    assert problems == []  # util and shared are allowed, and need no uses_features entry
    assert domain_modules.feature_segment("util") == "utilfeature"  # a feature called util keeps its own package
    assert "util → utilfeature" in domain_modules.brief(project, "loan", None, ws)
    assert "util → util." in domain_modules.brief(project, "loan", None, ws)


def test_a_stub_run_puts_its_placeholder_class_in_the_features_package_of_the_domain_module(client, tmp_path):
    from tests.test_delivery import finish_workflow, git, setup_project

    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    wf = proj.get("/api/workflows/standard").json()
    from tests.test_delivery import domain_repo
    domain_repo(proj, upstream, "modular_monolith")
    steps = [{**st, "code": {"repository": ""}} if st.get("key") == "modular_monolith" else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = finish_workflow(proj, run["id"])
    ws = Path(proj.get(f"/api/runs/{run['id']}").json()["workspace"])
    java = ws / "loan-domain/src/main/java/com/example/loan/domain/approve/ApproveStub.java"
    assert java.read_text().startswith("package com.example.loan.domain.approve;")
    assert (ws / "loan-domain/pom.xml").exists()
    assert "loan-domain/src/main/java/com/example/loan/domain/approve/ApproveStub.java" in git(ws, "ls-files")


def _service_java(ws: Path, package: str, name: str, body: str = "", folder: str = "src/main/java") -> None:
    path = ws / folder / package.replace(".", "/")
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{name}.java").write_text(f"package {package};\n{body}\npublic class {name} {{}}\n")


def test_the_domain_service_keeps_one_project_and_a_package_per_feature(client, tmp_path):
    project = _features(client, "create-loan", "view-loan")
    ws, base = _repo(tmp_path)
    svc = "com.acme.loan.service"
    _service_java(ws, svc, "LoanServiceApplication", "public static void main(String[] a) {}")  # the main class: root OK
    _service_java(ws, f"{svc}.createloan.web", "CreateLoanController")
    _service_java(ws, f"{svc}.createloan.web", "CreateLoanRequest")
    _service_java(ws, f"{svc}.shared.web", "RestExceptionHandler")
    _service_java(ws, f"{svc}.util", "Dates")
    _service_java(ws, f"{svc}.createloan", "CreateLoanServiceTest", folder="src/test/java")
    decl = {"base_package": svc, "uses_features": []}
    check = lambda d=decl: domain_modules.service_problems(project, "loan", "create-loan", None, ws, base,  # noqa: E731
                                                            "service", d)
    assert check() == []

    _service_java(ws, svc, "LoanConfig")  # not a main class: not at the root
    (ws / "loan-api").mkdir()
    (ws / "loan-api/pom.xml").write_text("<project/>")  # a second project
    problems = check()
    assert any("LoanConfig.java is in package com.acme.loan.service:" in p for p in problems)
    assert any("loan-api/pom.xml: a second project" in p for p in problems)
    assert "Add base_package" in check({})[0]
    # Modernise / Service tests: no declaration of their own; this run's microservice-extraction.yaml gives the package
    assert domain_modules.service_problems(project, "loan", "create-loan", None, ws, base, "service", None,
                                           one_project=False, base_hint=svc) != []


def test_a_later_feature_keeps_the_services_base_package(client, tmp_path):
    project = _features(client, "approve", "reject")
    with session_scope() as s:
        run = Run(project=project, domain="loan", operation="approve", status="completed")
        s.add(run)
        s.flush()
        s.add(Artifact(run_id=run.id, stage="microservice_scaffold", attempt=1, name="microservice-extraction.yaml",
                       path="x", kind="yaml", content=(
                           "feature: approve\ndomain: loan\nmodule_commit: abc\nbase_package: com.acme.loan.service\n"
                           "uses_features: [reject]\noperations:\n  - {method: LoanService#approve, operation_id: approve}\n"
                           "replaced: []\nopen_decisions: []\nevents: []\n")))
    ws, base = _repo(tmp_path)
    problems = domain_modules.service_problems(project, "loan", "reject", "this-run", ws, base, "service",
                                               {"base_package": "com.acme.loans", "uses_features": []})
    assert any("the service's is com.acme.loan.service" in p for p in problems)
    brief = domain_modules.brief(project, "loan", "this-run", ws)
    assert "## This domain's services" in brief and "built by approve; base package `com.acme.loan.service`" in brief
    assert "approve → reject" in brief and "repository not set in the domain's Agent inputs yet" in brief


def test_a_stub_run_adds_its_placeholder_to_the_domains_service_in_the_features_package(client, tmp_path):
    from tests.test_delivery import domain_repo, finish_workflow, setup_project
    from tests.test_projects import make_monolith

    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    make_monolith(tmp_path / "anything-lending")  # any name: the domain says which repository
    wf = proj.get("/api/workflows/standard").json()
    steps = [{**st, "code": {"repository": domain_repo(proj, tmp_path / "anything-lending", "microservice_scaffold")}}
             if st.get("key") == "microservice_scaffold" else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    run = finish_workflow(proj, proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()["id"])
    detail = proj.get(f"/api/runs/{run['id']}").json()
    service = next(r for r in detail["repositories"] if r["url"] == str(tmp_path / "anything-lending"))
    assert service["url"] == str(tmp_path / "anything-lending")
    java = Path(detail["workspace"]) / service["path"] / "src/main/java/com/example/loan/service/approve/ApproveServiceStub.java"
    assert java.read_text().startswith("package com.example.loan.service.approve;")


def test_two_repositories_modernise_is_told_the_lifted_files_and_checked_against_them(client, tmp_path):
    """The lifted service (Microservice step, old stack) and the modern service (Modernise) are two repositories of
    the domain. Modernise gets exactly the files this run lifted, may not touch the lift, and must convert them."""
    import subprocess
    from types import SimpleNamespace

    from tests.test_delivery import domain_repo, finish_workflow, git, setup_project
    from tests.test_projects import make_monolith

    upstream, key, proj = setup_project(client, tmp_path, monolith_step=False)
    for name in ("loan-lift", "loan-modern"):
        make_monolith(tmp_path / name)
    r = proj.put("/api/domains/loan", json={"agent_inputs": {"microservice_scaffold": {"repository": str(tmp_path / "loan-lift")},
                                                             "modernise": {"repository": str(tmp_path / "loan-lift")}}})
    assert r.status_code == 422 and "needs a repository of its own" in r.text  # old and new stack: never one project
    r = proj.put("/api/domains/loan", json={"agent_inputs": {"microservice_scaffold": {"repository": str(tmp_path / "loan-lift")},
                                                             "independent_tests": {"repository": str(tmp_path / "loan-modern")}}})
    assert r.status_code == 422 and "where its baseline is taken" in r.text  # the suite goes with the lifted service
    domain_repo(proj, tmp_path / "loan-lift", "microservice_scaffold", also={"modernise": tmp_path / "loan-modern"})
    wf = proj.get("/api/workflows/standard").json()
    steps = [{**st, "code": {"changes_code": True}} if st.get("key") in ("microservice_scaffold", "modernise") else st
             for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    run = finish_workflow(proj, proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()["id"])
    detail = proj.get(f"/api/runs/{run['id']}").json()
    ws = Path(detail["workspace"])

    info = domain_modules.lifted(key, "loan", run["id"], ws)
    assert info["url"] == str(tmp_path / "loan-lift")
    assert "src/main/java/com/example/loan/service/approve/ApproveServiceStub.java" in info["files"]
    brief = domain_modules.brief(key, "loan", run["id"], ws, "modernise")
    assert "## The lifted feature" in brief and "ApproveServiceStub.java" in brief and "READ ONLY" in brief
    assert "Keep the lifted service's base package, `com.example.loan.service`" in brief
    assert f"Lifted service (the module's stack" in brief and f"`{tmp_path / 'loan-lift'}`" in brief
    assert f"Modern service (Modernise, Service tests; the one the monolith calls): `{tmp_path / 'loan-modern'}`" in brief

    # Another feature's lift, pushed to the domain branch and merged into this run's checkout: not this run's to convert
    lift_root = ws / info["path"]
    other_file = lift_root / "src/main/java/com/example/loan/service/reject/RejectServiceStub.java"
    other_file.parent.mkdir(parents=True)
    other_file.write_text("package com.example.loan.service.reject;\npublic class RejectServiceStub {}\n")
    git(lift_root, "add", "-A")
    git(lift_root, "commit", "-qm", "reject's lift, merged in")
    assert not any("reject" in f for f in domain_modules.lifted(key, "loan", run["id"], ws)["files"])

    modern = next(r for r in detail["repositories"] if r["url"] == str(tmp_path / "loan-modern"))
    ctx = SimpleNamespace(run_id=run["id"], domain="loan", operation="approve", stage="modernise", workspace=ws,
                          code_dir=modern["path"], base_sha=modern["base_sha"], output_dir=tmp_path / "docs")
    assert any("Nothing converted for approve" in p for p in domain_modules.modern_problems(ctx))  # the stub converts nothing
    target = ws / modern["path"] / "src/main/java/com/example/loan/service/approve/ApproveService.java"
    target.parent.mkdir(parents=True)
    target.write_text("package com.example.loan.service.approve;\npublic class ApproveService {}\n")
    other = ws / modern["path"] / "src/main/java/com/example/loan/service/reject/RejectService.java"
    other.parent.mkdir(parents=True)
    other.write_text("package com.example.loan.service.reject;\npublic class RejectService {}\n")
    problems = domain_modules.modern_problems(ctx)
    assert not any("Nothing converted" in p for p in problems)
    assert any("RejectService.java is in another feature's package (reject)" in p for p in problems)
    from app import service_guardrails as sg
    operations = sg.openapi_paths(domain_modules._own_document(run["id"], "openapi.yaml"))
    assert operations and any("No controller of the modern service maps" in p for p in problems)  # REST layer missing
    controller = ws / modern["path"] / "src/main/java/com/example/loan/service/approve/web/ApproveController.java"
    controller.parent.mkdir(parents=True)
    controller.write_text("package com.example.loan.service.approve.web;\n@RestController\npublic class ApproveController {\n"
                          + "".join(f'  @{v.capitalize()}Mapping("{p}")\n  public void op{i}() {{}}\n'
                                    for i, (v, p, _id) in enumerate(operations)) + "}\n")
    assert not any("No controller" in p for p in domain_modules.modern_problems(ctx))
    lift_file = lift_root / "App.java"
    lift_file.write_text("changed by modernise")
    assert any("the lifted service is read only" in p for p in domain_modules.modern_problems(ctx))
    git(lift_root, "checkout", "--", "App.java")

    # Another feature's class, already in the modern service: deleting (or moving) it is refused
    other.unlink()
    modern_root = ws / modern["path"]
    kept = modern_root / "src/main/java/com/example/loan/service/view/ViewService.java"
    kept.parent.mkdir(parents=True)
    kept.write_text("package com.example.loan.service.view;\npublic class ViewService {}\n")
    git(modern_root, "add", "-A")
    git(modern_root, "commit", "-qm", "an earlier feature's modern code")
    ctx.base_sha = git(modern_root, "rev-parse", "HEAD")
    kept.unlink()
    assert any("ViewService.java of another feature (view) was deleted or moved" in p
               for p in domain_modules.modern_problems(ctx))
    git(modern_root, "checkout", "--", str(kept.relative_to(modern_root)))

    # This feature's package using another feature's without uses_features: refused for Modernise too
    using = modern_root / "src/main/java/com/example/loan/service/approve/ApproveFlow.java"
    using.write_text("package com.example.loan.service.approve;\nimport com.example.loan.service.view.ViewService;\n"
                     "public class ApproveFlow {}\n")
    ctx.ownership, ctx.config = {}, {}
    proj.post("/api/domains/loan/features", json={"key": "view", "name": "view"})
    problems = domain_modules.for_service(ctx, "service", None)
    assert any("uses view: list it in uses_features" in p for p in problems)
    del subprocess, git


def test_controller_mappings_read_constants_generated_interfaces_and_stay_lenient_on_what_they_cant_resolve(tmp_path):
    from types import SimpleNamespace

    def put(rel, text):
        f = tmp_path / "svc" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    put("src/main/java/a/Paths.java", 'public final class Paths {\n  public static final String BASE = "/api/broker";\n'
                                      '  public static final String ONE = "/brokers/{brokerId}";\n}\n')
    put("src/main/java/a/BrokerController.java",
        '@RestController\n@RequestMapping(Paths.BASE)\npublic class BrokerController {\n'
        '  @GetMapping(value = Paths.ONE, produces = "application/json") X get() {}\n'
        '  @PostMapping(path = {"/brokers"}, consumes = "application/json") X create() {}\n'
        '  @DeleteMapping(Elsewhere.UNKNOWN) X remove() {}\n}\n')
    put("target/generated-sources/openapi/a/BrokersApi.java",
        'public interface BrokersApi {\n  @PutMapping("/api/broker/brokers/{id}") default X update() { return null; }\n}\n')
    mapped = domain_modules.controller_mappings(tmp_path / "svc")
    assert ("GET", "/api/broker/brokers/{}") in mapped and ("POST", "/api/broker/brokers") in mapped
    assert ("PUT", "/api/broker/brokers/{}") in mapped  # a generated API interface counts
    assert ("DELETE", None) in mapped  # unresolved: assumed to serve, not reported missing
    assert not any(p == "/application/json" for _v, p in mapped)  # produces/consumes aren't paths

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "openapi.yaml").write_text(
        "paths:\n  /brokers/{brokerId}:\n    get: {operationId: getBroker}\n    put: {operationId: updateBroker}\n"
        "    delete: {operationId: removeBroker}\n  /brokers:\n    post: {operationId: createBroker}\n"
        "  /brokers/{brokerId}/close:\n    post: {operationId: closeBroker}\n")
    ctx = SimpleNamespace(output_dir=docs, run_id=None)
    problems = domain_modules._unmapped_operations(ctx, tmp_path / "svc")
    assert problems == [p for p in problems if "closeBroker" in p] and len(problems) == 1  # only the one really missing


def test_paths_and_repositories_work_the_same_on_windows(client, tmp_path):
    """Agents and people on Windows write backslashes, drive letters and paths with spaces; git writes forward
    slashes, and its diffs can end lines with CRLF there. They all name the same things."""
    from app import delivery, settings_store, workflow_store

    # Repositories: a local Windows path in another spelling is the same repository; URLs keep their case
    assert delivery._same(r"C:\Repos\Broker-Lift.git", "c:/repos/broker-lift/")
    assert delivery._same(r"\\server\share\broker", "//SERVER/share/broker")
    assert not delivery._same("https://github.com/Acme/Broker.git", "https://github.com/acme/other.git")
    assert delivery._same("https://github.com/acme/broker.git", "https://github.com/acme/broker")

    # A local path may have spaces; a URL can't
    assert workflow_store.repository_error(r"C:\Users\Jo Doe\repos\broker-lift") is None
    assert workflow_store.repository_error("https://github.com/acme/broker lift.git") is not None

    # Folder settings: backslashes are fine, climbing out isn't (either separator)
    project = new_project(client)
    assert settings_store._relative_folder(r"modules\{domain}") and not settings_store._relative_folder(r"..\x")
    assert domain_modules.posix(r"modules\loan\\") == "modules/loan"

    # A declaration written on Windows names the same module
    assert domain_modules.problems(project, "loan", None, tmp_path, "domain", "loan-domain\\") == []

    # A step's recorded diff with CRLF line endings still names its files
    diff = "diff --git a/src/A.java b/src/A.java\r\n--- a/src/A.java\r\n+++ b/src/A.java\r\n@@ -1 +1 @@\r\n-x\r\n+y\r\n"
    assert domain_modules.DIFF_TARGET.findall(diff) == ["src/A.java"]
