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


def test_the_modules_and_packages_are_named_from_the_project_settings_alone(client, tmp_path):
    project = new_project(client)  # base_package_prefix com.acme
    mods = domain_modules.names(project, "loan")
    assert (mods["domain"].path, mods["domain"].artifact, mods["domain"].base_package) == \
        ("loan-domain", "com.acme:loan-domain", "com.acme.loan")
    assert (mods["orchestrator"].path, mods["orchestrator"].artifact, mods["orchestrator"].base_package) == \
        ("loan-orchestrator", "com.acme:loan-orchestrator", "com.acme.orchestrator.loan")
    assert domain_modules.base_package(project, "customer-comm", "service") == "com.acme.customercomm"
    assert domain_modules.base_package(project, "customer-comm", "orchestrator_service") == \
        "com.acme.orchestrator.customercomm"

    brief = domain_modules.brief(project, "loan", None, tmp_path)
    assert "Domain module: `loan-domain/`, artifact `com.acme:loan-domain`, base package `com.acme.loan`" in brief
    assert "Orchestrator module: `loan-orchestrator/`, artifact `com.acme:loan-orchestrator`, base package " \
           "`com.acme.orchestrator.loan`" in brief
    assert "Not in your checkout" in brief and "create it here, with exactly these names" in brief
    assert domain_modules.problems(project, "loan", tmp_path, "domain", "loan-domain", "com.acme:loan-domain") == []
    wrong = domain_modules.problems(project, "loan", tmp_path, "domain", "loan-domain/approve/domain")
    assert wrong and "The domain's domain module is loan-domain" in wrong[0]
    wrong = domain_modules.problems(project, "loan", tmp_path, "domain", "loan-domain", "com.acme:approve-module")
    assert any("is the artifact com.acme:loan-domain" in p for p in wrong)

    other = new_project(client, domain_module_dir_pattern="modules/{domain}")
    assert domain_modules.names(other, "loan")["domain"].artifact == "com.acme:loan"
    unset = new_project(client, base_package_prefix="")
    assert domain_modules.names(unset, "loan")["domain"].base_package is None
    assert "base package prefix isn't set" in domain_modules.brief(unset, "loan", None, tmp_path)
    assert any("base package prefix isn't set" in p
               for p in domain_modules.problems(unset, "loan", tmp_path, "domain", "loan-domain"))


def test_a_feature_is_told_what_its_checkout_holds_of_the_domain_module_and_extends_it(client, tmp_path):
    import subprocess

    project = new_project(client)
    ws = checkout(tmp_path)  # merged into the base branch by earlier features
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=ws, check=True,
                       capture_output=True)
    brief = domain_modules.brief(project, "loan", "this-run", ws)
    assert "In your checkout (from the base branch): extend it" in brief
    assert "LoanDao" in brief and "LoanService" in brief  # classes to reuse
    assert "LoanDomain.xml:getLoan" in brief and "LoanDomain.xml:insertLoan" in brief  # statements to reuse
    assert "Feature toggles already in your checkout (keep each one, with its methods): migration.loan.approve.route" \
        in brief
    second = domain_modules.problems(project, "loan", ws, "domain", "loan-domain-2")
    assert second and "build this feature into it, not loan-domain-2" in second[0]


def test_nothing_of_other_features_runs_is_read(client, tmp_path):
    """Strict independence: whatever another feature's run declared (in progress, failed, or finished but not merged),
    this feature's names, checks and brief are the same: from the project's settings and its own checkout only."""
    from app.db.models import StageExecution

    project = _features(client, "approve", "reject")
    ws, base = _repo(tmp_path)
    out = tmp_path / "docs"
    out.mkdir()

    def everything() -> tuple:
        return (domain_modules.names(project, "loan"),
                domain_modules.problems(project, "loan", ws, "domain", "loan-domain", "com.acme:loan-domain", base),
                domain_modules.package_problems(project, "loan", "reject", ws, base, "domain", "loan-domain",
                                                {"base_package": "com.acme.loan", "uses_features": []}),
                domain_modules.brief(project, "loan", "this-run", ws))

    before = everything()
    run_id = earlier_run(project, "approve", module_extraction("approve", path="approve-domain").replace(
        "bridge_dir:", "base_package: com.acme.approve\nbridge_dir:"))
    assert everything() == before  # finished, not merged
    with session_scope() as s:
        s.get(Run, run_id).status = "running"
        s.add(StageExecution(run_id=run_id, stage="modular_monolith", attempt=2, status="failed"))
    assert everything() == before  # in progress, a failed attempt
    assert "approve-domain" not in before[3] and "com.acme.approve" not in before[3]


