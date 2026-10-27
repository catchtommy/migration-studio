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
    steps = [{**st, "code": {"repository": ""}} if st.get("key") == "modular_monolith" else st for st in wf["steps"]]
    assert proj.put("/api/workflows/standard?library=project", json={"steps": steps}).status_code == 200
    run = proj.post("/api/runs", json={"domain": "loan", "operation": "approve"}).json()
    run = finish_workflow(proj, run["id"])
    ws = Path(proj.get(f"/api/runs/{run['id']}").json()["workspace"])
    java = ws / "loan-domain/src/main/java/com/example/loan/domain/approve/ApproveStub.java"
    assert java.read_text().startswith("package com.example.loan.domain.approve;")
    assert (ws / "loan-domain/pom.xml").exists()
    assert "loan-domain/src/main/java/com/example/loan/domain/approve/ApproveStub.java" in git(ws, "ls-files")
