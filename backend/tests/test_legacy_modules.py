"""The temporary modules for the domains not migrated yet (app/legacy_modules.py): the integration module's entry points,
the event-handler module's handlers, their published interfaces and the legacy adapters, checked by the Module in
monolith guardrail on the small throw-away monolith of test_module_guardrails."""

from app import legacy_modules as lm
from app import module_guardrails as g
from app.guardrail_problems import Blocker
from tests.test_module_guardrails import (BRIDGE, DECLARATION, MOD, OWNERSHIP, extract, git, put,  # noqa: F401
                                          repo)

KYC = "customer.get-kyc-status"
RECORD = "customer.record-broker-created"
EVENT = "broker.broker-created"
KYC_DELEGATE = "com.acme.customer.CustomerService#getKycStatus"
RECORD_DELEGATE = "com.acme.customer.CustomerHistoryService#recordBrokerCreated"
ENTRY_POINTS = [
    {"key": KYC, "provider": "customer", "operations": [{"method": "getKycStatus", "delegates_to": [KYC_DELEGATE]}],
     "delegates_to": [KYC_DELEGATE]},
    {"key": RECORD, "provider": "customer",
     "operations": [{"method": "recordBrokerCreated", "delegates_to": [RECORD_DELEGATE]}],
     "delegates_to": [RECORD_DELEGATE]},
]
HANDLERS = [{"event": EVENT, "domain": "customer", "calls": [RECORD], "delegates_to": [RECORD_DELEGATE],
             "interaction": "X2"}]
ADAPTERS = "web/src/main/java/com/acme/integration/legacy"
API = "interfaces/src/main/java/com/acme/integration/customer/api"
HANDLER_API = "interfaces/src/main/java/com/acme/eventhandler/customer/api"
INTEGRATION = "legacy-integration/src/main/java/com/acme/integration/customer"
HANDLERS_DIR = "legacy-event-handlers/src/main/java/com/acme/eventhandler/customer"
LEGACY_DECLARED = {"adapter_dir": ADAPTERS, "entry_points": [KYC, RECORD],
                   "handlers": [{"event": EVENT, "domain": "customer"}]}
HANDLER_IMPL = """package com.acme.eventhandler.customer;

import com.acme.eventhandler.customer.api.CustomerBrokerCreatedHandler;
import com.acme.integration.customer.api.CustomerRecordBrokerCreatedApi;
import java.util.logging.Logger;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.TransactionStatus;
import org.springframework.transaction.support.TransactionCallbackWithoutResult;
import org.springframework.transaction.support.TransactionTemplate;

/** Temporary: until customer subscribes to broker.broker-created. */
public class CustomerBrokerCreatedHandlerImpl implements CustomerBrokerCreatedHandler {
  private static final Logger LOG = Logger.getLogger("handlers");
  CustomerRecordBrokerCreatedApi history;
  PlatformTransactionManager transactionManager;

  public void handle(final String id) {
    TransactionTemplate tx = new TransactionTemplate(transactionManager);
    tx.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
    try {
      tx.execute(new TransactionCallbackWithoutResult() {
        protected void doInTransactionWithoutResult(TransactionStatus status) { history.recordBrokerCreated(id); }
      });
    } catch (RuntimeException e) {
      LOG.warning("broker.broker-created " + id + " not recorded: " + e);
    }
  }
}
"""
EVENT_ADAPTER = """package com.acme.broker.legacybridge;

import com.acme.eventhandler.customer.api.CustomerBrokerCreatedHandler;
import org.springframework.transaction.support.TransactionSynchronizationAdapter;
import org.springframework.transaction.support.TransactionSynchronizationManager;

class SpringBrokerEvents implements com.acme.broker.domain.BrokerEvents {
  CustomerBrokerCreatedHandler customerHandler;

  public void created(final String id) {
    if (!TransactionSynchronizationManager.isSynchronizationActive()) { customerHandler.handle(id); return; }
    TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronizationAdapter() {
      public void afterCommit() { customerHandler.handle(id); }
    });
  }
}
"""


def layout(interfaces="interfaces") -> lm.Layout:
    return lm.Layout(prefix="com.acme", interfaces_dir=interfaces, integration_dir="legacy-integration",
                     event_handler_dir="legacy-event-handlers", adapter_dir=ADAPTERS,
                     interfaces_artifact="com.acme:interfaces",
                     artifacts={"com.acme:interfaces", "com.acme:legacy-integration", "com.acme:legacy-event-handlers"})