def test_the_domains_module_and_base_package_are_never_named_after_a_feature(client, tmp_path):
    project = _features(client, "manage-preference", "view-preference")
    ws, base = _repo(tmp_path)
    problems = domain_modules.problems(project, "loan", ws, "domain", "loan-domain",
                                       "com.acme:manage-preference-module")
    assert any("is the artifact com.acme:loan-domain" in p for p in problems)
    problems = domain_modules.package_problems(project, "loan", "manage-preference", ws, base, "domain",
                                               "loan-domain", {"base_package": "com.acme.managepreference",
                                                               "uses_features": []})
    assert any("base_package is com.acme.managepreference, but the domain module's is com.acme.loan" in p
               for p in problems)


def test_the_design_declares_the_domains_one_module_and_the_features_package(client, tmp_path):
    from app import contracts_store as cs

    project = _features(client, "manage-preference", "view-preference")
    out = tmp_path / "docs"
    out.mkdir()

    def design(**fields) -> list[str]:
        lines = ["domain: loan", "feature: manage-preference", *(f"{k}: {v}" for k, v in fields.items()),
                 *(f"{k}: []" for k in ("owned_tables", "read_only_tables", "utility_modules",
                                         "other_domain_dependencies", "context_ports", "schema_changes", "ui_changes",
                                         "stack_changes", "god_module_dependencies"))]
        (out / cs.DOMAIN_DESIGN_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return domain_modules.design_problems(project, "loan", "manage-preference", out)

    assert any("add module, artifact, base_package, feature_package" in p for p in design())
    good = {"module": "loan-domain", "artifact": "com.acme:loan-domain", "base_package": "com.acme.loan",
            "feature_package": "com.acme.loan.managepreference"}
    assert design(**good) == []
    wrong = design(**{**good, "module": "manage-preference", "artifact": "com.acme:manage-preference",
                      "base_package": "com.acme.loan.domain", "feature_package": "com.acme.loan"})
    assert any("but the domain's is loan-domain" in p for p in wrong)
    assert any("but the domain's is com.acme:loan-domain" in p for p in wrong)
    assert any("base_package is com.acme.loan.domain, but the domain's is com.acme.loan" in p for p in wrong)
    assert any("go in com.acme.loan.managepreference" in p for p in wrong)

    # A cross-domain feature also declares the orchestrator module: a separate Maven module, com.acme.orchestrator.loan
    (out / cs.DOMAIN_DEPENDENCIES_FILE).write_text("decision:\n  scope: cross-domain\n  reason: calls customer\n"
                                                   "dependencies:\n  - domain: customer\n    relation: calls\n"
                                                   "    touchpoints: [{kind: service, ref: a.B#c}]\n",
                                                   encoding="utf-8")
    assert any("add orchestrator_module" in p for p in design(**good))
    orch = {"module": "loan-orchestrator", "artifact": "com.acme:loan-orchestrator",
            "base_package": "com.acme.orchestrator.loan",
            "feature_package": "com.acme.orchestrator.loan.managepreference"}
    nested = "{" + ", ".join(f"{k}: {v}" for k, v in orch.items()) + "}"
    assert design(**good, orchestrator_module=nested) == []
    same = "{" + ", ".join(f"{k}: {v}" for k, v in {**orch, "base_package": "com.acme.loan"}.items()) + "}"
    assert any("but the domain's is com.acme.orchestrator.loan" in p for p in design(**good, orchestrator_module=same))


def test_features_merged_into_the_base_branch_keep_their_toggle_in_the_router(client, tmp_path):
    """A toggle the router reads on the base branch (a feature merged there) must stay; one only on another feature's
    branch is no concern of this feature's."""
    import subprocess

    project = new_project(client)
    ws, base = _repo(tmp_path)
    router = ws / "web/src/main/java/com/acme/loan/bridge/LoanServiceRouter.java"
    router.parent.mkdir(parents=True)
    router.write_text('class LoanServiceRouter { String p = "migration.loan.reject.route"; }')
    assert domain_modules.problems(project, "loan", ws, "domain", "loan-domain", None, base) == []
    assert domain_modules.domain_bridges(ws, base, "loan") == set()  # no router on the base branch yet

    router.write_text('class LoanServiceRouter { String p = "migration.loan.approve.route"; }')  # merged into base
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "merged"], cwd=ws, check=True)
    merged = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws, check=True, capture_output=True, text=True).stdout
    assert domain_modules.domain_bridges(ws, merged.strip(), "loan") == {"web/src/main/java/com/acme/loan/bridge"}
    router.write_text('class LoanServiceRouter { String p = "migration.loan.reject.route"; }')  # dropped: refused
    problems = domain_modules.problems(project, "loan", ws, "domain", "loan-domain", None, merged.strip())
    assert any("no longer reads migration.loan.approve.route" in p for p in problems)


