"""The release, test and orchestrator service checks as a step runs them (for_context): what each needs before it can
check anything, and how its report reads. The checks themselves are tested on real changes in their own test files."""

from types import SimpleNamespace

import pytest

from app import orchestrator_service_guardrails as osg
from app import release_guardrails as rg
from app import test_guardrails as tg


def ctx(tmp_path, **values):
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    base = dict(base_sha="abc123", output_dir=docs, rel=lambda p: p.name, workspace=tmp_path, code_dir="service",
                domain="broker", run_id="not-a-run", stage="independent_tests", ownership={}, config={"checks": []})
    return SimpleNamespace(**{**base, **values})


@pytest.mark.parametrize("module,name", [(rg, "Release"), (tg, "Test"), (osg, "Orchestrator service")])
def test_each_report_says_pass_or_lists_the_problems(module, name):
    assert module.report([]).startswith(f"{name} guardrails: PASS")
    failed = module.report(["first", "second"])
    assert failed.startswith(f"{name} guardrails: FAIL") and "- first\n- second" in failed


# --- release checks (Modernise, Monolith integration) --------------------------------------------------------------

def test_release_checks_run_only_when_the_step_has_them(tmp_path):
    assert rg.for_context(ctx(tmp_path), ["boundary"]) == []


def test_release_checks_need_the_starting_commit(tmp_path):
    problems = rg.for_context(ctx(tmp_path, base_sha=None), ["modernise_guardrails"])
    assert len(problems) == 1 and "starting commit is unknown" in problems[0]


def test_modernisation_never_changes_the_monolith(tmp_path):
    problems = rg.for_context(ctx(tmp_path, code_dir="."), ["modernise_guardrails"])
    assert any("never in the monolith" in p for p in problems)


def test_monolith_integration_needs_to_know_which_service_to_call(tmp_path):
    c = ctx(tmp_path)
    (c.output_dir / "orchestrator-extraction.yaml").write_text(
        "feature: f\ndomain: broker\norchestrator: module\nmodule: {path: o, artifact: 'a:b'}\n"
        "operations: [{method: A#b, replaces: C#b}]\n")  # an orchestrator in the monolith, but no orchestrator service
    problems = rg.for_context(c, ["integration_guardrails"])
    assert len(problems) == 1 and "can't route this feature" in problems[0]


# --- test checks (Independent tests, Service tests) ----------------------------------------------------------------

def test_test_checks_run_only_when_configured(tmp_path):
    assert tg.for_context(ctx(tmp_path)) == []  # the agent has none
    assert tg.for_context(ctx(tmp_path), ["boundary"]) == []  # none of them is a test check


def test_tests_are_written_in_the_service_repository_from_a_known_commit(tmp_path):
    problems = tg.for_context(ctx(tmp_path, code_dir="."), ["acceptance_test_guardrails"])
    assert len(problems) == 1 and "never in the monolith" in problems[0]
    problems = tg.for_context(ctx(tmp_path, base_sha=None), ["acceptance_test_guardrails"])
    assert len(problems) == 1 and "starting commit is unknown" in problems[0]


def test_a_repository_that_cant_be_compared_is_reported(tmp_path):
    (tmp_path / "service").mkdir()  # not a git checkout
    problems = tg.for_context(ctx(tmp_path), ["acceptance_suite_unchanged"])
    assert len(problems) == 1 and "Could not compare with the starting commit" in problems[0]


def test_the_orchestrator_suite_must_not_import_the_orchestrator_services_packages(client, tmp_path):
    c = ctx(tmp_path, domain="customer")
    (c.output_dir / "orchestrator-service-extraction.yaml").write_text("not: [valid")  # unreadable: ignored here
    packages = tg._orchestrator_packages(c)
    assert packages and all(p.startswith("com.acme") for p in packages)  # from the project's settings


# --- orchestrator service checks (Orchestrator microservice) -------------------------------------------------------

def test_the_orchestrator_service_is_built_in_its_own_repository_from_a_known_commit(tmp_path):
    assert "never in the monolith" in osg.for_context(ctx(tmp_path, code_dir="."))[0]
    assert "starting commit is unknown" in osg.for_context(ctx(tmp_path, base_sha=None))[0]


def test_the_orchestrator_service_step_needs_its_declaration(tmp_path):
    problems = osg.for_context(ctx(tmp_path))
    assert len(problems) == 1 and problems[0].startswith("Write orchestrator-service-extraction.yaml")
    c = ctx(tmp_path)
    (c.output_dir / "orchestrator-service-extraction.yaml").write_text("orchestrator: [unclosed")
    assert len(osg.for_context(c)) == 1  # the document's own error


def test_there_is_nothing_to_lift_without_an_orchestrator_module(tmp_path):
    c = ctx(tmp_path)
    (c.output_dir / "orchestrator-service-extraction.yaml").write_text(
        "feature: f\ndomain: broker\norchestrator: none\nreason: no orchestrator in the monolith\n")
    (c.output_dir / "orchestrator-extraction.yaml").write_text(
        "feature: f\ndomain: broker\norchestrator: none\nreason: domain-only\n")
    problems = osg.for_context(c)
    assert all("nothing to lift" not in p for p in problems)  # no service declared: nothing is expected