def entry_point(root, key: str, provider_class: str, method: str, legacy_call: str) -> None:
    n = lm.entry_point_names(key)
    put(root, f"{API}/{n['api']}.java",
        f"package com.acme.integration.customer.api;\npublic interface {n['api']} {{ String {method}(String id); }}\n")
    put(root, f"{INTEGRATION}/spi/{n['gateway']}.java",
        f"package com.acme.integration.customer.spi;\npublic interface {n['gateway']} {{ String {method}(String id); }}\n")
    put(root, f"{INTEGRATION}/{n['implementation']}.java",
        f"package com.acme.integration.customer;\n\nimport com.acme.integration.customer.api.{n['api']};\n"
        f"import com.acme.integration.customer.spi.{n['gateway']};\n\n"
        f"public class {n['implementation']} implements {n['api']} {{\n  {n['gateway']} gateway;\n"
        f"  public String {method}(String id) {{ return gateway.{method}(id); }}\n}}\n")
    put(root, f"{ADAPTERS}/customer/{n['adapter']}.java",
        f"package com.acme.integration.legacy.customer;\n\nimport com.acme.customer.{provider_class};\n"
        f"import com.acme.integration.customer.spi.{n['gateway']};\n\n"
        f"/** Temporary: until customer provides {key}. */\n"
        f"public class {n['adapter']} implements {n['gateway']} {{\n  {provider_class} legacy;\n"
        f"  public String {method}(String id) {{ return {legacy_call}; }}\n}}\n")


def build(root) -> None:
    """The Module in monolith step's correct work for a cross-domain feature: the module (extract), and for customer, not
    migrated yet, two entry points, one handler, their interfaces, the legacy adapters and the event port's adapter."""
    extract(root)
    entry_point(root, KYC, "CustomerService", "getKycStatus", "legacy.getKycStatus(id)")
    entry_point(root, RECORD, "CustomerHistoryService", "recordBrokerCreated",
                "String.valueOf(legacy.recordBrokerCreated(id))")
    put(root, f"{HANDLER_API}/CustomerBrokerCreatedHandler.java",
        "package com.acme.eventhandler.customer.api;\npublic interface CustomerBrokerCreatedHandler { void handle(String id); }\n")
    put(root, f"{HANDLERS_DIR}/CustomerBrokerCreatedHandlerImpl.java", HANDLER_IMPL)
    for module in ("legacy-integration", "legacy-event-handlers"):
        put(root, f"{module}/pom.xml", f"""<project>
  <parent><groupId>com.acme</groupId><artifactId>monolith</artifactId><version>1.0</version></parent>
  <artifactId>{module}</artifactId>
  <dependencies>
    <dependency><groupId>com.acme</groupId><artifactId>interfaces</artifactId><version>${{project.version}}</version></dependency>
  </dependencies>
</project>
""")
    put(root, f"{MOD}/src/main/java/com/acme/broker/domain/BrokerEvents.java",
        "package com.acme.broker.domain;\npublic interface BrokerEvents { void created(String id); }\n")
    put(root, f"{BRIDGE}/SpringBrokerEvents.java", EVENT_ADAPTER)
    pom = (root / "pom.xml").read_text()
    put(root, "pom.xml", pom.replace("<module>web</module>", "<module>web</module>\n    <module>legacy-integration</module>"
                                     "\n    <module>legacy-event-handlers</module>"))


def run(root, base, declared=LEGACY_DECLARED, lay=None, entry_points=ENTRY_POINTS, handlers=HANDLERS) -> list[str]:
    declaration = {**DECLARATION, "scope": "cross-domain", "legacy_modules": declared}
    return g.check(root, base, declaration, None, OWNERSHIP, "broker",
                   legacy={"layout": lay or layout(), "declared": declared, "entry_points": entry_points,
                           "handlers": handlers, "side": "domain"})


def test_names_come_from_the_keys():
    assert lm.entry_point_names(KYC) == {"api": "CustomerGetKycStatusApi", "implementation": "CustomerGetKycStatusEntryPoint",
                                         "gateway": "CustomerGetKycStatusGateway",
                                         "adapter": "CustomerGetKycStatusGatewayLegacy"}
    assert lm.handler_names(EVENT, "customer") == {"interface": "CustomerBrokerCreatedHandler",
                                                "implementation": "CustomerBrokerCreatedHandlerImpl"}
    # The owner named once; two domains handling one event, or one domain two publishers' events, never collide
    assert lm.entry_point_names("customer.customer-summary")["api"] == "CustomerSummaryApi"
    assert lm.handler_names("loan.approved", "customer") != lm.handler_names("policy.approved", "customer")
    assert lm.handler_names(EVENT, "customer") != lm.handler_names(EVENT, "audit")