def test_the_brief_says_each_feature_runs_on_its_own_branch_and_a_design_step_gets_no_checkout_notes(client, tmp_path):
    project = new_project(client)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "Each feature on its own branch" in domain_modules.brief(project, "loan", "this-run", empty)
    design = domain_modules.brief(project, "loan", "this-run", None, "modulith_design")
    assert "Not in your checkout" not in design and "In your checkout" not in design
    assert "base package `com.acme.loan`" in design  # the names, to design against


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


DECL = {"base_package": "com.acme.loan", "uses_features": []}


def test_each_features_code_goes_in_its_own_package_and_shared_code_in_shared(client, tmp_path):
    project = _features(client, "create-loan", "view-loan", "close-loan")
    ws, base = _repo(tmp_path)
    _java(ws, "com.acme.loan.createloan", "CreateLoanService")
    _java(ws, "com.acme.loan.createloan.dto", "CreateLoanRequest")  # sub-packages are fine
    _java(ws, "com.acme.loan.shared", "Loan")
    _java(ws, "com.acme.loan.closeloan", "LoanCloser")  # another feature's package, not migrated yet: fine
    check = lambda decl: domain_modules.package_problems(project, "loan", "create-loan", ws, base,  # noqa: E731
                                                          "domain", "loan-domain", decl)
    assert check(DECL) == []
    assert "Add base_package" in check({})[0]

    _java(ws, "com.acme.loan", "Misplaced")  # directly in the base package
    _java(ws, "com.acme.loan.repayloan", "Unknown")  # no such feature
    problems = check(DECL)
    assert any("Misplaced.java is in package com.acme.loan:" in p for p in problems)
    assert any("repayloan, which is no feature of the domain" in p for p in problems)


def test_a_feature_using_another_features_package_lists_it(client, tmp_path):
    """F1 (create-loan) uses F2's (view-loan) functionality: it goes in F2's package, on F1's branch, created even
    though F2 hasn't started, and F1 lists F2 in uses_features."""
    project = _features(client, "create-loan", "view-loan")
    ws, base = _repo(tmp_path)
    _java(ws, "com.acme.loan.viewloan", "LoanReader")
    _java(ws, "com.acme.loan.createloan", "CreateLoanService",
          "import com.acme.loan.viewloan.LoanReader;\nimport com.acme.loan.shared.Loan;")
    check = lambda decl: domain_modules.package_problems(project, "loan", "create-loan", ws, base,  # noqa: E731
                                                          "domain", "loan-domain", decl)
    assert any("uses view-loan: list it in uses_features" in p for p in check(DECL))
    assert check({**DECL, "uses_features": ["view-loan"]}) == []


def test_the_modules_base_package_comes_from_the_settings_and_the_brief_lists_the_feature_packages(client, tmp_path):
    project = _features(client, "approve", "reject")
    ws, base = _repo(tmp_path)
    problems = domain_modules.package_problems(project, "loan", "reject", ws, base, "domain", "loan-domain",
                                               {"base_package": "com.acme.loans", "uses_features": []})
    assert any("the domain module's is com.acme.loan" in p for p in problems)
    problems = domain_modules.package_problems(project, "loan", "reject", ws, base, "orchestrator",
                                               "loan-orchestrator", {"base_package": "com.acme.loan",
                                                                     "uses_features": []})
    assert any("the orchestrator module's is com.acme.orchestrator.loan" in p for p in problems)
    brief = domain_modules.brief(project, "loan", "this-run", ws)
    assert "approve → approve, reject → reject; shared → shared" in brief
    assert "base package `com.acme.loan`" in brief


