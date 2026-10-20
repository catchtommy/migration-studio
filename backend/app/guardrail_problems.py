"""Problems a guardrail check reports that the agent can't fix by changing its work: they need the workflow's settings
(a step not set to change code, or set to the wrong repository), an earlier step's documents, the environment (git),
or a person's decision.

They are still plain strings (so every report and list keeps working), but marked: a step fails at once on them with
what to change, before an AI agent starts when they're known then (agents.common.preflight), and the agent is never
sent back to fix them (agents.common.verify), so it doesn't loop on a check it can't pass.
"""


class Blocker(str):
    """A problem that needs the workflow, an earlier step, the environment or a person."""


def blocker(text: str) -> Blocker:
    return Blocker(text)


def blockers(problems: list[str]) -> list[str]:
    return [p for p in problems if isinstance(p, Blocker)]