def test_the_entry_points_and_the_handler_pass(repo):
    root, base = repo
    build(root)
    assert run(root, base) == []


def test_an_older_design_without_entry_points_checks_nothing_new(repo):
    root, base = repo
    extract(root)
    assert run(root, base, declared=None, entry_points=None, handlers=None) == []


def test_without_the_interfaces_module_the_step_stops(repo):
    root, base = repo
    build(root)
    problems = run(root, base, lay=layout(interfaces=""))
    assert any(isinstance(p, Blocker) and "interfaces module isn't set" in p for p in problems)


def test_what_the_design_gives_is_declared(repo):
    root, base = repo
    build(root)
    problems = run(root, base, declared={**LEGACY_DECLARED, "entry_points": [RECORD], "handlers": []})
    assert any("legacy_modules.entry_points misses customer.get-kyc-status" in p for p in problems)
    assert any("legacy_modules.handlers misses broker.broker-created handled by customer" in p for p in problems)


def test_a_legacy_adapter_calls_exactly_its_delegates(repo):
    root, base = repo
    build(root)
    entry_point(root, KYC, "CustomerService", "getKycStatus", "legacy.findCustomer(id).getStatus()")
    assert any("does not call com.acme.customer.CustomerService#getKycStatus" in p for p in run(root, base))
    entry_point(root, KYC, "CustomerService", "getKycStatus", "legacy.getKycStatus(id) + customerDao.find(id)")
    assert any("CustomerGetKycStatusGatewayLegacy.java uses customerDao" in p or "uses CustomerDao" in p
               or "customerDao" in p for p in run(root, base))


def test_the_integration_module_never_touches_legacy_code(repo):
    root, base = repo
    build(root)
    impl = f"{INTEGRATION}/CustomerGetKycStatusEntryPoint.java"
    put(root, impl, (root / impl).read_text().replace("import com.acme.integration.customer.api",
                                                      "import com.acme.customer.CustomerService;\nimport com.acme.integration.customer.api"))
    assert any("imports com.acme.customer.CustomerService: the integration module reaches the monolith only through"
               in p for p in run(root, base))


def test_an_entry_point_calls_only_its_own_gateway(repo):
    root, base = repo
    build(root)
    impl = f"{INTEGRATION}/CustomerGetKycStatusEntryPoint.java"
    put(root, impl, (root / impl).read_text().replace("CustomerGetKycStatusGateway gateway;",
                                                      "CustomerGetKycStatusGateway gateway;\n  "
                                                      "com.acme.integration.customer.spi.CustomerRecordBrokerCreatedGateway other;"))
    assert any("uses CustomerRecordBrokerCreatedGateway: an entry point calls only its own gateway" in p
               for p in run(root, base))


def test_a_handler_reaches_other_domains_only_through_entry_point_interfaces(repo):
    root, base = repo
    build(root)
    impl = f"{HANDLERS_DIR}/CustomerBrokerCreatedHandlerImpl.java"
    put(root, impl, HANDLER_IMPL.replace("import com.acme.integration.customer.api.CustomerRecordBrokerCreatedApi;",
                                         "import com.acme.integration.customer.api.CustomerRecordBrokerCreatedApi;\n"
                                         "import com.acme.customer.CustomerHistoryService;"))
    assert any("imports com.acme.customer.CustomerHistoryService: a handler reaches other domains only" in p
               for p in run(root, base))
    put(root, impl, HANDLER_IMPL.replace("import com.acme.integration.customer.api.CustomerRecordBrokerCreatedApi;",
                                         "import com.acme.integration.customer.api.CustomerRecordBrokerCreatedApi;\n"
                                         "import com.acme.integration.customer.CustomerRecordBrokerCreatedEntryPoint;"))
    assert any("imports com.acme.integration.customer.CustomerRecordBrokerCreatedEntryPoint" in p for p in run(root, base))