def test_stateless_helpers_go_in_util_and_using_util_or_shared_is_no_feature_interaction(client, tmp_path):
    project = _features(client, "create-loan", "util")
    ws, base = _repo(tmp_path)
    _java(ws, "com.acme.loan.util", "LoanNumberFormatter")
    _java(ws, "com.acme.loan.util.dates", "LoanDates")  # sub-packages too
    _java(ws, "com.acme.loan.shared", "Loan")
    _java(ws, "com.acme.loan.createloan", "CreateLoanService",
          "import com.acme.loan.util.LoanNumberFormatter;\nimport com.acme.loan.shared.Loan;")
    problems = domain_modules.package_problems(project, "loan", "create-loan", ws, base, "domain", "loan-domain",
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
    java = ws / "loan-domain/src/main/java/com/acme/loan/approve/ApproveStub.java"
    assert java.read_text().startswith("package com.acme.loan.approve;")
    assert (ws / "loan-domain/pom.xml").exists()
    assert "loan-domain/src/main/java/com/acme/loan/approve/ApproveStub.java" in git(ws, "ls-files")


def _service_java(ws: Path, package: str, name: str, body: str = "", folder: str = "src/main/java") -> None:
    path = ws / folder / package.replace(".", "/")
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{name}.java").write_text(f"package {package};\n{body}\npublic class {name} {{}}\n")


def test_the_domain_service_keeps_one_project_and_a_package_per_feature(client, tmp_path):
    project = _features(client, "create-loan", "view-loan")
    ws, base = _repo(tmp_path)
    svc = "com.acme.loan"
    _service_java(ws, svc, "LoanServiceApplication", "public static void main(String[] a) {}")  # the main class: root OK
    _service_java(ws, f"{svc}.createloan.web", "CreateLoanController")
    _service_java(ws, f"{svc}.createloan.web", "CreateLoanRequest")
    _service_java(ws, f"{svc}.shared.web", "RestExceptionHandler")
    _service_java(ws, f"{svc}.util", "Dates")
    _service_java(ws, f"{svc}.createloan", "CreateLoanServiceTest", folder="src/test/java")
    decl = {"base_package": svc, "uses_features": []}
    check = lambda d=decl: domain_modules.service_problems(project, "loan", "create-loan", ws, base,  # noqa: E731
                                                            "service", d)
    assert check() == []

    _service_java(ws, svc, "LoanConfig")  # not a main class: not at the root
    (ws / "loan-api").mkdir()
    (ws / "loan-api/pom.xml").write_text("<project/>")  # a second project
    problems = check()
    assert any("LoanConfig.java is in package com.acme.loan:" in p for p in problems)
    assert any("loan-api/pom.xml: a second project" in p for p in problems)
    assert "Add base_package" in check({})[0]
    # Modernise / Service tests: no declaration of their own; the settings give the package
    assert domain_modules.service_problems(project, "loan", "create-loan", ws, base, "service", None,
                                           one_project=False) != []


def test_the_services_base_packages_come_from_the_settings(client, tmp_path):
    project = _features(client, "approve", "reject")
    ws, base = _repo(tmp_path)
    problems = domain_modules.service_problems(project, "loan", "reject", ws, base, "service",
                                               {"base_package": "com.acme.loan.service", "uses_features": []})
    assert any("but the service's is com.acme.loan" in p for p in problems)
    problems = domain_modules.service_problems(project, "loan", "reject", ws, base, "orchestrator_service",
                                               {"base_package": "com.acme.loan", "uses_features": []})
    assert any("but the orchestrator service's is com.acme.orchestrator.loan" in p for p in problems)
    brief = domain_modules.brief(project, "loan", "this-run", ws)
    assert "## This domain's services" in brief and "base package `com.acme.loan`" in brief
    assert "Orchestrator service: repository not set in the domain's Agent inputs yet; base package " \
           "`com.acme.orchestrator.loan`" in brief


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
    java = Path(detail["workspace"]) / service["path"] / "src/main/java/com/acme/loan/approve/ApproveServiceStub.java"
    assert java.read_text().startswith("package com.acme.loan.approve;")


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
    assert "src/main/java/com/acme/loan/approve/ApproveServiceStub.java" in info["files"]
    brief = domain_modules.brief(key, "loan", run["id"], ws, "modernise")
    assert "## The lifted feature" in brief and "ApproveServiceStub.java" in brief and "READ ONLY" in brief
    assert "Keep the lifted service's base package (`com.acme.loan`)" in brief
    assert f"Lifted service (the module's stack" in brief and f"`{tmp_path / 'loan-lift'}`" in brief
    assert f"Modern service (Modernise, Service tests; the one the monolith calls): `{tmp_path / 'loan-modern'}`" in brief

    lift_root = ws / info["path"]

    modern = next(r for r in detail["repositories"] if r["url"] == str(tmp_path / "loan-modern"))
    ctx = SimpleNamespace(run_id=run["id"], domain="loan", operation="approve", stage="modernise", workspace=ws,
                          code_dir=modern["path"], base_sha=modern["base_sha"], output_dir=tmp_path / "docs")
    assert any("Nothing converted for approve" in p for p in domain_modules.modern_problems(ctx))  # the stub converts nothing
    target = ws / modern["path"] / "src/main/java/com/acme/loan/approve/ApproveService.java"
    target.parent.mkdir(parents=True)
    target.write_text("package com.acme.loan.approve;\npublic class ApproveService {}\n")
    other = ws / modern["path"] / "src/main/java/com/acme/loan/reject/RejectService.java"
    other.parent.mkdir(parents=True)
    other.write_text("package com.acme.loan.reject;\npublic class RejectService {}\n")
    problems = domain_modules.modern_problems(ctx)
    assert not any("Nothing converted" in p for p in problems)
    assert any("RejectService.java is in another feature's package (reject)" in p for p in problems)
    from app import service_guardrails as sg
    operations = sg.openapi_paths(domain_modules._own_document(run["id"], "openapi.yaml"))
    assert operations and any("No controller of the modern service maps" in p for p in problems)  # REST layer missing
    controller = ws / modern["path"] / "src/main/java/com/acme/loan/approve/web/ApproveController.java"
    controller.parent.mkdir(parents=True)
    controller.write_text("package com.acme.loan.approve.web;\n@RestController\npublic class ApproveController {\n"
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
    kept = modern_root / "src/main/java/com/acme/loan/view/ViewService.java"
    kept.parent.mkdir(parents=True)
    kept.write_text("package com.acme.loan.view;\npublic class ViewService {}\n")
    git(modern_root, "add", "-A")
    git(modern_root, "commit", "-qm", "an earlier feature's modern code")
    ctx.base_sha = git(modern_root, "rev-parse", "HEAD")
    kept.unlink()
    assert any("ViewService.java of another feature (view) was deleted or moved" in p
               for p in domain_modules.modern_problems(ctx))
    git(modern_root, "checkout", "--", str(kept.relative_to(modern_root)))

    # This feature's package using another feature's without uses_features: refused for Modernise too
    using = modern_root / "src/main/java/com/acme/loan/approve/ApproveFlow.java"
    using.write_text("package com.acme.loan.approve;\nimport com.acme.loan.view.ViewService;\n"
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
    assert domain_modules.problems(project, "loan", tmp_path, "domain", "loan-domain\\") == []

    # A step's recorded diff with CRLF line endings still names its files
    diff = "diff --git a/src/A.java b/src/A.java\r\n--- a/src/A.java\r\n+++ b/src/A.java\r\n@@ -1 +1 @@\r\n-x\r\n+y\r\n"
    assert domain_modules.DIFF_TARGET.findall(diff) == ["src/A.java"]


def test_the_domain_architecture_and_lift_rules_are_shared_texts_editable_from_the_agents_page(client, tmp_path):
    """Their wording lives in the database (Agents → Shared texts); the studio only fills in the run's data. Saving one
    without a {{data.…}} the studio fills in is refused; Reset puts the default back."""
    texts = {t["key"]: t for t in client.get("/api/shared-texts").json()}
    assert {"domain_architecture", "lift_rules"} <= set(texts)
    assert "Module in monolith" in texts["domain_architecture"]["used_by"] and texts["lift_rules"]["used_by"]
    original = texts["domain_architecture"]["text"]
    project = new_project(client)
    try:
        edited = original.replace("**One package per feature.**", "**One package per feature (house rule 7).**")
        assert client.put("/api/shared-texts/domain_architecture", json={"text": edited}).status_code == 200
        brief = domain_modules.brief(project, "loan", None, tmp_path)
        assert "house rule 7" in brief and "{{data." not in brief
        assert "Feature packages (key → package): no features listed for the domain yet; shared → shared" in brief

        r = client.put("/api/shared-texts/domain_architecture", json={"text": edited.replace("{{data.modules}}", "")})
        assert r.status_code == 422 and "{{data.modules}}" in r.json()["detail"]
        r = client.put("/api/shared-texts/lift_rules", json={"text": "## Lifted\n{{data.lift_path}} {{data.feature}}"})
        assert r.status_code == 422 and "{{data.lifted_files}}" in r.json()["detail"]
    finally:
        assert client.post("/api/shared-texts/domain_architecture/reset").status_code == 200
    assert client.get("/api/shared-texts").json() and "house rule 7" not in domain_modules.brief(project, "loan", None, tmp_path)


def test_modernise_converts_what_was_pushed_to_the_lift_by_hand_too(client, tmp_path, monkeypatch):
    import subprocess

    from app import delivery

    project = new_project(client)
    ws = tmp_path / "ws"
    lift = ws / "lift"
    lift.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=lift,  # noqa: E731
                                    check=True, capture_output=True, text=True).stdout.strip()
    run("init", "-q")
    (lift / "README").write_text("lift")
    run("add", "-A")
    run("commit", "-qm", "base")
    base = run("rev-parse", "HEAD")
    for name in ("Approve.java", "HandFix.java"):  # the step's work, then a class someone pushed by hand
        (lift / name).write_text(f"class {name[:-5]} {{}}\n")
        run("add", "-A")
        run("commit", "-qm", name)
    with session_scope() as s:
        r = Run(project=project, domain="loan", operation="approve", status="running", workflow="standard",
                repositories=[{"key": "lift", "url": "https://git/lift", "path": "lift", "base_sha": base}])
        s.add(r)
        s.flush()
        run_id = r.id
    monkeypatch.setattr(delivery, "domain_inputs",
                        lambda p, d: {domain_modules.LIFT_AGENT: {"repository": "https://git/lift"}})
    monkeypatch.setattr(domain_modules, "_run_changes", lambda rid, key: ["Approve.java"])  # what the step recorded
    assert domain_modules.lifted(project, "loan", run_id, ws)["files"] == ["Approve.java", "HandFix.java"]




def test_feature_and_domain_keys_that_would_share_a_package_are_refused(client):
    from app import discovery_store
    from tests.test_projects import as_project

    project = _features(client, "create-loan")
    proj = as_project(client, project)
    for clash in ("create_loan", "CreateLoan", "createloan"):
        r = proj.post("/api/domains/loan/features", json={"key": clash, "name": clash})
        assert r.status_code == 409 and "would share the package createloan" in r.json()["detail"], clash
    assert proj.post("/api/domains/loan/features", json={"key": "close-loan", "name": "x"}).status_code == 201

    listing = ("features:\n  - {key: view-loan, name: View}\n  - {key: View_Loan, name: View again}\n")
    try:
        discovery_store.parse_features(listing)
        raise AssertionError("a listing with two keys of one package was accepted")
    except discovery_store.InvalidDocument as e:
        assert "would share the package viewloan" in str(e)

    proj.post("/api/domains", json={"key": "customer-comm", "name": "Comm", "description": ""})
    r = proj.post("/api/domains", json={"key": "customercomm", "name": "Comm 2", "description": ""})
    assert r.status_code == 409 and "would share the package customercomm" in r.json()["detail"]


def test_a_feature_package_already_on_the_branch_stays_valid_when_the_feature_list_changes(client, tmp_path):
    """create-loan's run created view-loan's package (it uses it); the domain's features were listed again since and
    view-loan isn't on the list any more: what is on the branch where the step started is still a feature package."""
    import subprocess

    project = _features(client, "create-loan")  # view-loan no longer listed
    ws, _ = _repo(tmp_path)
    _java(ws, "com.acme.loan.viewloan", "LoanReader")  # made by an earlier step of this run
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "earlier step"], cwd=ws,
                   check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws, check=True, capture_output=True, text=True).stdout.strip()
    _java(ws, "com.acme.loan.viewloan", "LoanReader", "// one more method")
    _java(ws, "com.acme.loan.createloan", "CreateLoanService", "import com.acme.loan.viewloan.LoanReader;")
    check = lambda decl: domain_modules.package_problems(project, "loan", "create-loan", ws, base,  # noqa: E731
                                                          "domain", "loan-domain", decl)
    assert any("uses viewloan: list it in uses_features" in p for p in check(DECL))
    assert check({**DECL, "uses_features": ["view-loan"]}) == []  # listed by its key: the same package
    _java(ws, "com.acme.loan.repayloan", "Unknown")  # new, and no feature of the domain: still refused
    assert any("repayloan, which is no feature of the domain" in p for p in check({**DECL, "uses_features": ["view-loan"]}))