def test_a_handler_runs_in_a_new_transaction_and_catches_what_fails(repo):
    root, base = repo
    build(root)
    impl = f"{HANDLERS_DIR}/CustomerBrokerCreatedHandlerImpl.java"
    put(root, impl, HANDLER_IMPL.replace("PROPAGATION_REQUIRES_NEW", "PROPAGATION_REQUIRED"))
    assert any("does not run its calls in a new transaction" in p for p in run(root, base))
    put(root, impl, HANDLER_IMPL.replace("    } catch (RuntimeException e) {\n      LOG.warning(\"broker.broker-created \" + id"
                                         " + \" not recorded: \" + e);\n    }", "    } finally { }"))
    assert any("lets a failure escape" in p for p in run(root, base))


def test_the_event_port_calls_the_handler_after_the_commit(repo):
    root, base = repo
    build(root)
    put(root, f"{BRIDGE}/SpringBrokerEvents.java", "package com.acme.broker.legacybridge;\n\n"
        "import com.acme.eventhandler.customer.api.CustomerBrokerCreatedHandler;\n\n"
        "class SpringBrokerEvents implements com.acme.broker.domain.BrokerEvents {\n"
        "  CustomerBrokerCreatedHandler customerHandler;\n  public void created(String id) { customerHandler.handle(id); }\n}\n")
    assert any("calls CustomerBrokerCreatedHandler without deferring it to after the commit" in p for p in run(root, base))


def test_the_domain_module_never_imports_the_legacy_class_of_a_domain_not_migrated(repo):
    root, base = repo
    build(root)
    put(root, f"{MOD}/src/main/java/com/acme/broker/domain/Kyc.java",
        "package com.acme.broker.domain;\n\nimport com.acme.customer.CustomerService;\n\nclass Kyc { CustomerService s; }\n")
    assert any("imports com.acme.customer.CustomerService, a legacy class of a domain not migrated yet" in p
               for p in run(root, base))


def test_an_entry_point_another_feature_built_is_only_extended(repo):
    root, base = repo
    build(root)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "an earlier feature built the entry points")
    base = git(root, "rev-parse", "HEAD")
    api = f"{API}/CustomerGetKycStatusApi.java"
    put(root, api, "package com.acme.integration.customer.api;\npublic interface CustomerGetKycStatusApi {\n"
                   "  String isVerified(String id);\n}\n")
    problems = run(root, base, declared={**LEGACY_DECLARED, "entry_points": [KYC, RECORD]})
    assert any(f"{api}: lines removed or changed" in p for p in problems)


def test_the_published_interfaces_are_interfaces_and_dtos_only(repo):
    root, base = repo
    build(root)
    put(root, f"{API}/dto/KycStatus.java", "package com.acme.integration.customer.api.dto;\n\n"
        "import com.acme.customer.Customer;\n\npublic class KycStatus { Customer customer; }\n")
    assert any("KycStatus.java uses com.acme.customer.Customer: a published interface is interfaces and DTOs only" in p
               for p in run(root, base))


def test_the_layout_reads_the_interfaces_modules_coordinates(repo, monkeypatch):
    root, base = repo
    put(root, "interfaces/pom.xml", "<project>\n  <parent><groupId>com.acme</groupId><artifactId>monolith</artifactId>"
        "<version>1.0</version></parent>\n  <artifactId>acme-interfaces</artifactId>\n</project>\n")
    from app import settings_store
    import app.domain_modules as dm

    monkeypatch.setattr(dm, "prefix", lambda project: "com.acme")
    monkeypatch.setattr(settings_store, "get_project", lambda project: {
        "interfaces_module": "interfaces", "integration_module_dir": "legacy-integration",
        "event_handler_module_dir": "legacy-event-handlers"})
    lay = lm.layout("p", root, None, ADAPTERS)
    assert lay.interfaces_artifact == "com.acme:acme-interfaces"
    assert lay.artifacts == {"com.acme:acme-interfaces", "com.acme:legacy-integration", "com.acme:legacy-event-handlers"}
    assert lay.entry_point_files(KYC, "customer")["adapter"] == \
        f"{ADAPTERS}/customer/CustomerGetKycStatusGatewayLegacy.java"


def test_the_orchestrator_step_builds_only_the_handlers_of_its_events(repo):
    root, base = repo
    build(root)
    diff = g.changes(root, base)
    in_bridge = [c for c in diff if g._under(c.path, [BRIDGE])]
    declared = {"handlers": [{"event": EVENT, "domain": "customer"}]}
    assert lm.check(root, layout(), diff, [], in_bridge, BRIDGE, declared, ENTRY_POINTS, HANDLERS, "orchestrator") == []
    assert any("legacy_modules.handlers misses broker.broker-created" in p for p in
               lm.check(root, layout(), diff, [], in_bridge, BRIDGE, {"handlers": []}, ENTRY_POINTS, HANDLERS,
                        "orchestrator"))


def test_the_declaration_is_parsed():
    import pytest

    from app import contracts_store as cs

    with pytest.raises(cs.InvalidDocument, match="entry points are built by the Module in monolith step"):
        cs._legacy_modules({"entry_points": [KYC]}, "orchestrator-extraction.yaml", "orchestrator")
    with pytest.raises(cs.InvalidDocument, match="handler 1"):
        cs._legacy_modules({"handlers": [{"event": EVENT}]}, "module-extraction.yaml", "domain")
    assert cs._legacy_modules(LEGACY_DECLARED, "module-extraction.yaml", "domain") == LEGACY_DECLARED


def test_the_settings_name_relative_folders():
    import pytest

    from app import settings_store

    settings_store._check({"interfaces_module": "", "integration_module_dir": "legacy-integration",
                           "event_handler_module_dir": "modules/legacy-event-handlers"}, settings_store.PROJECT_DEFAULTS,
                          "project")
    for key, value in (("interfaces_module", "/abs/interfaces"), ("integration_module_dir", ""),
                       ("event_handler_module_dir", "../outside"), ("integration_module_dir", "{domain}-integration")):
        with pytest.raises(KeyError, match="relative folder in the monolith"):
            settings_store._check({key: value}, settings_store.PROJECT_DEFAULTS, "project")


def test_leaving_out_the_declaration_is_refused(repo):
    root, base = repo
    build(root)
    assert any("add legacy_modules" in p for p in run(root, base, declared=None))


def test_a_reused_entry_point_must_be_there_but_need_not_be_declared(repo):
    root, base = repo
    build(root)
    reused = [{**ENTRY_POINTS[0], "reused": True}, ENTRY_POINTS[1]]
    assert run(root, base, declared={**LEGACY_DECLARED, "entry_points": [RECORD]}, entry_points=reused) == []
    (root / f"{API}/CustomerGetKycStatusApi.java").unlink()  # its feature isn't merged: built as the catalogue says
    assert any("CustomerGetKycStatusApi.java doesn't exist" in p for p in
               run(root, base, declared={**LEGACY_DECLARED, "entry_points": [RECORD]}, entry_points=reused))


def test_monolith_integration_leaves_the_temporary_modules_as_they_are(repo):
    from app import release_guardrails as rg

    root, base = repo
    put(root, f"{INTEGRATION}/CustomerGetKycStatusEntryPoint.java", "package com.acme.integration.customer;\nclass X {}\n")
    assert any("is in the module legacy-integration, which this step does not change" in p
               for p in rg.integration(root, base, [MOD, "legacy-integration", "legacy-event-handlers"]))


def test_the_shared_text_is_instructions_and_the_guardrail_sections_keep_what_they_held():
    """The temporary modules' text sits after each agent's guardrail sections, which keep their own paragraphs."""
    from app.agents import defaults as d

    for key in ("linked_domain_contracts", "domain_design", "orchestrator_design", "modular_monolith",
                "monolith_orchestrator", "microservice_scaffold", "modulith_design", "integration_design"):
        assert "{{legacy_modules}}" in d.TEMPLATES[key], key
        assert "{{legacy_modules}}" not in d.GUARDRAIL_TEMPLATES.get(key, ""), key
    assert "For this step the guardrails read as follows" in d.GUARDRAIL_TEMPLATES["microservice_scaffold"]
    assert "Hard rules for this step." in d.GUARDRAIL_TEMPLATES["microservice_scaffold"]
    assert "For contracts the guardrails mean" in d.GUARDRAIL_TEMPLATES["linked_domain_contracts"]
    assert "Hard rules for this step." not in d.TEMPLATES["microservice_scaffold"]


def test_the_later_steps_are_told_where_the_names_come_from():
    from app.agents import defaults as d

    assert "`legacy_modules.entry_points` of `module-extraction.yaml`" in d.TEMPLATES["monolith_integration"]
    assert "`legacy_modules.handlers` of its extraction file" in d.TEMPLATES["monolith_integration"]
    assert "the `ports` of `orchestrator-extraction.yaml`" in d.TEMPLATES["orchestrator_service"]
